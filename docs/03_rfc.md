# 03 — RFC: Audit-Grade Fund Compliance Reporter

**Status:** implemented · **Scope:** sample materials · **Audience:** reviewers / audit examiner

This memo derives the architecture from the five hard constraints and defends
the decisions. It reads for *why*, not *what* (the *what* is in `docs/02`).

---

## Constraint 3 first — the LLM cannot be the source of any number

Every other decision bends around this, so it comes first. The requirement is
not "tell the model not to do maths"; it is that an examiner can *verify* no
figure came from the model. Instruction is unverifiable; structure is.

**Decision: physically separate the compute path from the model path.** The
computation layer (`methods.py`, `compute.py`, `graph.py`) imports no LLM client
and has no code path that could call one. Numbers are produced only by pure
Python functions traversing the graph. The model is reachable from exactly one
module, `narrative.py`, which is handed *already-computed* figures and asked for
prose.

**Decision: a numeric firewall, verified at run time.** The narrative is not
trusted. `reconcile.firewall` tokenises every number in the narrative and checks
each against the set of numbers already present in the computed figures. A token
that isn't there means the model introduced a figure → the narrative is rejected
and the deterministic, number-free narrative is used instead. The test
`test_firewall_blocks_smuggled_number` proves a planted "99.9%" is caught.

**Consequence:** the system runs fully offline by default. The LLM is opt-in
(`--llm` + `ANTHROPIC_API_KEY`); with it off, commentary is templated and still
firewalled. An examiner verifies constraint 3 by reading two facts: the compute
modules contain no model import, and the firewall rejects out-of-set numbers.

## Constraint 2 — traceability through the graph

**Decision: rules become graph nodes whose provenance is bound to the live PDF.**
`provenance.py` parses the guidelines into section-scoped chunks with stable,
content-hashed `chunk_id`s. The verified rule extraction
(`config/rules_meridian.yaml`) declares, per rule, an `anchor` — a distinctive
fragment of the source sentence. At graph-build time the engine *locates* that
anchor in the parsed chunks and stamps the resulting `Citation`
(`source_doc, page, chunk_id, section, passage`) onto the limit/cap node. If an
anchor cannot be found, ingestion fails — a hallucinated or tampered rule cannot
pass silently.

**Decision: figures are computed by traversing the graph, and carry the path.**
Each method gathers its inputs through graph edges (e.g. allocations walk
`Position -[:IN_ASSET_CLASS]-> AssetClass -[:HAS_LIMIT]-> Limit`) and emits the
`graph_path` plus the citation copied from the node it tested against. A figure
that cannot produce both is returned as `ERROR`, never emitted silently
(`check_traceability`, gate G3). So "pick a figure, follow it to its source"
resolves for all 13 figures — including multi-hop ones like GRE-at-parent.

## Constraint 5 — switch firms with no engine-code edit

**Decision: methods are parameterised and firm-agnostic; firms are data.** The
engine exposes a registry of named methods (`allocations`,
`aggregate_exposure`, `max_issuer_concentration`, …). No method contains a firm
name or an `if firm == …` branch. A firm is a YAML file that selects methods and
sets parameters. `firm_b.yaml` is `base_methods.yaml` plus three declarative
overrides that map 1:1 to `firm_B_brief.md`:

| House convention | Override |
|---|---|
| fallen angels count toward non-IG | `aggregate_non_ig.params.include_below_ig_holdings: true` |
| GRE concentration at the parent | `gre_issuer.params.group_by: parent_issuer` |
| utilisation in truncated bps | `global.utilization_format: truncated_bps` |

The reviewer's test — run A, run B, diff, with no code change — passes because
the only thing that differs between the two runs is which YAML `run.py` loads.
The `aggregate_exposure` and `max_issuer_concentration` methods were written from
the start to take `include_below_ig_holdings` and `group_by` as parameters, so
Firm B exercises existing branches rather than new code.

## Constraint 4 — reconcile to the answer key

**Decision: reconcile by metric label, compare value + status, report deltas.**
`reconcile.py` loads `firm_A_answer_key.xlsx` and matches each computed figure to
its expected row. We reproduce Firm A **exactly** (delta 0.0 on all 13 figures),
so no tolerance is required; the comparator still computes a numeric delta and
would surface any drift. Display formats were chosen to match the key precisely:
allocations `xx.x%`, duration `x.xx yrs`, DV01 `SGD x,xxx / bp`, and the Cash row
shown against its **min** floor (the guidelines' "Minimum liquidity floor"),
which is why Cash reads `min 5% / n/a / BREACH`.

## Constraint 1 — determinism

**Decision: remove every source of run-to-run variance.** Fixed ingestion
timestamp; `sorted()` on all set-derived traversals (string hash randomisation
otherwise reorders sets); `hashlib.sha1/sha256` rather than the salted built-in
`hash()`; and a timestamp-free `figures.json` as the artefact to diff. Two runs
produce byte-identical figure JSON (`test_determinism`).

## Append-only audit

**Decision: immutability enforced in the database, not by convention.**
`audit.py` is SQLite with `BEFORE UPDATE`/`BEFORE DELETE` triggers that
`RAISE(ABORT)`, so there is no code path — ours or an operator's — that can edit
or delete a row. A sha256 hash chain (`row_hash = sha256(prev_hash || payload)`)
makes any storage-level tampering detectable via `verify_chain()`. Every run is
fully replayable from the log.

## Key trade-offs / what I would add for production

- **Rule extraction is human-verified and shipped as YAML.** This honours the
  assignment's human-gate requirement and keeps numbers deterministic. In
  production the pre-gate proposal would be LLM/regex-assisted with a real
  reviewer UI; the run-time anchor re-binding (already implemented) is what makes
  the shipped artefact trustworthy and tamper-evident.
- **Failure modes covered:** an unbindable anchor fails ingestion; an untraceable
  figure is returned as `ERROR`; an out-of-set narrative number is firewalled.
- **Production hardening (not in scope):** real secrets management for the LLM
  key, authn/authz on the report endpoint, a signed/WORM audit store, and a
  reviewer UI for Gate 1. Noted, not built, per the scope notes.

## Bonus implemented

`viewer.py` — given a figure, replays its value, graph path, source passage,
config rule (firm/method/params), and delta vs the answer key. The
`extends`/`overrides` config is a small method DSL with the firm diff expressed
declaratively.
