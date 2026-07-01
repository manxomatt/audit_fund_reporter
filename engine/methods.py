"""
methods.py
==========
The deterministic computation library (Phase 3 + Phase 4).

Every reported number is produced here, in pure Python, by traversing the
knowledge graph via the helpers in graph.py. No language model is reachable from
this module -- that is the structural guarantee behind constraint 3.

Each method is *firm-agnostic and parameterised*. Firm A and Firm B select and
configure these same methods from YAML; the differences between firms are
entirely in the parameters (e.g. ``group_by: parent_issuer``,
``include_below_ig_holdings: true``). No method contains a firm name or an
``if firm == ...`` branch -- which is why switching firms needs zero engine-code
edits (constraint 5).

Every method returns one or more ``Figure`` objects. A Figure that cannot be
bound to a graph path + source citation is returned with status ``ERROR``
rather than silently emitted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import networkx as nx

from . import graph as G


@dataclass
class Figure:
    figure: str
    label: str
    section: str
    value_raw: float
    value: str
    limit: str
    status: str
    utilization_raw: Optional[float]   # percent (e.g. 58.333), or None for n/a
    graph_path: str
    citation: dict
    contributors: list = field(default_factory=list)
    method: str = ""
    utilization_display: str = ""      # set by compute (global format)


@dataclass
class Context:
    """Read-only computation context shared by all methods."""

    g: nx.DiGraph
    rating_scale: dict
    ig_floor_rank: int

    @property
    def nav(self) -> float:
        return G.nav(self.g)

    def mv(self, position_node: str) -> float:
        return self.g.nodes[position_node]["market_value_sgd"]

    def rating_rank(self, rating: Optional[str]) -> Optional[int]:
        if not rating:
            return None
        return self.rating_scale.get(rating)


# --------------------------------------------------------------------------- #
# Status helpers                                                              #
# --------------------------------------------------------------------------- #

def _status_max(value: float, cap: float) -> str:
    if value > cap:
        return "BREACH"
    if math.isclose(value, cap, abs_tol=1e-9):
        return "AT LIMIT"
    return "OK"


def _status_min(value: float, floor: float) -> str:
    if value < floor:
        return "BREACH"
    if math.isclose(value, floor, abs_tol=1e-9):
        return "AT LIMIT"
    return "OK"


def _status_range(value: float, lo: float, hi: float) -> str:
    if value < lo or value > hi:
        return "BREACH"
    if math.isclose(value, lo, abs_tol=1e-9) or math.isclose(value, hi, abs_tol=1e-9):
        return "AT LIMIT"
    return "OK"


def _fmt_g(x: float) -> str:
    """Trim trailing .0 (20.0 -> '20')."""
    return f"{x:g}"


# --------------------------------------------------------------------------- #
# Methods                                                                     #
# --------------------------------------------------------------------------- #

def allocations(ctx: Context, params: dict) -> list[Figure]:
    """Allocation % per asset class vs its limit (one Figure per class)."""
    figs: list[Figure] = []
    for ac in _ordered_asset_classes_with_limits(ctx.g):
        label = ctx.g.nodes[ac]["label"]
        limit_node = G.limit_of(ctx.g, ac)
        lim = ctx.g.nodes[limit_node]
        members = G.positions_in_asset_class(ctx.g, ac)
        total = sum(ctx.mv(p) for p in members)
        pct = total / ctx.nav * 100.0
        binding = params.get("binding", {}).get(label, "max")
        path = (f"(Position:*)-[:IN_ASSET_CLASS]->({ac})-[:HAS_LIMIT]->({limit_node})")
        if binding == "min":
            status = _status_min(pct, lim["min_pct"])
            limit_str = f"min {_fmt_g(lim['min_pct'])}%"
            util = None
        else:
            status = _status_range(pct, lim["min_pct"], lim["max_pct"])
            limit_str = f"{_fmt_g(lim['min_pct'])}\u2013{_fmt_g(lim['max_pct'])}%"
            util = pct / lim["max_pct"] * 100.0   # value / max, as a percent
        figs.append(Figure(
            figure=f"allocation:{label}", label=label, section="Allocation",
            value_raw=pct, value=f"{pct:.1f}%", limit=limit_str, status=status,
            utilization_raw=util, graph_path=path, citation=lim["citation"],
            contributors=members, method="allocations",
        ))
    return figs


def aggregate_exposure(ctx: Context, params: dict) -> list[Figure]:
    """Aggregate non-IG exposure vs its cap.

    Base members come from the graph (AssetClass -[:CONTRIBUTES_TO]->
    Aggregate:non_ig). With ``include_below_ig_holdings: true`` we additionally
    pull in any holding rated below the IG floor that sits in a *non-member*
    asset class (Firm B's 'fallen angels'), de-duplicated against the base.
    """
    agg = ctx.g.nodes["Aggregate:non_ig"]
    cap = agg["max_pct"]
    member_classes = G.aggregate_members(ctx.g)
    contributors: list[str] = []
    for ac in member_classes:
        contributors.extend(G.positions_in_asset_class(ctx.g, ac))
    base = set(contributors)

    extra: list[str] = []
    if params.get("include_below_ig_holdings", False):
        for p in G.positions(ctx.g):
            if p in base:
                continue
            rank = ctx.rating_rank(ctx.g.nodes[p].get("credit_rating"))
            if rank is not None and rank < ctx.ig_floor_rank:
                extra.append(p)
    contributors = sorted(base) + sorted(extra)

    total = sum(ctx.mv(p) for p in contributors)
    pct = total / ctx.nav * 100.0
    member_str = ",".join(ac.split(":", 1)[1] for ac in member_classes)
    path = f"(AssetClass:{{{member_str}}})-[:CONTRIBUTES_TO]->(Aggregate:non_ig)"
    if extra:
        path += " + (Position rating<IG)-[:fallen_angel]->(Aggregate:non_ig)"
    return [Figure(
        figure="aggregate_non_ig", label="Aggregate non-IG exposure",
        section="Aggregate", value_raw=pct, value=f"{pct:.1f}%",
        limit=f"max {_fmt_g(cap)}%", status=_status_max(pct, cap),
        utilization_raw=pct / cap * 100.0, graph_path=path,
        citation=agg["citation"], contributors=contributors,
        method="aggregate_exposure",
    )]


def max_issuer_concentration(ctx: Context, params: dict) -> list[Figure]:
    """Largest single-issuer (or issuer-group) concentration vs a cap.

    ``issuer_types``  : which issuer types are in scope (e.g. [corporate] or [GRE]).
    ``group_by``      : 'issuer' (per legal issuer) or 'parent_issuer' (roll up
                        issuers sharing a parent into one group).
    ``cap``           : which cap node to test against ('single_issuer' / 'gre_issuer').
    """
    issuer_types = set(params["issuer_types"])
    group_by = params.get("group_by", "issuer")
    cap_node = f"Cap:{params['cap']}"
    cap = ctx.g.nodes[cap_node]["max_pct"]

    groups: dict[str, list[str]] = {}
    for p in G.positions(ctx.g):
        if ctx.g.nodes[p]["issuer_type"] not in issuer_types:
            continue
        issuer = G.issuer_of(ctx.g, p)
        if group_by == "parent_issuer":
            parent = G.parent_of(ctx.g, issuer)
            key = parent if parent else issuer
        else:
            key = issuer
        groups.setdefault(key, []).append(p)

    if not groups:
        return [_error_figure("concentration", "Largest issuer", "Concentration",
                              "no issuers in scope")]

    # Deterministic max: break ties by group key.
    ranked = sorted(
        ((sum(ctx.mv(p) for p in members), key, members)
         for key, members in groups.items()),
        key=lambda t: (-t[0], t[1]),
    )
    top_value, top_key, top_members = ranked[0]
    pct = top_value / ctx.nav * 100.0

    if group_by == "parent_issuer":
        path = ("(Position:*)-[:ISSUED_BY]->(Issuer:*)-[:ROLLS_UP_TO]->"
                f"({top_key}) vs ({cap_node})")
    else:
        path = f"(Position:*)-[:ISSUED_BY]->({top_key}) vs ({cap_node})"
    label = params["label"]

    fig_id = params.get("figure_id", "concentration")
    return [Figure(
        figure=fig_id, label=label, section="Concentration",
        value_raw=pct, value=f"{pct:.1f}%", limit=f"max {_fmt_g(cap)}%",
        status=_status_max(pct, cap), utilization_raw=pct / cap * 100.0,
        graph_path=path, citation=ctx.g.nodes[cap_node]["citation"],
        contributors=top_members, method="max_issuer_concentration",
    )]


def liquid_assets_ratio(ctx: Context, params: dict) -> list[Figure]:
    """Liquid-asset ratio vs the liquidity floor."""
    floor_node = "Floor:liquidity_floor"
    floor = ctx.g.nodes[floor_node]["min_pct"]
    liquid_classes = params["liquid_asset_classes"]
    contributors: list[str] = []
    for name in liquid_classes:
        ac = f"AssetClass:{name}"
        if ac in ctx.g:
            contributors.extend(G.positions_in_asset_class(ctx.g, ac))
    contributors = sorted(contributors)
    total = sum(ctx.mv(p) for p in contributors)
    pct = total / ctx.nav * 100.0
    path = ("(Position:*)-[:IN_ASSET_CLASS]->(AssetClass:{SGS,MAS Bills,Cash}) "
            f"vs ({floor_node})")
    return [Figure(
        figure="liquidity_ratio", label="Liquid assets ratio",
        section="Liquidity", value_raw=pct, value=f"{pct:.1f}%",
        limit=f"min {_fmt_g(floor)}%", status=_status_min(pct, floor),
        utilization_raw=pct / floor * 100.0, graph_path=path,
        citation=ctx.g.nodes[floor_node]["citation"], contributors=contributors,
        method="liquid_assets_ratio",
    )]


def weighted_duration(ctx: Context, params: dict) -> list[Figure]:
    """Market-value-weighted portfolio modified duration vs its range."""
    rl = ctx.g.nodes["RiskLimit:modified_duration"]
    positions = G.positions(ctx.g)
    weighted = sum(ctx.mv(p) * ctx.g.nodes[p]["modified_duration"] for p in positions)
    dur = weighted / ctx.nav
    path = "(Position:*)-[:weighted_by_MV]->(RiskLimit:modified_duration)"
    return [Figure(
        figure="modified_duration", label="Portfolio modified duration",
        section="Market risk", value_raw=dur, value=f"{dur:.2f} yrs",
        limit=f"{rl['min_years']:.1f}\u2013{rl['max_years']:.1f} yrs",
        status=_status_range(dur, rl["min_years"], rl["max_years"]),
        utilization_raw=None, graph_path=path, citation=rl["citation"],
        contributors=positions, method="weighted_duration",
    )]


def portfolio_dv01(ctx: Context, params: dict) -> list[Figure]:
    """Portfolio DV01 = sum(MV * modified_duration) * bump, in SGD per bp."""
    rl = ctx.g.nodes["RiskLimit:dv01"]
    cap = rl["max_sgd_per_bp"]
    bump = params.get("bump_bps", 1) * 0.0001
    positions = G.positions(ctx.g)
    dv01 = sum(ctx.mv(p) * ctx.g.nodes[p]["modified_duration"] for p in positions) * bump
    path = "(Position:*)-[:MV*duration*1bp]->(RiskLimit:dv01)"
    return [Figure(
        figure="dv01", label="Portfolio DV01", section="Market risk",
        value_raw=dv01, value=f"SGD {dv01:,.0f} / bp",
        limit=f"max {cap:,.0f}", status=_status_max(dv01, cap),
        utilization_raw=dv01 / cap * 100.0, graph_path=path,
        citation=rl["citation"], contributors=positions, method="portfolio_dv01",
    )]


# --------------------------------------------------------------------------- #
# Utilities                                                                   #
# --------------------------------------------------------------------------- #

def _ordered_asset_classes_with_limits(g: nx.DiGraph) -> list[str]:
    """Asset classes that have a HAS_LIMIT edge, in graph-insertion order."""
    out = []
    for n, d in g.nodes(data=True):
        if d["kind"] == "AssetClass" and G.limit_of(g, n) is not None:
            out.append(n)
    return out


def _error_figure(fig_id: str, label: str, section: str, reason: str) -> Figure:
    return Figure(figure=fig_id, label=label, section=section, value_raw=float("nan"),
                  value="ERROR", limit="", status="ERROR", utilization_raw=None,
                  graph_path="(untraceable)", citation={}, contributors=[],
                  method="error")


# Method registry: config selects methods by these names.
REGISTRY: dict[str, Callable[[Context, dict], list[Figure]]] = {
    "allocations": allocations,
    "aggregate_exposure": aggregate_exposure,
    "max_issuer_concentration": max_issuer_concentration,
    "liquid_assets_ratio": liquid_assets_ratio,
    "weighted_duration": weighted_duration,
    "portfolio_dv01": portfolio_dv01,
}
