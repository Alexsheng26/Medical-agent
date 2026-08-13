"""Figure planning (the step before anything is plotted).

Deliberately not a plotting library. A figure comes back from a supervisor for
one of four reasons — the panel does not establish what the caption says, the
plot form hides the n, a control panel is missing, or the figures do not chain
into an argument — and none of the four is a rendering problem. There are plenty
of good plotting tools; there is no tool that tells a researcher their Figure 3
argues nothing.

`JournalProfile.figure_narrative` has been extracted at profiling time since the
beginning and used nowhere. This is what it was for.
"""

from __future__ import annotations

import json
import re

from . import assess, prompts, retrieval
from . import journal as journal_mod
from .config import Config
from .llm import LLM
from .schemas import FigureSet
from .store import Store

RETRIEVAL_K = 10


def plan(
    cfg: Config,
    store: Store,
    llm: LLM,
    data_paths: list,
    *,
    journal: str = "",
    notes: str = "",
) -> FigureSet:
    """Plan the main-text figures for a body of data."""
    blocks = assess._data_blocks(data_paths, notes)
    context, _ = retrieval.build_context(
        store, assess.retrieval_query(store, blocks, notes), k=RETRIEVAL_K, cfg=cfg, llm=llm
    )

    profile = (
        journal_mod.profile_text(store, journal)
        if journal
        else "(No journal chosen yet. Use general standards for a specialist "
             "journal: 4-6 main figures, panels lettered, every quantitative "
             "panel carrying n, the test and what the error bars are.)"
    )

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "figures",
            profile=profile,
            context=context,
            data="\n\n===\n\n".join(blocks),
        ),
    ]
    figures = llm.parse(
        system,
        [{"role": "user", "content": "Plan the main-text figures."}],
        FigureSet,
        effort=cfg.effort,
        max_tokens=16000,
        cache_upto=0,
    )

    figures.figures.sort(key=lambda f: f.number)
    return figures


def unsourced_panels(figures: FigureSet, data_paths: list) -> list[str]:
    """Panels whose `source` names no column that exists in the supplied files.

    A plan that cites a column nobody has is the figure equivalent of a
    fabricated citation: it looks actionable and is not. Checked mechanically
    rather than trusted, and reported rather than silently dropped — the model
    may legitimately be pointing at data the researcher has but did not attach.
    """
    columns, filename_tokens = _known_columns(data_paths)
    if not columns:
        return []

    flagged = []
    for figure in figures.figures:
        for panel in figure.panels:
            source = panel.source.lower()
            if "not in the supplied data" in source:
                continue
            if any(column in source for column in columns):
                continue
            # Naming only the file is fine — a panel can legitimately use the
            # whole table. What is not fine is naming a column that is not there,
            # so flag only when something column-shaped is left unaccounted for.
            if _tokens(source) - filename_tokens - _STRUCTURAL_WORDS:
                flagged.append(f"Fig {figure.number}{panel.label}: {panel.source}")
    return flagged


# Words that appear in a source description without naming a column.
_STRUCTURAL_WORDS = {
    "and", "all", "the", "from", "column", "columns", "row", "rows", "per",
    "data", "file", "files", "supplied", "with", "for", "each", "not", "csv",
    "tsv", "txt", "table", "same", "both", "derived", "computed", "above",
}


def _known_columns(data_paths: list) -> tuple[set[str], set[str]]:
    columns: set[str] = set()
    filename_tokens: set[str] = set()
    for path in data_paths:
        filename_tokens |= _tokens(getattr(path, "name", str(path)))
        try:
            described = assess.load_data_description(path)
        except (FileNotFoundError, OSError):
            continue
        for line in described.splitlines():
            if line.startswith("COLUMNS"):
                header = line.split(":", 1)[1]
                columns |= {c.strip().lower() for c in re.split(r"[,\t]", header) if c.strip()}
    return columns, filename_tokens


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z0-9_]{3,}", text)}


def format_figures(figures: FigureSet) -> str:
    """Render the plan for the terminal."""
    lines = ["图表规划", "=" * 60, "", f"整体叙事：{figures.story}", ""]

    for figure in figures.figures:
        lines.append("─" * 60)
        lines.append(f"Figure {figure.number} — {figure.handle}")
        lines.append(f"  论证：{figure.argument}")
        lines.append("")
        for panel in figure.panels:
            lines.append(f"  {panel.label}. {panel.claim}")
            lines.append(f"     画什么：{panel.shows}")
            lines.append(f"     用什么图：{panel.plot_type}")
            lines.append(f"     数据来源：{panel.source}")
            for caveat in panel.caveats:
                lines.append(f"     ⚠ {caveat}")
            lines.append("")
        lines.append(f"  图注草稿：\n    {figure.caption}")
        if figure.missing:
            lines.append("  这张图还缺：")
            lines += [f"    ✗ {item}" for item in figure.missing]
        lines.append("")

    if figures.caption_overclaims:
        lines += ["═" * 60, "你会想写、但这些 panel 撑不住的图注：", ""]
        lines += [f"  ✗ {item}" for item in figures.caption_overclaims]
        lines.append("")

    if figures.better_as_table:
        lines += ["不该做成图的（做成表或写进正文）：", ""]
        lines += [f"  · {item}" for item in figures.better_as_table]
        lines.append("")

    if figures.supplementary:
        lines += ["建议放补充材料：", ""]
        lines += [f"  · {item}" for item in figures.supplementary]

    return "\n".join(lines)


def to_json(figures: FigureSet) -> str:
    return json.dumps(figures.model_dump(), ensure_ascii=False, indent=2)
