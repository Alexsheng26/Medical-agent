"""Data-to-journal fit assessment (Clause 4)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from . import journal as journal_mod
from . import prompts
from .config import Config
from .llm import LLM
from .schemas import FitAssessment
from .store import Store

# Tabular inputs get summarised rather than dumped: a 5,000-row expression
# matrix tells the model nothing extra past the first few dozen rows, and the
# researcher's own description of the experiment carries far more signal.
MAX_TABLE_ROWS = 40


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


def assess(
    cfg: Config,
    store: Store,
    llm: LLM,
    journal: str,
    data_paths: list[Path],
    *,
    notes: str = "",
) -> FitAssessment:
    """Score the researcher's data against a journal's bar."""
    profile = journal_mod.profile_text(store, journal)

    blocks = [load_data_description(path) for path in data_paths]
    if notes:
        blocks.insert(0, f"RESEARCHER'S NOTES:\n{notes}")
    if not blocks:
        raise ValueError("No data supplied to assess.")

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "evaluate", journal=journal, profile=profile, data="\n\n===\n\n".join(blocks)
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


def format_assessment(assessment: FitAssessment) -> str:
    """Render an assessment for the terminal."""
    scores = [d.score for d in assessment.dimensions] or [0]
    mean = sum(scores) / len(scores)

    lines = [
        f"Fit assessment — {assessment.target_journal}",
        f"Mean score {mean:.1f}/5   Verdict: {assessment.overall_verdict}",
        "",
    ]
    for dimension in assessment.dimensions:
        bar = "█" * dimension.score + "·" * (5 - dimension.score)
        lines.append(f"  {dimension.dimension:<20} {bar} {dimension.score}/5")
        lines.append(f"    {dimension.justification}")
        for gap in dimension.gaps:
            lines.append(f"    gap: {gap}")
        lines.append("")

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


def assessment_to_json(assessment: FitAssessment) -> str:
    return json.dumps(assessment.model_dump(), ensure_ascii=False, indent=2)
