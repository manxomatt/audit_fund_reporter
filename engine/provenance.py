"""
provenance.py
=============
Deterministic ingestion of the guidelines PDF into *source chunks*, each
carrying provenance (document, page, chunk_id, section, passage text).

Why this module exists
----------------------
Constraint 2 (traceability) requires that every reported figure resolve to the
exact source passage it came from. That is only credible if the citation points
at *real* parsed text, located in the actual PDF at run time -- not at a
hand-typed string. So we:

  1. Parse the PDF page by page (deterministic; pdfplumber).
  2. Split each page into section-scoped chunks (a chunk = the lines belonging
     to one numbered section on one page). The section context carries across
     page breaks, because tables in this document span pages.
  3. Assign every chunk a stable, reproducible ``chunk_id`` = "chunk_" + the
     first 8 hex chars of sha1(normalised text). The same PDF always yields the
     same chunk_ids -> supports reproducibility (constraint 1).

Anchor binding
--------------
A rule in ``rules_meridian.yaml`` declares an ``anchor`` (a short, distinctive
fragment of the source sentence) and an expected ``page``. ``bind_anchor`` finds
the chunk whose whitespace-normalised text contains that anchor and returns its
citation. If the anchor cannot be located, that is an *error* (the human
extraction gate has failed) -- never a silent guess.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

import pdfplumber


# A line is treated as a section heading if it starts like "2." or "3.1 ".
_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)?)[.\s]")


def _normalise(text: str) -> str:
    """Collapse all runs of whitespace to single spaces; strip ends.

    Used everywhere we compare against the document so that anchors which span
    wrapped lines in the PDF still match.
    """
    return re.sub(r"\s+", " ", text).strip()


def _chunk_id(page: int, section: str, text: str) -> str:
    raw = f"{page}|{section}|{_normalise(text)}".encode("utf-8")
    return "chunk_" + hashlib.sha1(raw).hexdigest()[:8]


@dataclass(frozen=True)
class Chunk:
    """One section-scoped passage of the source document."""

    doc: str
    page: int
    section: str
    chunk_id: str
    text: str            # normalised passage text

    @property
    def normalised(self) -> str:
        return _normalise(self.text)


@dataclass(frozen=True)
class Citation:
    """A resolved pointer from a figure/rule back to the source."""

    source_doc: str
    page: int
    chunk_id: str
    section: str
    passage_summary: str

    def as_dict(self) -> dict:
        return {
            "source_doc": self.source_doc,
            "page": self.page,
            "chunk_id": self.chunk_id,
            "section": self.section,
            "passage_summary": self.passage_summary,
        }

    def compact(self) -> str:
        """One-line form used in the report's Source column."""
        return f"{self.source_doc} p.{self.page} #{self.chunk_id}"


@dataclass
class SourceIndex:
    """All chunks parsed from a document, with anchor-binding helpers."""

    doc: str
    chunks: list[Chunk] = field(default_factory=list)
    # extraction_confidence is recorded per chunk for the graph provenance.
    confidence: float = 1.0

    def bind_anchor(self, anchor: str, expected_page: Optional[int] = None,
                    summary: str = "") -> Citation:
        """Locate ``anchor`` in the parsed chunks and return its Citation.

        Raises ValueError if the anchor cannot be found (extraction-gate fail).
        """
        needle = _normalise(anchor)
        candidates = self.chunks
        if expected_page is not None:
            candidates = [c for c in self.chunks if c.page == expected_page] or self.chunks
        for chunk in candidates:
            if needle in chunk.normalised:
                return Citation(
                    source_doc=self.doc,
                    page=chunk.page,
                    chunk_id=chunk.chunk_id,
                    section=chunk.section,
                    passage_summary=summary or _passage_summary(chunk.normalised, needle),
                )
        raise ValueError(
            f"Anchor not found in {self.doc} (extraction gate failed): {anchor!r}"
        )


def _passage_summary(chunk_text: str, needle: str, width: int = 140) -> str:
    """Return a short window of the chunk around the anchor for human eyes."""
    idx = chunk_text.find(needle)
    start = max(0, idx - 20)
    end = min(len(chunk_text), idx + len(needle) + 60)
    snippet = chunk_text[start:end].strip()
    return (snippet[:width] + "...") if len(snippet) > width else snippet


def parse_pdf(path: str, doc_name: str) -> SourceIndex:
    """Parse a PDF into section-scoped chunks.

    Deterministic: identical bytes in -> identical chunks (and chunk_ids) out.
    """
    chunks: list[Chunk] = []
    current_section = "preamble"
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            # Group consecutive lines under the section heading in force.
            buckets: list[tuple[str, list[str]]] = []
            for raw_line in text.split("\n"):
                line = raw_line.rstrip()
                if not line:
                    continue
                m = _HEADING_RE.match(line.strip())
                if m:
                    current_section = line.strip()
                # Append to the bucket for the current section (create if new).
                if buckets and buckets[-1][0] == current_section:
                    buckets[-1][1].append(line)
                else:
                    buckets.append((current_section, [line]))
            for section, lines in buckets:
                body = " ".join(lines)
                chunks.append(
                    Chunk(
                        doc=doc_name,
                        page=page_no,
                        section=section,
                        chunk_id=_chunk_id(page_no, section, body),
                        text=body,
                    )
                )
    return SourceIndex(doc=doc_name, chunks=chunks)
