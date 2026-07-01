# 01 — Reporting Flow & Audit Event Catalogue

## 1. AS-IS flow (today, done by hand)

```
Analyst reads guidelines PDF
        |
        v
Analyst eyeballs holdings snapshot (CSV/Excel)
        |
        v
Analyst types formulas into a working spreadsheet  <-- numbers born here, untracked
        |
        v
Analyst copies results into the report template
        |
        v
Reviewer spot-checks a few cells
        |
        v
Report issued
```

Failure modes an examiner cares about: a number's origin is "a formula three
tabs deep"; a manual edit leaves no trace; switching to another firm's house
method means rebuilding the sheet; nothing proves a figure wasn't quietly
changed.

## 2. TO-BE flow (this system)

`[A]` = autonomous (deterministic, no human, no LLM)  
`[H]` = human gate  
`[N]` = LLM, narrative only, firewalled

```
[A] Parse guidelines PDF -> section-scoped chunks (stable chunk_ids)
        |
[A] Propose rule extraction (limits, caps, thresholds, owners, retention)
        |
[H] GATE 1 — Human verifies the extracted rule graph  ───────────┐
        |  auto-pass vs review criteria in §4                     │ rejected ->
        v                                                          │ correct & re-gate
[A] Build knowledge graph: rules (verified) + holdings (snapshot) │
        |  every node/edge carries provenance                     │
[A] GATE 2 — Structural ingestion check (anchors bind, NAV sane)  │
        |                                                          │
[A] Compute figures by traversing the graph (pure Python)         │
        |  each figure: value + graph_path + citation             │
[A] GATE 3 — Traceability gate: untraceable figure => ERROR       │
        |                                                          │
[A] Reconcile to answer key  +  [A] write report.xlsx             │
        |                                                          │
[N] Generate narrative commentary (optional LLM)                  │
        |                                                          │
[A] GATE 4 — Firewall: reject narrative if it contains any number │
        |             not in the computed output                  │
        v                                                          │
[A] Append-only audit log written at every step  <───────────────┘
        |
    Report issued (+ replayable audit trail)
```

## 3. The LLM boundary (heart of constraint 3)

| May be produced by the LLM | Must be produced deterministically |
|---|---|
| Prose commentary: which limits are breached, narrative tone, phrasing of remediation language | **Every number**: allocations, utilisations, aggregates, concentrations, ratios, duration, DV01 |
| Re-wording of status descriptions already computed | Every **status** flag (OK / AT LIMIT / BREACH) |
| | Every **limit**, **graph_path**, **citation**, **delta** |

The boundary is **structural, not instructional**. The computation layer
(`engine/methods.py`, `engine/compute.py`) imports no LLM client and has no
network path to one. The LLM is reachable only from `engine/narrative.py`, which
receives already-computed figures and whose output is passed through the
numeric **firewall** before it can enter a report. A number the LLM invents is
mechanically detected and the LLM text is discarded.

## 4. Gate criteria (auto-pass vs human review)

| Gate | Auto-pass criterion | Else -> human review |
|---|---|---|
| **G1 Rule extraction** | every proposed rule's `anchor` binds to a source chunk AND `extraction_confidence == 1.0` AND limit values within sane bounds (0–100% etc.) | any anchor fails to bind, confidence < 1.0, or a value out of range |
| **G2 Ingestion** | all rule anchors resolve to real chunks; NAV > 0; every position maps to a known asset class | any unresolved anchor / orphan position |
| **G3 Traceability** | figure has a non-empty `graph_path` and a citation with a `chunk_id` | figure returned as `ERROR`, not emitted |
| **G4 Firewall** | narrative numeric tokens ⊆ computed numeric tokens | LLM narrative rejected; deterministic narrative used |

In this submission the extraction at G1 is already human-verified and shipped as
`config/rules_meridian.yaml`; the engine re-binds each rule to the live PDF at
run time, so a tampered value or anchor still fails G1/G2.

## 5. Audit event catalogue

All events are written to a persistent, append-only store
(`engine/audit.py`; SQLite with `BEFORE UPDATE`/`BEFORE DELETE` triggers that
`RAISE(ABORT)`, plus a sha256 hash chain). Retention follows the guidelines
§5.1 (7 years transaction data, 10 years investor-facing output).

| Event | Trigger | Data captured | Retention |
|---|---|---|---|
| `run_started` | CLI invocation | firm, flags (llm, both) | 7y |
| `graph_construction` | graph built | node/edge counts, NAV, source-chunk count | 7y |
| `configuration_change` | a firm config is loaded | firm, utilisation_format, list of figure ids/methods | 7y |
| `figure_computation` | each figure computed | figure id, value, status, utilisation, graph_path, citation, contributing positions | 7y |
| `reconciliation` | output compared to answer key | per-figure pass/fail summary, traceability summary, firewall result | 7y |
| `narrative_firewall` | narrative generated | narrative source (deterministic/llm), firewall pass, violations | 7y |
| `export` | report.xlsx written | output filename, firm | 10y |

Every row also stores `prev_hash` and `row_hash`; `verify_chain()` recomputes
the chain so any retroactive edit is detectable even if the triggers were
bypassed at the storage layer.
