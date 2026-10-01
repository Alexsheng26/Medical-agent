"""Point-by-point response to peer review.

The chain stopped at `finalize`: the manuscript goes out and comes back with
three reviewers and twenty-odd comments, which is where months actually go. It
is also the step where everything this tool already stores is worth most — the
library knows what is published, the hypothesis knows what the data can carry,
the journal profile knows the register.

The danger here is specific, and it is not hallucination. Ask any model to
"respond to reviewers" and it agrees with all of them: every point conceded,
every concession a promise of new experiments. That letter costs the authors
either months of work they never agreed to, or a claim their data supported.
So the model is made to classify before it writes, and two things it cannot be
trusted on are checked mechanically afterwards — that each comment it answers
was really in the letter, and that each paper it cites really exists.
"""

from __future__ import annotations

import json
import re
import textwrap

from . import assess, citations, prompts, retrieval
from . import journal as journal_mod
from .config import Config
from .llm import LLM
from .schemas import Rebuttal, ReviewerPoint
from .store import Store

RETRIEVAL_K = 12

# Long enough that an accidental match is implausible, short enough to survive
# the whitespace and quote-character churn of a pasted decision letter.
QUOTE_PROBE = 40

STANCE_LABELS = {
    "concede-and-do": "认，要补",
    "concede-and-limit": "认，但写成局限",
    "already-addressed": "文中已有，是没写显眼",
    "disagree": "不认",
    "out-of-scope": "超出本文范围",
}

SEVERITY_LABELS = {"fatal": "动摇主结论", "major": "影响结论或图", "minor": "小问题"}

# Order the report by what the researcher has to decide about first.
_SEVERITY_RANK = {"fatal": 0, "major": 1, "minor": 2}


def respond(
    cfg: Config,
    store: Store,
    llm: LLM,
    reviews: str,
    manuscript: str,
    *,
    data_paths: list | None = None,
    journal: str = "",
) -> Rebuttal:
    """Draft the response letter for one set of reviews."""
    # Data is optional here, unlike in `assess`: plenty of comments are about
    # framing and the literature, and refusing to start without a CSV would
    # make the command useless for exactly those.
    paths = list(data_paths or [])
    data = (
        "\n\n===\n\n".join(assess._data_blocks(paths, ""))
        if paths
        else "(没有附原始数据。凡是需要重算才能回答的意见，写进 new_work，不要替作者"
             "假设数字。)"
    )

    context, _ = retrieval.build_context(
        store, _retrieval_query(reviews), k=RETRIEVAL_K, cfg=cfg, llm=llm
    )
    profile = (
        journal_mod.profile_text(store, journal)
        if journal
        else "(没有期刊档案。用专科期刊回复信的通行写法。)"
    )

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "rebuttal",
            reviews=reviews,
            manuscript=manuscript,
            data=data,
            profile=profile,
            context=context,
        ),
    ]
    rebuttal = llm.parse(
        system,
        [{"role": "user", "content": "Draft the point-by-point response."}],
        Rebuttal,
        effort=cfg.effort,
        max_tokens=cfg.max_tokens,
        cache_upto=0,
    )

    rebuttal.points.sort(key=_order)
    return rebuttal


def _order(point: ReviewerPoint) -> tuple:
    return (_SEVERITY_RANK.get(point.severity, 3), point.reviewer, _numeric(point.number))


def _numeric(number: str) -> tuple:
    """Sort 1, 2, 10 — not 1, 10, 2 — while tolerating '2.1' and 'Q3'."""
    return tuple(int(part) for part in re.findall(r"\d+", number)) or (0,)


def _retrieval_query(reviews: str) -> str:
    """Reviewers name the literature they expect to see; that is the query.

    Their objections are where the citations have to land, so searching the
    review text finds more usable papers than searching the manuscript would.
    """
    return reviews[:2000]


# ------------------------------------------------------------------- the checks


def unquoted_points(rebuttal: Rebuttal, reviews: str) -> list[str]:
    """Points whose `quote` is not in the review letter.

    A response to a comment nobody made is not a drafting slip — it is a whole
    paragraph of the letter addressed to nothing, and it reads to an editor as
    though the authors answered a different paper's reviews. Cheap to check, so
    checked rather than trusted.
    """
    haystack = _normalize(reviews)
    missing = []
    for point in rebuttal.points:
        probe = _normalize(point.quote)[:QUOTE_PROBE]
        if not probe:
            missing.append(f"{point.reviewer} #{point.number}（没有引用原文）")
        elif probe not in haystack:
            missing.append(f"{point.reviewer} #{point.number}：{point.quote[:60]}…")
    return missing


#: Curly quotes, dashes and non-breaking spaces all survive a copy out of a PDF
#: decision letter and differ from what the model echoes back.
_CHURN = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ", "…": "...",
})


def _normalize(text: str) -> str:
    """Fold the differences a paste introduces, and nothing else.

    Case is folded too: the probe is long enough that an accidental match is
    implausible, and a reviewer's sentence re-capitalised at the start is a
    quote, not an invention.
    """
    return " ".join(text.translate(_CHURN).lower().split())


def fabricated_citations(rebuttal: Rebuttal, store: Store) -> list[str]:
    """Identifiers cited in the responses that are not in the library.

    An editor checks a citation in a response letter in about ten seconds, and
    one that does not exist costs the authors the benefit of the doubt on every
    other point in the letter. Both the prose and the `evidence` lists are
    checked: a marker can reach the letter through either.
    """
    evidence = " ".join(marker for point in rebuttal.points for marker in point.evidence)
    report = citations.check(f"{letter_body(rebuttal)}\n{evidence}", store)
    return list(report.unverified)


def commitments(rebuttal: Rebuttal) -> list[ReviewerPoint]:
    """Points that promise work nobody has done yet."""
    return [p for p in rebuttal.points if p.new_work.strip()]


# ------------------------------------------------------------------- rendering


def letter_body(rebuttal: Rebuttal) -> str:
    """Just the responses — what would go to the editor."""
    return "\n\n".join(point.response for point in rebuttal.points)


def letter(rebuttal: Rebuttal) -> str:
    """The response letter itself.

    Only the reviewers' words and the authors' replies. Everything addressed to
    the researcher — severity, risk, what still has to be decided — stays on
    screen: this file goes to the editor, and a note meant for the author
    riding along in it is the kind of mistake that is noticed by exactly the
    wrong person.
    """
    lines = ["# Response to Reviewers", "", rebuttal.overall, ""]

    for reviewer in _reviewers(rebuttal):
        lines += [f"## {reviewer}", ""]
        for point in rebuttal.points:
            if point.reviewer != reviewer:
                continue
            lines += [f"**Comment {point.number}.** {point.quote}", ""]
            lines += [point.response, ""]
            if point.manuscript_change.strip().lower() not in ("", "none"):
                lines += [f"*Changes to the manuscript:* {point.manuscript_change}", ""]

    return "\n".join(lines).rstrip() + "\n"


def _reviewers(rebuttal: Rebuttal) -> list[str]:
    """Reviewers in first-appearance order, each once."""
    seen: list[str] = []
    for point in rebuttal.points:
        if point.reviewer not in seen:
            seen.append(point.reviewer)
    return seen


def format_rebuttal(rebuttal: Rebuttal) -> str:
    """Render the working view for the terminal."""
    lines = ["审稿意见逐条处理", "=" * 60, ""]

    counts = _tally(rebuttal)
    lines.append("  " + "    ".join(f"{STANCE_LABELS[s]} {n}" for s, n in counts))
    lines.append("")

    if rebuttal.threats:
        lines += ["═" * 60, "这几条真的动摇主结论——怎么回都绕不过去：", ""]
        lines += [f"  ✗ {item}" for item in rebuttal.threats]
        lines += [""]

    for point in rebuttal.points:
        lines.append("─" * 60)
        severity = SEVERITY_LABELS.get(point.severity, point.severity)
        lines.append(f"{point.reviewer} #{point.number}   [{severity}] "
                     f"{STANCE_LABELS.get(point.stance, point.stance)}")
        lines.append(f"  原文：{point.quote}")
        lines.append(f"  他真正要的：{point.asks_for}")
        lines.append("")
        lines.append("  回复草稿：")
        lines += [f"    {line}" for line in _wrap(point.response)]
        if point.manuscript_change.strip().lower() not in ("", "none"):
            lines.append(f"  稿子要改：{point.manuscript_change}")
        if point.new_work.strip():
            lines.append(f"  ⚑ 这条答应了要补：{point.new_work}")
        if point.evidence:
            lines.append(f"  依据：{' '.join(point.evidence)}")
        if point.risk.strip():
            lines.append(f"  ⚠ 他可能还会追：{point.risk}")
        lines.append("")

    if work := commitments(rebuttal):
        lines += ["═" * 60, "这封信一旦发出，你就承诺了这些事：", ""]
        for point in work:
            lines.append(f"  · {point.reviewer} #{point.number}  {point.new_work}")
        lines += ["", "  发信之前先确认每一条你都做得到、也打算做。", ""]

    if rebuttal.decisions:
        lines += ["═" * 60, "这些只有你能定：", ""]
        lines += [f"  ? {item}" for item in rebuttal.decisions]

    return "\n".join(lines)


def _tally(rebuttal: Rebuttal) -> list[tuple[str, int]]:
    counts = []
    for stance in STANCE_LABELS:
        n = sum(1 for p in rebuttal.points if p.stance == stance)
        if n:
            counts.append((stance, n))
    return counts


def _wrap(text: str, width: int = 72) -> list[str]:
    return textwrap.wrap(text, width=width) or [""]


def to_json(rebuttal: Rebuttal) -> str:
    return json.dumps(rebuttal.model_dump(), ensure_ascii=False, indent=2)
