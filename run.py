"""
run.py -- single-command entrypoint.

    python run.py --firm a            # produce Firm A report + checks
    python run.py --firm b            # produce Firm B report (config only)
    python run.py --firm a --llm      # allow LLM narrative (needs ANTHROPIC_API_KEY)
    python run.py --both              # run both firms

For each run it: builds the graph, computes figures by traversing it, writes the
xlsx report, reconciles to the answer key, checks traceability, generates a
firewalled narrative, and records every step to the append-only audit log. A
timestamp-free figures JSON is written for the reproducibility diff.
"""

from __future__ import annotations

import argparse
import json
import os

import yaml


def _load_dotenv(path: str) -> None:
    """Minimal, dependency-free .env loader.

    Reads KEY=VALUE lines from ``path`` and sets them in os.environ *only* if
    they are not already set (a real shell export always wins). Supports
    ``export KEY=...``, ``#`` comments, and single/double quoted values. Kept
    tiny on purpose -- production would use python-dotenv + a secrets manager
    (see RFC).
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

from engine import graph as G
from engine.audit import AuditLog
from engine.compute import compute_figures, figures_to_records, load_config
from engine.narrative import maybe_llm_narrative
from engine.provenance import parse_pdf
from engine.reconcile import (check_traceability, firewall, load_answer_key,
                              reconcile, summarise)
from engine.report import write_report

ROOT = os.path.dirname(os.path.abspath(__file__))
_load_dotenv(os.path.join(ROOT, ".env"))  # opt-in LLM key; no-op if absent
DOCS = os.path.join(ROOT, "sample_docs")
RULES = os.path.join(ROOT, "config", "rules_meridian.yaml")
OUT = os.environ.get("OUTPUT_DIR", os.path.join(ROOT, "output"))

FIRMS = {
    "a": {"config": "firm_a.yaml", "answer_key": "firm_A_answer_key.xlsx"},
    "b": {"config": "firm_b.yaml", "answer_key": None},  # B differs by config only
}


def _print_table(title, rows, cols):
    print(f"\n{title}")
    widths = [max(len(str(r.get(c, ""))) for r in rows + [{c: c}]) for c in cols]
    line = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print("  " + line)
    print("  " + "-" * len(line))
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


def run_firm(firm_key: str, use_llm: bool, audit: AuditLog) -> dict:
    spec = FIRMS[firm_key]
    rules = yaml.safe_load(open(RULES, encoding="utf-8"))

    # --- ingest -> graph ---
    index = parse_pdf(os.path.join(DOCS, "sample_fund_guidelines.pdf"),
                      "sample_fund_guidelines.pdf")
    holdings = G.load_holdings(os.path.join(DOCS, "sample_holdings.csv"))
    g = G.build_graph(rules, index, holdings)
    audit.record("graph_construction", "build_graph",
                 {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
                  "nav": G.nav(g), "source_chunks": len(index.chunks)})

    # --- compute figures by graph traversal ---
    cfg = load_config(os.path.join(ROOT, "config", spec["config"]))
    figures = compute_figures(g, rules, cfg, audit)

    # --- write report ---
    os.makedirs(OUT, exist_ok=True)
    out_xlsx = os.path.join(OUT, f"report_firm_{firm_key}.xlsx")
    write_report(os.path.join(DOCS, "report_template.xlsx"), out_xlsx, figures)
    audit.record("export", "write_report", {"path": os.path.basename(out_xlsx),
                                             "firm": cfg["firm"]})

    # --- figures JSON (timestamp-free, for reproducibility diff) ---
    records = figures_to_records(figures)
    out_json = os.path.join(OUT, f"figures_firm_{firm_key}.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False, sort_keys=True)

    # --- reconcile / traceability / firewall ---
    trace = check_traceability(figures)
    if spec["answer_key"]:
        key = load_answer_key(os.path.join(DOCS, spec["answer_key"]))
        recon = reconcile(figures, key)
    else:
        recon = [{"figure": f.label, "pass": True, "computed": f.value,
                  "expected": "(no answer key; config-only firm)", "delta": None}
                 for f in figures]
    narrative, nmeta = maybe_llm_narrative(figures, cfg["firm"], use_llm)
    fw = firewall(narrative, figures)
    audit.record("reconciliation", "reconcile",
                 {"summary": summarise(recon, trace, fw)})
    audit.record("narrative_firewall", nmeta["source"],
                 {"firewall_pass": fw["pass"], "violations": fw["violations"]})

    summary = summarise(recon, trace, fw)

    # --- console output ---
    print("\n" + "=" * 78)
    print(f" {cfg['firm']}  (config: {cfg['_source']}, "
          f"utilisation: {cfg['global']['utilization_format']})")
    print("=" * 78)
    _print_table("FIGURES", [{
        "Metric": f.label, "Value": f.value, "Limit": f.limit,
        "Utilization": f.utilization_display, "Status": f.status,
    } for f in figures], ["Metric", "Value", "Limit", "Utilization", "Status"])

    if spec["answer_key"]:
        _print_table("RECONCILIATION vs answer key", [{
            "Metric": r["figure"], "Computed": r["computed"],
            "Expected": r["expected"], "Delta": r["delta"],
            "Pass": "PASS" if r["pass"] else "FAIL",
        } for r in recon], ["Metric", "Computed", "Expected", "Delta", "Pass"])

    print(f"\nTraceability : {summary['traceability']['passed']}/"
          f"{summary['traceability']['total']} figures resolve "
          f"figure -> graph path -> source")
    print(f"Reconciliation: {summary['reconciliation']['passed']}/"
          f"{summary['reconciliation']['total']} match answer key"
          + (f"  FAILED: {summary['reconciliation']['failed']}"
             if summary['reconciliation']['failed'] else ""))
    print(f"Firewall      : narrative numbers all present in computed output = "
          f"{fw['pass']}"
          + (f"  violations: {fw['violations']}" if fw["violations"] else ""))
    print(f"Narrative ({nmeta['source']}): {narrative}")
    print(f"\nWrote: {os.path.relpath(out_xlsx, ROOT)} , "
          f"{os.path.relpath(out_json, ROOT)}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Audit-grade fund compliance reporter")
    ap.add_argument("--firm", choices=["a", "b"], help="which firm to run")
    ap.add_argument("--both", action="store_true", help="run both firms")
    ap.add_argument("--llm", action="store_true",
                    help="allow LLM narrative (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    audit = AuditLog(os.path.join(OUT, "audit_log.sqlite"))
    audit.record("run_started", "cli",
                 {"firm": args.firm, "both": args.both, "llm": args.llm})

    firms = ["a", "b"] if args.both or not args.firm else [args.firm]
    all_summaries = {f: run_firm(f, args.llm, audit) for f in firms}

    print("\n" + "=" * 78)
    print(f" Audit log: {audit.all().__len__()} immutable events, "
          f"hash chain intact = {audit.verify_chain()}")
    print("=" * 78)
    audit.close()

    ok = all(s["all_pass"] for s in all_summaries.values()
             if "all_pass" in s) and all(
        s["traceability"]["passed"] == s["traceability"]["total"]
        for s in all_summaries.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
