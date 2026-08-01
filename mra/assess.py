"""Data-to-journal fit assessment and journal recommendation (Clause 4).

Two entry points for one question, split by whether the researcher has already
chosen a target:

- `assess()` — a target is named. Score against that journal's stored profile.
- `recommend()` — no target. Score against the field and rank journals that fit.

Both ground on retrieval. Novelty is the one dimension a model cannot judge from
its own memory without drifting: it has to be measured against what is actually
in this researcher's corpus, so that "this has already been shown" can carry a
[PMID:x] the researcher can open.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from . import journal as journal_mod
from . import prompts, retrieval
from .config import Config
from .llm import LLM
from .schemas import FitAssessment, JournalRecommendation
from .store import Store

# Tabular inputs get summarised rather than dumped: a 5,000-row expression
# matrix tells the model nothing extra past the first few dozen rows, and the
# researcher's own description of the experiment carries far more signal.
MAX_TABLE_ROWS = 40

# How many papers to retrieve as the novelty baseline.
RETRIEVAL_K = 12

# Per data file, how much text may feed the retrieval query.
QUERY_CHARS_PER_BLOCK = 1200

_TABLE_BODY_RE = re.compile(r"\nFIRST \d+ ROWS:\n")


def load_data_description(path: Path) -> str:
    """Read the researcher's data file into something describable.

    Plain text and Markdown pass through. CSV/TSV get a header plus a sample of
    rows and a row count. Anything else is read as text with replacement.
    """
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")

    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="replace")

    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return f"({path.name} is empty)"
        header, body = rows[0], rows[1:]
        preview = "\n".join(delimiter.join(r) for r in body[:MAX_TABLE_ROWS])
        return (
            f"FILE: {path.name}\n"
            f"COLUMNS ({len(header)}): {delimiter.join(header)}\n"
            f"ROWS: {len(body)}\n"
            f"FIRST {min(MAX_TABLE_ROWS, len(body))} ROWS:\n{preview}"
        )

    return f"FILE: {path.name}\n\n{raw}"


def retrieval_query(store: Store, blocks: list[str], notes: str = "") -> str:
    """Build the query that pulls the novelty baseline out of the knowledge base.

    Ordered by how much signal each source carries: the researcher's own notes
    first, then the current hypothesis if there is one, then the data files.
    """
    parts = [notes]

    found = store.latest_hypothesis()
    if found:
        _, hypothesis = found
        parts.append(hypothesis.get("title", ""))
        parts.append(hypothesis.get("knowledge_gap", ""))
        parts.extend(hypothesis.get("mechanism_chain", [])[:3])

    parts.extend(_query_head(block) for block in blocks)
    return " ".join(part for part in parts if part)


def _query_head(block: str) -> str:
    """Keep the filename and column names; drop the table body.

    Every token becomes an OR term in the FTS query, so 500 rows of gene IDs
    would dilute the ranking with terms that appear in no abstract.
    """
    return _TABLE_BODY_RE.split(block, maxsplit=1)[0][:QUERY_CHARS_PER_BLOCK]


def _data_blocks(data_paths: list[Path], notes: str) -> list[str]:
    blocks = [load_data_description(path) for path in data_paths]
    if not blocks:
        raise ValueError("No data supplied to assess.")
    if notes:
        blocks.insert(0, f"RESEARCHER'S NOTES:\n{notes}")
    return blocks


def assess(
    cfg: Config,
    store: Store,
    llm: LLM,
    journal: str,
    data_paths: list[Path],
    *,
    notes: str = "",
) -> FitAssessment:
    """Score the researcher's data against a named journal's bar."""
    profile = journal_mod.profile_text(store, journal)
    blocks = _data_blocks(data_paths, notes)
    context, _ = retrieval.build_context(
        store, retrieval_query(store, blocks, notes), k=RETRIEVAL_K
    )

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "evaluate",
            journal=journal,
            profile=profile,
            context=context,
            data="\n\n===\n\n".join(blocks),
        ),
    ]
    assessment = llm.parse(
        system,
        [{"role": "user", "content": f"Assess this work for {journal}."}],
        FitAssessment,
        effort=cfg.effort,
        max_tokens=16000,
        cache_upto=0,
    )

    for dimension in assessment.dimensions:
        dimension.score = max(1, min(5, dimension.score))
    return assessment


def recommend(
    cfg: Config,
    store: Store,
    llm: LLM,
    data_paths: list[Path],
    *,
    notes: str = "",
) -> JournalRecommendation:
    """Score the data against the field and rank journals that would take it.

    No stored profile is required — this runs before a target exists, which is
    the point. The journals it names are what `mra journal add` should be
    pointed at next.
    """
    blocks = _data_blocks(data_paths, notes)
    context, _ = retrieval.build_context(
        store, retrieval_query(store, blocks, notes), k=RETRIEVAL_K
    )

    system = [
        prompts.core(cfg.chat_language),
        prompts.load("recommend", context=context, data="\n\n===\n\n".join(blocks)),
    ]
    recommendation = llm.parse(
        system,
        [{"role": "user", "content": "Where should this work go? Rank the targets."}],
        JournalRecommendation,
        effort=cfg.effort,
        max_tokens=16000,
        cache_upto=0,
    )

    for dimension in recommendation.dimensions:
        dimension.score = max(1, min(5, dimension.score))
    return recommendation


def format_assessment(assessment: FitAssessment) -> str:
    """Render an assessment for the terminal."""
    scores = [d.score for d in assessment.dimensions] or [0]
    mean = sum(scores) / len(scores)

    lines = [
        f"Fit assessment — {assessment.target_journal}",
        f"Mean score {mean:.1f}/5   Verdict: {assessment.overall_verdict}",
        "",
    ]
    lines += _dimension_lines(assessment.dimensions)

    lines.append(f"Strongest defensible claim:\n  {assessment.strongest_claim}\n")

    if assessment.overclaims:
        lines.append("Claims the data will not support under review:")
        lines += [f"  ✗ {item}" for item in assessment.overclaims]
        lines.append("")

    lines.append("Suggested experiments (best value first):")
    lines += [f"  {i}. {exp}" for i, exp in enumerate(assessment.suggested_experiments, 1)]

    if assessment.alternative_journals:
        lines += ["", "Better-matched targets as it stands:"]
        lines += [f"  · {j}" for j in assessment.alternative_journals]

    return "\n".join(lines)


TIER_MARK = {"realistic": "◆ 现实", "reach": "▲ 冲刺", "safe": "● 稳妥"}


def format_recommendation(recommendation: JournalRecommendation, store: Store | None = None) -> str:
    """Render a recommendation for the terminal.

    When a store is passed, each candidate is marked with whether a style
    profile already exists for it — that is the next command the researcher has
    to run before anything can be drafted, and it is easy to forget.
    """
    scores = [d.score for d in recommendation.dimensions] or [0]
    mean = sum(scores) / len(scores)

    lines = [
        "Journal recommendation — no target chosen yet",
        f"Mean score {mean:.1f}/5 against the field "
        "(3 = solid specialist journal, 5 = top general journal)",
        "",
    ]
    lines += _dimension_lines(recommendation.dimensions)

    lines.append(f"Strongest defensible claim:\n  {recommendation.strongest_claim}\n")

    if recommendation.overclaims:
        lines.append("Claims the data will not support under review:")
        lines += [f"  ✗ {item}" for item in recommendation.overclaims]
        lines.append("")

    lines.append("Candidate journals, best match first:")
    missing_profiles = []
    for index, candidate in enumerate(recommendation.candidates, 1):
        tier = TIER_MARK.get(candidate.tier.lower(), candidate.tier)
        mark = ""
        if store is not None:
            if store.get_journal(candidate.journal) is None:
                mark = "  [no style profile yet]"
                missing_profiles.append(candidate.journal)
            else:
                mark = "  [profile on file]"
        lines.append(f"\n  {index}. {candidate.journal}   {tier}{mark}")
        lines.append(f"     {candidate.why}")
        lines.append(f"     odds: {candidate.odds}")
        for gap in candidate.gaps:
            lines.append(f"     gap: {gap}")

    lines.append("")
    lines.append("Suggested experiments (best value first):")
    lines += [f"  {i}. {exp}" for i, exp in enumerate(recommendation.suggested_experiments, 1)]

    lines += ["", f"Reality check:\n  {recommendation.reality_check}"]

    if missing_profiles:
        lines += [
            "",
            "Before drafting for any of these, build its style profile:",
            f'  mra journal add "{missing_profiles[0]}"',
        ]

    return "\n".join(lines)


def _dimension_lines(dimensions) -> list[str]:
    lines = []
    for dimension in dimensions:
        bar = "█" * dimension.score + "·" * (5 - dimension.score)
        lines.append(f"  {dimension.dimension:<20} {bar} {dimension.score}/5")
        lines.append(f"    {dimension.justification}")
        for gap in dimension.gaps:
            lines.append(f"    gap: {gap}")
        lines.append("")
    return lines


def assessment_to_json(assessment: FitAssessment | JournalRecommendation) -> str:
    return json.dumps(assessment.model_dump(), ensure_ascii=False, indent=2)
