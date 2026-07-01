"""
Tests that the five constraints hold. Run with:  python -m pytest -q
(or plain `python tests/test_determinism.py`).
"""
import json
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import graph as G                                   # noqa: E402
from engine.compute import compute_figures, figures_to_records, load_config  # noqa: E402
from engine.narrative import deterministic_narrative            # noqa: E402
from engine.provenance import parse_pdf                         # noqa: E402
from engine.reconcile import (check_traceability, firewall,     # noqa: E402
                              load_answer_key, reconcile)

DOCS = os.path.join(ROOT, "sample_docs")


def _figures(firm):
    rules = yaml.safe_load(open(os.path.join(ROOT, "config", "rules_meridian.yaml")))
    index = parse_pdf(os.path.join(DOCS, "sample_fund_guidelines.pdf"),
                      "sample_fund_guidelines.pdf")
    holdings = G.load_holdings(os.path.join(DOCS, "sample_holdings.csv"))
    g = G.build_graph(rules, index, holdings)
    cfg = load_config(os.path.join(ROOT, "config", f"firm_{firm}.yaml"))
    return compute_figures(g, rules, cfg)


def test_determinism():
    """Constraint 1: two runs -> byte-identical figure JSON."""
    a = json.dumps(figures_to_records(_figures("a")), sort_keys=True)
    b = json.dumps(figures_to_records(_figures("a")), sort_keys=True)
    assert a == b


def test_firm_a_reconciles():
    """Constraint 4: every Firm A figure matches the answer key."""
    key = load_answer_key(os.path.join(DOCS, "firm_A_answer_key.xlsx"))
    recon = reconcile(_figures("a"), key)
    assert all(r["pass"] for r in recon), [r for r in recon if not r["pass"]]


def test_firm_b_differences():
    """Constraint 5: config-only switch reproduces Firm B's distinct figures."""
    figs = {f.label: f for f in _figures("b")}
    assert figs["Aggregate non-IG exposure"].value == "21.0%"
    assert figs["Aggregate non-IG exposure"].status == "BREACH"
    assert figs["Largest GRE issuer"].value == "13.0%"
    assert figs["Largest GRE issuer"].status == "BREACH"
    assert figs["Singapore Government Securities"].utilization_display == "5833 bps"


def test_traceability():
    """Constraint 2: every figure resolves figure -> graph path -> source."""
    trace = check_traceability(_figures("a"))
    assert all(t["pass"] for t in trace), [t for t in trace if not t["pass"]]


def test_firewall_blocks_smuggled_number():
    """Constraint 3: a narrative with an invented number fails the firewall."""
    figs = _figures("a")
    clean = deterministic_narrative(figs, "Firm A")
    assert firewall(clean, figs)["pass"]
    tampered = clean + " The hidden VaR is 99.9% of NAV."
    assert not firewall(tampered, figs)["pass"]


def test_multihop_breach_owner_and_retention():
    """Phase 2: the graph answers 'if duration breaches, who is notified?' and
    carries retention -- both by traversal, not by re-reading the document."""
    rules = yaml.safe_load(open(os.path.join(ROOT, "config", "rules_meridian.yaml")))
    index = parse_pdf(os.path.join(DOCS, "sample_fund_guidelines.pdf"),
                      "sample_fund_guidelines.pdf")
    holdings = G.load_holdings(os.path.join(DOCS, "sample_holdings.csv"))
    g = G.build_graph(rules, index, holdings)
    resp = G.breach_response(g, "RiskLimit:modified_duration")
    assert resp and resp[0]["owner"] == "Portfolio Manager"
    assert "PM notification" in resp[0]["action"]
    assert G.retention_for(g, "investor_facing_reports") == 10
    assert G.retention_for(g, "transaction_data") == 7


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
