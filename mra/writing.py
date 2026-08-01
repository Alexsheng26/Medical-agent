"""Drafting, nativization and the de-AI rewrite loop (Clause 5)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import citations, deai, journal as journal_mod, prompts, retrieval
from .config import Config
from .llm import LLM
from .memory import Memory
from .store import Store

log = logging.getLogger(__name__)

SECTIONS = ["title", "abstract", "introduction", "results", "discussion", "methods"]

# Below this the lint no longer finds meaningful patterns; more rewriting starts
# costing content fidelity for no readability gain.
DEFAULT_TARGET_SCORE = 15.0
DEFAULT_MAX_ROUNDS = 3

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class PolishResult:
    text: str
    rounds: int
    initial: deai.DeAIReport
    final: deai.DeAIReport
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"De-AI pass: {self.initial.score:.1f} → {self.final.score:.1f} "
            f"({self.final.verdict}) over {self.rounds} round(s)",
        ]
        if self.warnings:
            lines.append("")
            lines += [f"  ⚠ {w}" for w in self.warnings]
        return "\n".join(lines)


def draft(
    cfg: Config,
    store: Store,
    llm: LLM,
    section: str,
    journal: str,
    data_text: str,
    *,
    context_query: str = "",
) -> str:
    """Generate one manuscript section in a journal's style."""
    profile = journal_mod.profile_text(store, journal)
    query = context_query or data_text[:2000]
    context, _ = retrieval.build_context(store, query, k=cfg.retrieval_k)

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "draft",
            section=section,
            journal=journal,
            profile=profile,
            data=data_text,
            context=context,
        ),
    ]
    result = llm.text(
        system,
        [{"role": "user", "content": f"Write the {section}."}],
        max_tokens=cfg.max_tokens,
        cache_upto=0,
    )
    return result.text


def nativize(
    cfg: Config,
    store: Store,
    llm: LLM,
    text: str,
    journal: str,
    *,
    memory: Memory | None = None,
) -> str:
    """Language pass: idiomatic native-speaker English in the journal's register."""
    profile = journal_mod.profile_text(store, journal)
    fingerprint = "(No fingerprint on file — write in the journal's default voice.)"
    if memory and memory.fingerprint:
        fingerprint = memory.fingerprint_text()

    system = [
        prompts.core(cfg.chat_language),
        prompts.load("nativize", journal=journal, profile=profile,
                     fingerprint=fingerprint, text=text),
    ]
    result = llm.text(
        system,
        [{"role": "user", "content": "Rewrite the text."}],
        max_tokens=cfg.max_tokens,
        cache_upto=0,
    )
    return result.text


def polish(
    cfg: Config,
    llm: LLM,
    text: str,
    *,
    target: float = DEFAULT_TARGET_SCORE,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    on_round=None,
) -> PolishResult:
    """Iteratively remove AI tells, checking content fidelity each round.

    The lint drives the rewrite: the model is told exactly which patterns were
    detected, rather than being asked to 'make it sound human', which produces
    synonym substitution and nothing else.
    """
    initial = deai.analyze(text)
    current = text
    report = initial
    rounds = 0
    warnings: list[str] = []

    while rounds < max_rounds and report.score > target:
        system = [
            prompts.core(cfg.chat_language),
            prompts.load("deai_rewrite", lint_report=report.summary(), text=current),
        ]
        result = llm.text(
            system,
            [{"role": "user", "content": "Revise the text."}],
            max_tokens=cfg.max_tokens,
            cache_upto=0,
        )
        candidate = _strip_fences(result.text)
        rounds += 1

        candidate_report = deai.analyze(candidate)
        drift = _fidelity_warnings(current, candidate)

        if on_round:
            on_round(rounds, report.score, candidate_report.score)

        # A rewrite that scores worse is discarded — the previous text stands.
        if candidate_report.score >= report.score:
            log.info("Round %d did not improve (%.1f → %.1f); keeping previous text.",
                     rounds, report.score, candidate_report.score)
            break

        warnings.extend(drift)
        current, report = candidate, candidate_report

    return PolishResult(
        text=current, rounds=rounds, initial=initial, final=report, warnings=warnings
    )


def write_versions(
    cfg: Config,
    store: Store,
    llm: LLM,
    text: str,
    journal: str,
    stem: str,
    *,
    memory: Memory | None = None,
) -> dict[str, Path]:
    """Produce the two deliverables the proposal calls for: v1 assisted draft,
    v2 nativized and de-AI'd. Both are kept so they can be compared."""
    cfg.ensure_workspace()
    paths: dict[str, Path] = {}

    v1 = cfg.drafts_dir / f"{stem}.v1.md"
    v1.write_text(text, encoding="utf-8")
    paths["v1"] = v1

    native = nativize(cfg, store, llm, text, journal, memory=memory)
    polished = polish(cfg, llm, native)

    v2 = cfg.drafts_dir / f"{stem}.v2.md"
    v2.write_text(polished.text, encoding="utf-8")
    paths["v2"] = v2

    report = cfg.drafts_dir / f"{stem}.report.txt"
    report.write_text(
        polished.summary()
        + "\n\n"
        + polished.final.summary()
        + "\n\n"
        + citations.check(polished.text, store).summary(),
        encoding="utf-8",
    )
    paths["report"] = report
    return paths


def _fidelity_warnings(before: str, after: str) -> list[str]:
    """Catch a rewrite that quietly dropped data or citations."""
    warnings = []

    pmids_before = set(citations.CITATION_RE.findall(before))
    pmids_after = set(citations.CITATION_RE.findall(after))
    if lost := pmids_before - pmids_after:
        warnings.append(f"Rewrite dropped citations: {', '.join(sorted(lost))}")
    if added := pmids_after - pmids_before:
        warnings.append(f"Rewrite introduced citations not in the source: {', '.join(sorted(added))}")

    # Strip citation markers first: a dropped PMID is already reported above, and
    # letting it through here buries real data loss (an n, a p-value, an effect
    # size) in a list of identifiers.
    nums_before = set(_NUMBER_RE.findall(citations.CITATION_RE.sub(" ", before)))
    nums_after = set(_NUMBER_RE.findall(citations.CITATION_RE.sub(" ", after)))
    if lost_nums := nums_before - nums_after:
        sample = ", ".join(sorted(lost_nums, key=lambda n: (-len(n), n))[:8])
        warnings.append(f"Numbers present before the rewrite and absent after: {sample}")

    for marker in ("[DATA NEEDED", "[CITATION NEEDED"):
        if before.count(marker) > after.count(marker):
            warnings.append(f"Rewrite removed {marker}...] markers — verify nothing was papered over.")

    return warnings


def _strip_fences(text: str) -> str:
    """Models occasionally wrap a whole document in a code fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return stripped
