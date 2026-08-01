"""The sync brief.

A knowledge base that quietly grows is worth nothing. The point of running
unattended is to answer one question on Monday morning: *is there anything here
I need to know about?*

The answer that matters most is when something has arrived that makes the
researcher's current hypothesis harder to defend, so that is what goes first.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import prompts
from .config import Config
from .llm import LLM
from .schemas import BriefImpact
from .store import Store

log = logging.getLogger(__name__)

# Order the brief by how much the researcher needs to see it, not alphabetically.
STANCE_ORDER = {"weakens": 0, "reframes": 1, "supports": 2, "unrelated": 3}
STANCE_LABEL = {
    "weakens": "⚠ 削弱假说",
    "reframes": "↻ 改变问题",
    "supports": "✓ 支持",
    "unrelated": "· 无关",
}

# Judging impact costs one model call for the whole batch. Beyond this many
# papers the prompt gets unwieldy and the judgements get shallow.
MAX_JUDGED = 25


def write_brief(
    cfg: Config,
    store: Store,
    llm: LLM | None,
    new_ids: list[str],
    *,
    watch_summary: str = "",
    errors: list[str] | None = None,
) -> Path:
    """Render the brief for one sync run and write it into the workspace."""
    cfg.ensure_workspace()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = cfg.briefs_dir / f"{today}.md"

    lines = [f"# 文献简报 · {today}", ""]

    if errors:
        lines += ["## ⚠ 本次运行有错误", ""]
        lines += [f"- {e}" for e in errors]
        lines.append("")

    if watch_summary:
        lines += ["## 检索情况", "", watch_summary, ""]

    if not new_ids:
        lines += ["本次没有新增文献。", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    impacts = _judge(cfg, store, llm, new_ids)

    if impacts:
        lines += ["## 对当前假说的影响", ""]
        for impact in impacts:
            article = store.get_article(impact.identifier)
            title = article.title if article else impact.identifier
            label = STANCE_LABEL.get(impact.stance, impact.stance)
            lines.append(f"### {label} — {title}")
            lines.append("")
            lines.append(f"{impact.reason}")
            if impact.action.strip():
                lines.append("")
                lines.append(f"**建议：** {impact.action}")
            lines.append("")
            lines.append(f"`{_marker(impact.identifier)}`")
            lines.append("")

    lines += [f"## 新增文献（{len(new_ids)} 篇）", ""]
    for identifier in _by_evidence(store, new_ids):
        lines.append(_render_entry(store, identifier))
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _judge(
    cfg: Config, store: Store, llm: LLM | None, new_ids: list[str]
) -> list[BriefImpact]:
    """Ask the model how the new papers bear on the current hypothesis.

    Skipped entirely when there is no hypothesis yet — there is nothing to judge
    against, and inventing a verdict would be worse than staying quiet.
    """
    if llm is None:
        return []
    latest = store.latest_hypothesis()
    if latest is None:
        return []

    judged = new_ids[:MAX_JUDGED]
    papers = "\n\n---\n\n".join(_render_for_judging(store, i) for i in judged)
    if not papers.strip():
        return []

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "brief_impact",
            hypothesis=json.dumps(latest[1], ensure_ascii=False, indent=2),
            papers=papers,
        ),
    ]

    try:
        batch = llm.parse(
            system,
            [{"role": "user", "content": "Judge each paper."}],
            _ImpactBatch,
            effort=cfg.effort,
            max_tokens=12000,
            cache_upto=0,
        )
    except Exception as exc:  # noqa: BLE001 - the article list is still useful
        log.warning("Impact judgement failed — %s", exc)
        return []

    known = set(judged)
    # Drop anything the model invented an id for; the brief must not point at a
    # record that is not in the store.
    impacts = [i for i in batch.impacts if i.identifier in known]
    impacts.sort(key=lambda i: STANCE_ORDER.get(i.stance, 9))
    return impacts


def _by_evidence(store: Store, identifiers: list[str]) -> list[str]:
    """Strongest evidence first — that is the order a reader wants."""

    def strength(identifier: str) -> int:
        card = store.get_card(identifier)
        return -(card.get("evidence_strength", 0) if card else 0)

    return sorted(identifiers, key=strength)


def _render_entry(store: Store, identifier: str) -> str:
    article = store.get_article(identifier)
    if article is None:
        return f"- `{_marker(identifier)}` (记录缺失)"

    card = store.get_card(identifier)
    header = (
        f"**{article.title}**  \n"
        f"{article.journal_abbrev or article.journal} {article.year} · "
        f"`{_marker(identifier)}`"
    )
    if not card:
        return header + "  \n_(尚未提炼)_"

    parts = [header, f"证据强度 {card.get('evidence_strength', '?')}/5"]
    for finding in card.get("key_findings", [])[:3]:
        parts.append(f"- {finding}")
    limitations = card.get("limitations", [])
    if limitations:
        parts.append(f"_局限：{limitations[0]}_")
    return "  \n".join(parts)


def _render_for_judging(store: Store, identifier: str) -> str:
    article = store.get_article(identifier)
    if article is None:
        return ""
    card = store.get_card(identifier)
    lines = [f"identifier: {identifier}", f"title: {article.title}"]
    if card:
        lines.append(f"question: {card.get('scientific_question', '')}")
        for finding in card.get("key_findings", []):
            lines.append(f"finding: {finding}")
        lines.append(f"evidence_strength: {card.get('evidence_strength', '?')}/5")
        if card.get("limitations"):
            lines.append(f"limitation: {card['limitations'][0]}")
    else:
        lines.append(f"abstract: {article.abstract[:1200]}")
    return "\n".join(lines)


def _marker(identifier: str) -> str:
    if identifier.startswith("local:"):
        return f"LOCAL:{identifier.split(':', 1)[1]}"
    return f"PMID:{identifier}"


# Declared here rather than in schemas.py because it exists only to give the
# structured-output call a list-shaped root object.
from pydantic import BaseModel, Field  # noqa: E402


class _ImpactBatch(BaseModel):
    impacts: list[BriefImpact] = Field(
        description="One entry per paper supplied, using the exact identifier given"
    )
