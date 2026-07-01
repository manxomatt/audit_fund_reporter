"""
graph.py
========
Phase 2 -- the knowledge graph.

Both sources are ingested into ONE directed graph (networkx):

  * the guidelines  -> AssetClass / Limit / Cap / Floor / RiskLimit / Aggregate
                       nodes (the rule web), each bound to a real source chunk;
  * the holdings    -> Position / Issuer / ParentIssuer nodes (the data).

Every node and every edge carries provenance:
    source_doc, page, chunk_id, ingested_at, extraction_confidence.

The graph is multi-hop queryable. Phase 3 computes figures by *traversing* this
graph (e.g. gather Positions -[:IN_ASSET_CLASS]-> an AssetClass -[:HAS_LIMIT]->
its Limit), never by re-reading the documents. A small set of query helpers at
the bottom make those traversals explicit and reusable.
"""

from __future__ import annotations

import csv
from typing import Iterable

import networkx as nx

from .provenance import Citation, SourceIndex

# Fixed default ingestion timestamp keeps the graph byte-reproducible. Override
# with a real clock only when an examiner wants wall-time provenance.
FROZEN_AS_OF = "2024-01-01T00:00:00Z"


def _prov(citation: Citation | None, as_of: str, confidence: float,
          source_doc: str | None = None) -> dict:
    """Build a provenance dict for a node or edge."""
    if citation is not None:
        return {
            "source_doc": citation.source_doc,
            "page": citation.page,
            "chunk_id": citation.chunk_id,
            "section": citation.section,
            "ingested_at": as_of,
            "extraction_confidence": confidence,
        }
    return {
        "source_doc": source_doc,
        "page": None,
        "chunk_id": None,
        "section": None,
        "ingested_at": as_of,
        "extraction_confidence": confidence,
    }


def load_holdings(path: str) -> list[dict]:
    """Read the holdings snapshot. Rows are returned in file order."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["market_value_sgd"] = float(r["market_value_sgd"])
        r["modified_duration"] = float(r["modified_duration"])
    return rows


def build_graph(rules: dict, index: SourceIndex, holdings: list[dict],
                as_of: str = FROZEN_AS_OF) -> nx.DiGraph:
    """Construct the knowledge graph from verified rules + holdings."""
    g = nx.DiGraph()
    conf = float(rules.get("extraction_confidence", 1.0))
    g.add_node("Fund:meridian", kind="Fund", label="Meridian Fixed Income Fund",
               provenance=_prov(None, as_of, conf, index.doc))

    # -- Allocation limits -----------------------------------------------------
    for lim in rules["asset_class_limits"]:
        ac = f"AssetClass:{lim['asset_class']}"
        cit = index.bind_anchor(lim["anchor"], lim["page"])
        g.add_node(ac, kind="AssetClass",
                   label=lim.get("display_name", lim["asset_class"]),
                   provenance=_prov(cit, as_of, conf))
        limit_node = f"Limit:{lim['asset_class']}"
        g.add_node(limit_node, kind="Limit", min_pct=lim["min_pct"],
                   max_pct=lim["max_pct"], citation=cit.as_dict(),
                   provenance=_prov(cit, as_of, conf))
        g.add_edge(ac, limit_node, rel="HAS_LIMIT",
                   provenance=_prov(cit, as_of, conf))
        g.add_edge("Fund:meridian", ac, rel="PERMITS",
                   provenance=_prov(cit, as_of, conf))

    # -- Aggregate / concentration / liquidity caps ---------------------------
    caps = rules["aggregate_caps"]
    nig = caps["non_ig_aggregate"]
    cit = index.bind_anchor(nig["anchor"], nig["page"])
    g.add_node("Aggregate:non_ig", kind="Aggregate", max_pct=nig["max_pct"],
               citation=cit.as_dict(), provenance=_prov(cit, as_of, conf))
    # By the default (guidelines) reading, HY + Structured Credit contribute.
    for member in ("High Yield Bonds", "Structured Credit"):
        ac = f"AssetClass:{member}"
        if ac in g:
            g.add_edge(ac, "Aggregate:non_ig", rel="CONTRIBUTES_TO",
                       provenance=_prov(cit, as_of, conf))

    for key, node_kind in (("single_issuer", "Cap"), ("gre_issuer", "Cap"),
                           ("liquidity_floor", "Floor")):
        spec = caps[key]
        cit = index.bind_anchor(spec["anchor"], spec["page"])
        node = f"{node_kind}:{key}"
        attrs = {"kind": node_kind, "citation": cit.as_dict(),
                 "provenance": _prov(cit, as_of, conf)}
        attrs.update({k: v for k, v in spec.items()
                      if k in ("max_pct", "min_pct")})
        g.add_node(node, **attrs)
        g.add_edge("Fund:meridian", node, rel="CONSTRAINED_BY",
                   provenance=_prov(cit, as_of, conf))

    # -- Market-risk limits ----------------------------------------------------
    for key, spec in rules["risk_limits"].items():
        cit = index.bind_anchor(spec["anchor"], spec["page"])
        node = f"RiskLimit:{key}"
        attrs = {"kind": "RiskLimit",
                 "breach_action": spec.get("breach_action"),
                 "citation": cit.as_dict(), "provenance": _prov(cit, as_of, conf)}
        attrs.update({k: v for k, v in spec.items()
                      if k in ("min_years", "max_years", "max_sgd_per_bp")})
        g.add_node(node, **attrs)
        g.add_edge("Fund:meridian", node, rel="CONSTRAINED_BY",
                   provenance=_prov(cit, as_of, conf))
        # Breach action with its owner, modelled as a traversable relationship:
        # RiskLimit -[:ON_BREACH {action}]-> Owner. Answers "if this metric is
        # breached, what happens and who is notified?" by graph traversal.
        owner = f"Owner:{spec['owner']}"
        if owner not in g:
            g.add_node(owner, kind="Owner", label=spec["owner"],
                       provenance=_prov(cit, as_of, conf))
        g.add_edge(node, owner, rel="ON_BREACH",
                   action=spec.get("breach_action"),
                   provenance=_prov(cit, as_of, conf))

    # -- General breach owner for allocation / cap / floor limits -------------
    gb = rules["general_breach"]
    cit = index.bind_anchor(gb["anchor"], gb["page"])
    gen_owner = f"Owner:{gb['owner']}"
    if gen_owner not in g:
        g.add_node(gen_owner, kind="Owner", label=gb["owner"],
                   provenance=_prov(cit, as_of, conf))
    for n, d in list(g.nodes(data=True)):
        if d["kind"] in ("Limit", "Aggregate", "Cap", "Floor"):
            g.add_edge(n, gen_owner, rel="ON_BREACH", action=gb["action"],
                       provenance=_prov(cit, as_of, conf))

    # -- Retention policy (ingested into the graph) ---------------------------
    for pol in rules.get("retention_policies", []):
        cit = index.bind_anchor(pol["anchor"], pol["page"])
        node = f"Retention:{pol['klass']}"
        g.add_node(node, kind="Retention", label=pol["klass"],
                   min_years=pol["min_years"], citation=cit.as_dict(),
                   provenance=_prov(cit, as_of, conf))
        g.add_edge("Fund:meridian", node, rel="RETAINS",
                   provenance=_prov(cit, as_of, conf))

    # -- Positions (holdings snapshot) ----------------------------------------
    hold_prov = lambda rid: {
        "source_doc": "sample_holdings.csv", "page": None,
        "chunk_id": f"row:{rid}", "section": "holdings",
        "ingested_at": as_of, "extraction_confidence": 1.0,
    }
    for row in holdings:
        rid = row["instrument_id"]
        pos = f"Position:{rid}"
        g.add_node(pos, kind="Position", instrument_id=rid,
                   instrument_name=row["instrument_name"],
                   issuer_type=row["issuer_type"],
                   credit_rating=row["credit_rating"] or None,
                   downgraded_from=row["downgraded_from"] or None,
                   market_value_sgd=row["market_value_sgd"],
                   modified_duration=row["modified_duration"],
                   provenance=hold_prov(rid))
        ac = f"AssetClass:{row['asset_class']}"
        if ac not in g:  # asset class present in data but not in limits table
            g.add_node(ac, kind="AssetClass", label=row["asset_class"],
                       provenance=hold_prov(rid))
        g.add_edge(pos, ac, rel="IN_ASSET_CLASS", provenance=hold_prov(rid))

        issuer = f"Issuer:{row['issuer_name']}"
        if issuer not in g:
            g.add_node(issuer, kind="Issuer", label=row["issuer_name"],
                       issuer_type=row["issuer_type"], provenance=hold_prov(rid))
        g.add_edge(pos, issuer, rel="ISSUED_BY", provenance=hold_prov(rid))

        if row["parent_issuer"]:
            parent = f"ParentIssuer:{row['parent_issuer']}"
            if parent not in g:
                g.add_node(parent, kind="ParentIssuer",
                           label=row["parent_issuer"], provenance=hold_prov(rid))
            g.add_edge(issuer, parent, rel="ROLLS_UP_TO", provenance=hold_prov(rid))

    return g


# --------------------------------------------------------------------------- #
# Multi-hop query helpers -- the ONLY way Phase 3 reaches into the graph.      #
# --------------------------------------------------------------------------- #

def positions(g: nx.DiGraph) -> list[str]:
    return sorted(n for n, d in g.nodes(data=True) if d["kind"] == "Position")


def asset_classes(g: nx.DiGraph) -> list[str]:
    return sorted(n for n, d in g.nodes(data=True) if d["kind"] == "AssetClass")


def positions_in_asset_class(g: nx.DiGraph, asset_class_node: str) -> list[str]:
    """Positions -[:IN_ASSET_CLASS]-> asset_class_node (sorted, deterministic)."""
    out = [u for u, v, d in g.in_edges(asset_class_node, data=True)
           if d["rel"] == "IN_ASSET_CLASS"]
    return sorted(out)


def limit_of(g: nx.DiGraph, asset_class_node: str) -> str | None:
    for _, v, d in g.out_edges(asset_class_node, data=True):
        if d["rel"] == "HAS_LIMIT":
            return v
    return None


def issuer_of(g: nx.DiGraph, position_node: str) -> str:
    for _, v, d in g.out_edges(position_node, data=True):
        if d["rel"] == "ISSUED_BY":
            return v
    raise KeyError(f"{position_node} has no issuer")


def parent_of(g: nx.DiGraph, issuer_node: str) -> str | None:
    for _, v, d in g.out_edges(issuer_node, data=True):
        if d["rel"] == "ROLLS_UP_TO":
            return v
    return None


def aggregate_members(g: nx.DiGraph) -> list[str]:
    """AssetClass nodes that -[:CONTRIBUTES_TO]-> Aggregate:non_ig."""
    out = [u for u, v, d in g.in_edges("Aggregate:non_ig", data=True)
           if d["rel"] == "CONTRIBUTES_TO"]
    return sorted(out)


def nav(g: nx.DiGraph) -> float:
    return sum(g.nodes[p]["market_value_sgd"] for p in positions(g))


def breach_response(g: nx.DiGraph, limit_node: str) -> list[dict]:
    """Multi-hop answer to "if this limit is breached, what happens and who is
    notified?" -- traverses limit_node -[:ON_BREACH]-> Owner.

    Example: breach_response(g, "RiskLimit:modified_duration")
             -> [{"action": "PM notification within 1h",
                  "owner": "Portfolio Manager"}]
    """
    out = []
    for _, owner, d in g.out_edges(limit_node, data=True):
        if d.get("rel") == "ON_BREACH":
            out.append({"action": d.get("action"),
                        "owner": g.nodes[owner]["label"]})
    return sorted(out, key=lambda x: (x["owner"] or "", x["action"] or ""))


def retention_for(g: nx.DiGraph, klass: str) -> int | None:
    """Retention (years) for a record class, read from the graph."""
    node = f"Retention:{klass}"
    return g.nodes[node]["min_years"] if node in g else None
