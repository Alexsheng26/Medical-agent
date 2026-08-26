"""Scientific dialogue, hypothesis versioning, and proposal generation."""

from __future__ import annotations

import json

import logging

from . import citations, prompts, retrieval
from .config import Config
from .llm import LLM
from .schemas import Hypothesis
from .store import Store

log = logging.getLogger(__name__)


# Messages (not turns) kept verbatim. Everything older is folded into a running
# summary instead of being dropped, and the fold only happens once enough has
# accumulated to be worth a call.
CHAT_WINDOW = 40
FOLD_WHEN = 10


def fold_older_turns(cfg: Config, store: Store, llm: LLM) -> int:
    """Compress whatever has fallen outside the window into the running summary.

    Returns how many messages were folded, 0 when nothing was due.

    The truncation this replaces was silent: turn 21 simply stopped being able
    to see turn 3, so the dialogue would re-propose a mechanism it had already
    ruled out and the researcher had no way to know why. A loss nobody is told
    about is worse than a smaller window nobody minds.
    """
    existing = store.chat_summary()
    covered = existing[0] if existing else 0
    pending = store.chat_before(CHAT_WINDOW, after_id=covered)
    if len(pending) < FOLD_WHEN:
        return 0

    transcript = "\n\n".join(
        f"{'研究者' if m['role'] == 'user' else '助手'}：{m['content']}" for m in pending
    )
    try:
        result = llm.text(
            [prompts.load(
                "chat_summary",
                language="中文" if cfg.chat_language == "zh" else "English",
                previous=existing[1] if existing else "（无）",
                messages=transcript,
            )],
            [{"role": "user", "content": "Produce the combined summary."}],
            max_tokens=2000,
        )
    except Exception as exc:  # noqa: BLE001 — losing the fold must not lose the turn
        log.warning("Could not summarise earlier turns (%s); keeping them out of context.", exc)
        return 0

    store.save_chat_summary(pending[-1]["id"], result.text.strip())
    return len(pending)


def respond(cfg: Config, store: Store, llm: LLM, message: str) -> str:
    """One conversational turn, grounded in retrieved literature."""
    # Retrieve against the message plus the current hypothesis title, so a
    # follow-up like "what about the second step?" still pulls relevant papers.
    latest = store.latest_hypothesis()
    query = message
    if latest:
        query = f"{message} {latest[1].get('title', '')}"

    context, _pmids = retrieval.build_context(store, query, k=cfg.retrieval_k, cfg=cfg, llm=llm)

    earlier = store.chat_summary()
    system = [
        prompts.core(cfg.chat_language),
        prompts.load("dialogue", context=context),
    ]
    if earlier:
        system.append(
            "以下是本次对话更早部分的摘要。它不是文献，是这场讨论自己的结论——"
            "特别是已经排除掉的方向和排除的理由，不要重新提出来。\n\n" + earlier[1]
        )

    history = store.chat_history(limit=CHAT_WINDOW)
    history.append({"role": "user", "content": message})

    # The dialogue block carries this turn's retrieval context; only the
    # core prompt is stable enough to cache.
    result = llm.text(system, history, cache_upto=0)

    store.append_chat("user", message)
    store.append_chat("assistant", result.text)
    return result.text


def consolidate(cfg: Config, store: Store, llm: LLM, note: str = "") -> tuple[int, Hypothesis]:
    """Freeze the current conversation into a versioned hypothesis (Clause 2.4)."""
    history = store.chat_history(limit=60)
    if not history:
        raise ValueError("No conversation yet. Run `mra chat` first.")

    transcript = "\n\n".join(f"[{turn['role']}] {turn['content']}" for turn in history)
    context, _ = retrieval.build_context(
        store, transcript[-4000:], k=max(cfg.retrieval_k, 16), cfg=cfg, llm=llm
    )

    previous = store.latest_hypothesis()
    prior_block = ""
    if previous:
        prior_block = (
            f"\n\n## Previous hypothesis (v{previous[0]})\n"
            f"{json.dumps(previous[1], ensure_ascii=False, indent=2)}\n\n"
            "Where this version differs, it should differ because the conversation "
            "moved it, not because it was regenerated from scratch."
        )

    system = [prompts.core(cfg.chat_language), prompts.load("hypothesis")]
    user = (
        f"## Evidence base\n\n{context}\n\n"
        f"## Conversation so far\n\n{transcript}{prior_block}"
    )

    hypothesis = llm.parse(system, [{"role": "user", "content": user}], Hypothesis,
                           effort=cfg.effort, max_tokens=12000)
    payload = hypothesis.model_dump()
    payload["novelty_level"] = max(1, min(5, payload.get("novelty_level", 3)))
    version = store.save_hypothesis(payload, note=note)
    return version, hypothesis


def write_proposal(cfg: Config, store: Store, llm: LLM, version: int | None = None) -> tuple[str, citations.CitationReport]:
    """Generate a full proposal from a stored hypothesis."""
    if version is None:
        latest = store.latest_hypothesis()
        if latest is None:
            raise ValueError("No hypothesis stored. Run `mra hypothesis` first.")
        version, payload = latest
    else:
        payload = store.get_hypothesis(version)
        if payload is None:
            raise ValueError(f"No hypothesis version {version}.")

    seed = " ".join(
        filter(
            None,
            [
                payload.get("title", ""),
                payload.get("statement", ""),
                " ".join(payload.get("mechanism_chain", [])),
            ],
        )
    )
    context, _ = retrieval.build_context(store, seed, k=max(cfg.retrieval_k, 20), cfg=cfg, llm=llm)

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "proposal",
            hypothesis=json.dumps(payload, ensure_ascii=False, indent=2),
            context=context,
        ),
    ]
    result = llm.text(
        system,
        [{"role": "user", "content": "Write the proposal."}],
        max_tokens=cfg.max_tokens,
        cache_upto=0,
    )

    return result.text, citations.check(result.text, store)


def diff_hypotheses(store: Store, older: int, newer: int) -> str:
    """Field-by-field comparison of two hypothesis versions."""
    a, b = store.get_hypothesis(older), store.get_hypothesis(newer)
    if a is None or b is None:
        raise ValueError("One or both versions do not exist.")

    lines = [f"Hypothesis v{older} → v{newer}", ""]
    for key in sorted(set(a) | set(b)):
        before, after = a.get(key), b.get(key)
        if before == after:
            continue
        lines.append(f"## {key}")
        if isinstance(before, list) or isinstance(after, list):
            before_set = set(map(str, before or []))
            after_set = set(map(str, after or []))
            for item in sorted(after_set - before_set):
                lines.append(f"  + {item}")
            for item in sorted(before_set - after_set):
                lines.append(f"  - {item}")
        else:
            lines.append(f"  - {before}")
            lines.append(f"  + {after}")
        lines.append("")
    if len(lines) == 2:
        lines.append("(identical)")
    return "\n".join(lines)
