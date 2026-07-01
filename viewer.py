"""
viewer.py -- bonus reconciliation / replay viewer.

Given a figure, show end-to-end: its value, the graph path it was computed
along, its source citation, its delta vs the answer key, and which configuration
rule (firm + method + params) produced it.

    python viewer.py --firm a                 # list all figures
    python viewer.py --firm a "Largest GRE issuer"
    python viewer.py --firm b "Aggregate non-IG exposure"
"""

from __future__ import annotations

import argparse
import os

import yaml

from engine import graph as G
from engine.compute import compute_figures, load_config
from engine.provenance import parse_pdf
from engine.reconcile import load_answer_key, reconcile

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "sample_docs")


def _config_rule_for(cfg: dict, figure) -> dict:
    """Find the config entry whose method produced this figure."""
    for fc in cfg["figures"]:
        if fc["method"] == figure.method:
            if fc["id"] in figure.figure or figure.figure.startswith("allocation"):
                return fc
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firm", choices=["a", "b"], default="a")
    ap.add_argument("metric", nargs="?", help="exact metric label to inspect")
    args = ap.parse_args()

    rules = yaml.safe_load(open(os.path.join(ROOT, "config", "rules_meridian.yaml")))
    index = parse_pdf(os.path.join(DOCS, "sample_fund_guidelines.pdf"),
                      "sample_fund_guidelines.pdf")
    holdings = G.load_holdings(os.path.join(DOCS, "sample_holdings.csv"))
    g = G.build_graph(rules, index, holdings)
    cfg = load_config(os.path.join(ROOT, "config", f"firm_{args.firm}.yaml"))
    figures = compute_figures(g, rules, cfg)

    key = None
    if args.firm == "a":
        key = load_answer_key(os.path.join(DOCS, "firm_A_answer_key.xlsx"))
    recon = {r["figure"]: r for r in reconcile(figures, key)} if key else {}

    if not args.metric:
        print(f"Figures for {cfg['firm']} (pass a metric label to inspect one):\n")
        for f in figures:
            print(f"  - {f.label}: {f.value} ({f.status})")
        return

    fig = next((f for f in figures if f.label == args.metric), None)
    if fig is None:
        print(f"No figure labelled {args.metric!r}. Try without an argument to list.")
        return

    rule = _config_rule_for(cfg, fig)
    cit = fig.citation or {}
    print("=" * 72)
    print(f" REPLAY: {fig.label}   [{cfg['firm']}]")
    print("=" * 72)
    print(f"Value         : {fig.value}")
    print(f"Limit         : {fig.limit}")
    print(f"Utilisation   : {fig.utilization_display}")
    print(f"Status        : {fig.status}")
    print(f"\nGraph path    : {fig.graph_path}")
    print(f"Contributors  : {', '.join(fig.contributors) or '(n/a)'}")
    print(f"\nSource        : {cit.get('source_doc')} p.{cit.get('page')} "
          f"#{cit.get('chunk_id')}")
    print(f"Passage       : {cit.get('passage_summary')}")
    print(f"\nConfig rule   : method={fig.method}")
    if rule:
        print(f"                id={rule['id']}  params={rule.get('params', {})}")
    print(f"                utilisation_format="
          f"{cfg['global']['utilization_format']}")
    # Multi-hop: who is notified if this limit is breached?
    cand = {f"Limit:{fig.label}", "RiskLimit:modified_duration",
            "RiskLimit:dv01", "Aggregate:non_ig",
            f"Cap:{rule.get('params', {}).get('cap', '')}", "Floor:liquidity_floor"}
    for node in cand:
        if node in g:
            resp = G.breach_response(g, node)
            if resp:
                owners = "; ".join(f"{r['owner']} ({r['action']})" for r in resp)
                print(f"On breach     : notifies {owners}")
                break
    if fig.label in recon:
        r = recon[fig.label]
        print(f"\nReconciliation: computed={r['computed']} expected={r['expected']} "
              f"delta={r['delta']} -> {'PASS' if r['pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
