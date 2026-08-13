"""Review-article writing from the stored corpus.

The one thing `digest` already produces is exactly a review's raw material: per
paper, the question it asked, what it found, how strong that evidence is, and
what it could not show. This turns that into an argued review rather than a
sequence of paper summaries.

Two stages, deliberately. The outline is written and shown first, for a few
cents, so the researcher can correct the structure before paying for several
thousand words — and so each section can be written against its own papers
instead of against the whole corpus, which is what keeps a long review from
drifting into generalities halfway through.
"""

from __future__ import annotations

import logging

from . import citations, prompts, retrieval
from .config import Config
from .llm import LLM
from .schemas import ReviewOutline
from .store import Store

log = logging.getLogger(__name__)

# A review should see the corpus, not a keyword slice of it. Sections are written
# from their own assigned papers, so a wide outline pass costs one call.
OUTLINE_K = 60

# Per section. Long enough to argue, short enough that the model does not start
# padding — and it is a target, not a limit.
WORDS_PER_SECTION = 550


def plan(cfg: Config, store: Store, llm: LLM, topic: str) -> tuple[ReviewOutline, list[str]]:
    """Plan the review. Returns the outline and the ids that were available."""
    if store.count_articles() == 0:
        raise ValueError(
            "The knowledge base is empty. Import or search for papers first — a "
            "review is assembled from what you have, not from the model's memory."
        )

    context, available = retrieval.build_context(
        store, topic, k=OUTLINE_K, cfg=cfg, llm=llm
    )
    system = [
        prompts.core(cfg.chat_language),
        prompts.load("review_outline", topic=topic, context=context),
    ]
    outline = llm.parse(
        system,
        [{"role": "user", "content": f"Plan the review on {topic}."}],
        ReviewOutline,
        effort=cfg.effort,
        max_tokens=16000,
        cache_upto=0,
    )

    # The model may name an id that was not in the context. Dropping it here is
    # what stops a section being written from a paper nobody has.
    known = set(available)
    for section in outline.sections:
        kept = [i for i in section.identifiers if i in known]
        if len(kept) != len(section.identifiers):
            log.warning(
                "Section %r referenced %d id(s) not in the knowledge base; dropped.",
                section.heading,
                len(section.identifiers) - len(kept),
            )
        section.identifiers = kept

    outline.sections = [s for s in outline.sections if s.identifiers]
    if not outline.sections:
        raise ValueError(
            "No section could be tied to a stored paper. The topic is probably not "
            "what this knowledge base covers — check `mra status`."
        )
    return outline, available


def write(
    cfg: Config,
    store: Store,
    llm: LLM,
    outline: ReviewOutline,
    *,
    journal: str = "",
    on_section=None,
) -> tuple[str, citations.CitationReport]:
    """Write every section of a planned review and assemble it."""
    from . import journal as journal_mod

    profile = ""
    if journal:
        profile = f"\n\nMatch this journal's conventions:\n{journal_mod.profile_text(store, journal)}"

    parts = [f"# {outline.title}\n", f"*{outline.scope}*\n"]
    total = len(outline.sections)

    for index, section in enumerate(outline.sections, start=1):
        if on_section:
            on_section(index, total, section.heading)

        position = (
            f"Section {index} of {total}. "
            + (f"It follows: {outline.sections[index - 2].heading}. " if index > 1 else "")
            + (f"It is followed by: {outline.sections[index].heading}." if index < total else
               "It closes the review.")
        )

        body = prompts.load(
            "review_section",
            heading=section.heading,
            title=outline.title,
            argument=section.argument,
            words=WORDS_PER_SECTION,
            position=position,
            context=retrieval.render_pmid_list(store, section.identifiers),
        )
        result = llm.text(
            [prompts.core(cfg.chat_language), body + profile],
            [{"role": "user", "content": f"Write the section: {section.heading}"}],
            max_tokens=cfg.max_tokens,
            cache_upto=0,
        )
        parts.append(f"## {section.heading}\n\n{result.text.strip()}\n")

    text = "\n".join(parts)
    return text, citations.check(text, store)


def format_outline(outline: ReviewOutline, store: Store, available: list[str]) -> str:
    """Render the plan for review before anything is written."""
    lines = [
        f"标题  {outline.title}",
        f"范围  {outline.scope}",
        "",
        f"章节（{len(outline.sections)} 节）：",
    ]
    used: set[str] = set()
    for index, section in enumerate(outline.sections, start=1):
        used.update(section.identifiers)
        lines.append(f"\n  {index}. {section.heading}")
        lines.append(f"     论点：{section.argument}")
        lines.append(f"     依据：{len(section.identifiers)} 篇 — "
                     + ", ".join(section.identifiers[:6])
                     + (" …" if len(section.identifiers) > 6 else ""))

    if outline.controversies:
        lines += ["", "文献间的分歧（综述必须正面处理，不能只报多数方向）："]
        lines += [f"  ⚠ {c}" for c in outline.controversies]

    if outline.gaps:
        lines += ["", "这批文献没有回答的问题："]
        lines += [f"  · {g}" for g in outline.gaps]

    if outline.unsupported:
        lines += ["", "读者会预期、但你库里的文献撑不住的论断："]
        lines += [f"  ✗ {u}" for u in outline.unsupported]

    # Coverage is worth stating plainly: an unused paper is either off-topic or a
    # retrieval miss, and only the researcher can tell which.
    unused = [i for i in available if i not in used]
    lines += ["", f"覆盖：库里 {store.count_articles()} 篇，本次检索到 {len(available)} 篇，"
                  f"大纲用上 {len(used)} 篇。"]
    if unused:
        lines.append(f"  未用上：{', '.join(unused[:10])}"
                     + (f" 等 {len(unused)} 篇" if len(unused) > 10 else ""))
        lines.append("  （可能是与主题无关，也可能是大纲漏了——你比模型清楚）")
    return "\n".join(lines)
