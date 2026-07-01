"""
compute.py
==========
Orchestrates Phase 3/4: load a firm's configuration, run each configured method
against the knowledge graph, and apply the firm's utilisation display format.

This module wires methods together but computes no numbers itself -- the figures
come entirely from engine/methods.py. It also emits the audit events for figure
computation and configuration changes.
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict

import yaml

from . import methods as M
from .audit import AuditLog
from .methods import Context, Figure


# --------------------------------------------------------------------------- #
# Configuration loading (extends + overrides + global)                        #
# --------------------------------------------------------------------------- #

def load_config(firm_config_path: str) -> dict:
    """Resolve a firm config: load its base via ``extends``, then apply the
    firm's per-figure ``overrides`` and ``global`` tweaks.

    The result is a flat, fully-resolved config the engine executes. No engine
    code differs between firms -- only this resolved data does.
    """
    with open(firm_config_path, encoding="utf-8") as fh:
        firm = yaml.safe_load(fh)

    base_dir = os.path.dirname(os.path.abspath(firm_config_path))
    base_path = os.path.join(base_dir, firm["extends"])
    with open(base_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    overrides = firm.get("overrides") or {}
    for fig in cfg["figures"]:
        ov = overrides.get(fig["id"])
        if not ov:
            continue
        for k, v in ov.items():
            if k == "params":
                fig.setdefault("params", {}).update(v)
            else:
                fig[k] = v

    if "global" in firm:
        cfg.setdefault("global", {}).update(firm["global"])

    cfg["firm"] = firm.get("firm", "Unknown")
    cfg["_source"] = os.path.basename(firm_config_path)
    return cfg


# --------------------------------------------------------------------------- #
# Utilisation formatting                                                       #
# --------------------------------------------------------------------------- #

def _format_utilisation(util_pct, fmt: str) -> str:
    if util_pct is None:
        return "n/a"
    if fmt == "truncated_bps":
        # 58.333% -> 5833 bps  (truncate toward zero on the bps value)
        return f"{math.floor(util_pct * 100)} bps"
    return f"{util_pct:.1f}%"   # percent_1dp default


# --------------------------------------------------------------------------- #
# Run                                                                          #
# --------------------------------------------------------------------------- #

def compute_figures(g, rules: dict, cfg: dict, audit: AuditLog | None = None) -> list[Figure]:
    """Execute every configured figure method and format utilisation."""
    ig_floor = rules["rating_scale"][rules["investment_grade_floor"]]
    ctx = Context(g=g, rating_scale=rules["rating_scale"], ig_floor_rank=ig_floor)
    fmt = cfg.get("global", {}).get("utilization_format", "percent_1dp")

    if audit:
        audit.record("configuration_change", f"load {cfg['_source']}",
                     {"firm": cfg["firm"], "utilization_format": fmt,
                      "figures": [f["id"] for f in cfg["figures"]]})

    out: list[Figure] = []
    for fig_cfg in cfg["figures"]:
        method = M.REGISTRY[fig_cfg["method"]]
        params = fig_cfg.get("params", {})
        for fig in method(ctx, params):
            fig.utilization_display = _format_utilisation(fig.utilization_raw, fmt)
            out.append(fig)
            if audit:
                audit.record("figure_computation", f"method {fig.method}", {
                    "figure": fig.figure, "value": fig.value,
                    "status": fig.status, "utilization": fig.utilization_display,
                    "graph_path": fig.graph_path, "citation": fig.citation,
                    "contributors": fig.contributors,
                })
    return out


def figures_to_records(figures: list[Figure]) -> list[dict]:
    """Stable, timestamp-free serialisation used for the reproducibility diff."""
    recs = []
    for f in figures:
        recs.append({
            "figure": f.figure, "section": f.section, "label": f.label,
            "value": f.value, "limit": f.limit, "status": f.status,
            "utilization": f.utilization_display, "graph_path": f.graph_path,
            "citation": f.citation,
        })
    return recs
