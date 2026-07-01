"""
narrative.py
============
The ONLY place a language model may appear -- and it may write commentary only,
never numbers.

Design for constraint 3:
  * The deterministic template below produces a number-free narrative by
    construction (it names breaching metrics and their status words, but inserts
    no figures). This is what runs by default, so the system works offline with
    no API key.
  * If ANTHROPIC_API_KEY is set and ``--llm`` is passed, an LLM may rephrase the
    commentary. Whatever it returns is passed through engine.reconcile.firewall:
    if it introduced any number not already in the computed output, the LLM text
    is REJECTED and we fall back to the deterministic narrative.

Either way, the narrative that reaches the report has provably introduced no
figure of its own.
"""

from __future__ import annotations

import os

from .methods import Figure


def deterministic_narrative(figures: list[Figure], firm: str) -> str:
    """A number-free commentary built only from labels and status words."""
    breaches = [f for f in figures if f.status == "BREACH"]
    at_limit = [f for f in figures if f.status == "AT LIMIT"]
    parts = [f"Compliance commentary for the {firm} configuration of the "
             f"Meridian Fixed Income Fund."]
    if breaches:
        names = ", ".join(b.label for b in breaches)
        parts.append(f"The portfolio is in breach on the following limits: {names}. "
                     "Each requires escalation to the Risk & Compliance Committee "
                     "per the fund's breach-reporting policy.")
    else:
        parts.append("No hard allocation or risk limit is in breach.")
    if at_limit:
        names = ", ".join(a.label for a in at_limit)
        parts.append(f"The following sit exactly at their limit and warrant "
                     f"monitoring: {names}.")
    parts.append("All figures above were produced by the deterministic "
                 "computation layer and traced through the knowledge graph to "
                 "their source passages; this commentary adds no new figures.")
    return " ".join(parts)


def maybe_llm_narrative(figures: list[Figure], firm: str, use_llm: bool) -> tuple[str, dict]:
    """Return (narrative, meta). Falls back to deterministic text if the LLM is
    unavailable or its output fails the firewall."""
    from .reconcile import firewall  # local import to avoid cycle

    base = deterministic_narrative(figures, firm)
    if not use_llm or not os.environ.get("ANTHROPIC_API_KEY"):
        return base, {"source": "deterministic", "firewall": firewall(base, figures)}

    try:
        import anthropic
        client = anthropic.Anthropic()
        computed = "\n".join(f"- {f.label}: {f.value} ({f.status})" for f in figures)
        prompt = (
            "You are writing audit commentary for a fund compliance report. "
            "You may ONLY use the figures listed below; do not invent or alter "
            "any number. Write 3-4 sentences of plain commentary.\n\n"
            f"Firm: {firm}\nComputed figures:\n{computed}"
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        fw = firewall(text, figures)
        if fw["pass"]:
            return text, {"source": "llm", "firewall": fw}
        # LLM smuggled in a number -> reject, fall back.
        return base, {"source": "deterministic_fallback",
                      "rejected_llm_text": text, "firewall": firewall(base, figures),
                      "llm_firewall": fw}
    except Exception as exc:  # noqa: BLE001
        return base, {"source": "deterministic", "error": str(exc),
                      "firewall": firewall(base, figures)}
