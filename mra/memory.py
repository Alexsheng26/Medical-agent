"""Long-term evolution layer (Clause 6).

Two things accumulate as the researcher uses the agent:

- a **topic graph**, built deterministically from stored MeSH terms, extraction
  cards and hypothesis chains — this is what lets a new project start from the
  researcher's actual centre of gravity instead of from nothing;
- a **writing fingerprint**, learned from their own prior text, so drafts come
  back sounding like them.

The topic graph needs no model calls, so it stays cheap to refresh after every
session.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import prompts
from .config import Config
from .deai import split_sentences
from .llm import LLM
from .schemas import WritingFingerprint
from .store import Store

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")

# Terms too generic to say anything about a researcher's direction.
STOP_CONCEPTS = {
    "humans", "animals", "male", "female", "adult", "mice", "rats", "aged",
    "middle aged", "young adult", "cells", "cell line", "treatment outcome",
    "risk factors", "prognosis", "retrospective studies", "prospective studies",
}


@dataclass
class Memory:
    path: Path
    concepts: Counter = field(default_factory=Counter)
    edges: Counter = field(default_factory=Counter)
    hypothesis_titles: list[str] = field(default_factory=list)
    fingerprint: dict | None = None
    updated_at: str = ""

    # ------------------------------------------------------------ persistence

    @classmethod
    def load(cls, path: Path) -> "Memory":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            concepts=Counter(data.get("concepts", {})),
            # JSON cannot key on tuples, so edges round-trip as "a||b".
            edges=Counter({tuple(k.split("||")): v for k, v in data.get("edges", {}).items()}),
            hypothesis_titles=data.get("hypothesis_titles", []),
            fingerprint=data.get("fingerprint"),
            updated_at=data.get("updated_at", ""),
        )

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.write_text(
            json.dumps(
                {
                    "concepts": dict(self.concepts),
                    "edges": {"||".join(k): v for k, v in self.edges.items()},
                    "hypothesis_titles": self.hypothesis_titles,
                    "fingerprint": self.fingerprint,
                    "updated_at": self.updated_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.path

    # ----------------------------------------------------------- topic graph

    def refresh_topics(self, store: Store) -> None:
        """Rebuild the topic graph from everything currently in the store."""
        self.concepts = Counter()
        self.edges = Counter()

        for pmid in store.all_pmids():
            article = store.get_article(pmid)
            if article is None:
                continue

            terms = {t.lower() for t in article.mesh_terms}
            card = store.get_card(pmid)
            if card:
                terms |= {k.lower() for k in card.get("mechanism_keywords", [])}

            terms = {t for t in terms if t and t not in STOP_CONCEPTS}
            self.concepts.update(terms)

            # Co-occurrence within a paper is the edge signal. Sorting the pair
            # keeps the graph undirected.
            ordered = sorted(terms)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    self.edges[(a, b)] += 1

        self.hypothesis_titles = []
        for row in store.list_hypotheses():
            payload = store.get_hypothesis(row["version"])
            if payload and payload.get("title"):
                self.hypothesis_titles.append(f"v{row['version']}: {payload['title']}")

    def core_concepts(self, top: int = 20) -> list[tuple[str, int]]:
        return self.concepts.most_common(top)

    def strongest_links(self, top: int = 15) -> list[tuple[tuple[str, str], int]]:
        return [(pair, n) for pair, n in self.edges.most_common(top) if n > 1]

    def summary(self) -> str:
        if not self.concepts:
            return "Topic graph is empty. Run `mra search` to populate the knowledge base."

        lines = [f"Topic graph — {len(self.concepts)} concepts, {len(self.edges)} links", ""]
        lines.append("Centre of gravity:")
        for concept, count in self.core_concepts(15):
            lines.append(f"  {count:>4}×  {concept}")

        links = self.strongest_links(10)
        if links:
            lines += ["", "Strongest associations:"]
            for (a, b), count in links:
                lines.append(f"  {count:>4}×  {a} — {b}")

        if self.hypothesis_titles:
            lines += ["", "Hypothesis trajectory:"]
            lines += [f"  {title}" for title in self.hypothesis_titles]

        if self.fingerprint:
            lines += ["", "Writing fingerprint: on file "
                      f"(mean sentence {self.fingerprint.get('mean_sentence_length', 0):.1f} words)"]
        else:
            lines += ["", "Writing fingerprint: not built yet (`mra fingerprint <dir>`)"]

        lines += ["", f"Last updated: {self.updated_at or 'never'}"]
        return "\n".join(lines)

    # ------------------------------------------------------------ fingerprint

    def fingerprint_text(self) -> str:
        return json.dumps(self.fingerprint, ensure_ascii=False, indent=2)


def measure_style(text: str) -> dict[str, float]:
    """Deterministic style statistics — no model call needed."""
    sentences = split_sentences(text)
    lengths = [len(_WORD.findall(s)) for s in sentences] or [0]
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    para_lengths = [len(_WORD.findall(p)) for p in paragraphs] or [0]

    return {
        "sentences": float(len(sentences)),
        "mean_sentence_length": statistics.fmean(lengths),
        "median_sentence_length": float(statistics.median(lengths)),
        "stdev_sentence_length": statistics.pstdev(lengths) if len(lengths) > 1 else 0.0,
        "longest_sentence": float(max(lengths)),
        "shortest_sentence": float(min(lengths)),
        "paragraphs": float(len(paragraphs)),
        "mean_paragraph_length": statistics.fmean(para_lengths),
        "passive_markers_per_100_sentences": _passive_rate(sentences),
        "first_person_rate": _first_person_rate(sentences),
    }


def build_fingerprint(
    cfg: Config, llm: LLM, memory: Memory, sample_dir: Path
) -> WritingFingerprint:
    """Learn the researcher's voice from their own prior writing."""
    if not sample_dir.exists():
        raise FileNotFoundError(f"No such directory: {sample_dir}")

    texts = []
    for path in sorted(sample_dir.iterdir()):
        if path.suffix.lower() in {".txt", ".md", ".markdown", ".text"}:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if len(content) > 400:
                texts.append(f"({path.name})\n{content}")

    if not texts:
        raise ValueError(
            f"No usable text files in {sample_dir}. Save two or three of your own "
            "published papers as .txt and try again."
        )

    combined = "\n\n===\n\n".join(texts)
    stats = measure_style(combined)

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "fingerprint",
            stats=json.dumps(stats, indent=2),
            samples=combined[:60000],
        ),
    ]
    fingerprint = llm.parse(
        system,
        [{"role": "user", "content": "Build the writing fingerprint."}],
        WritingFingerprint,
        effort=cfg.effort,
        max_tokens=8000,
    )

    payload = fingerprint.model_dump()
    # The measured value is authoritative over anything the model reports.
    payload["mean_sentence_length"] = stats["mean_sentence_length"]
    payload["_measured"] = stats
    memory.fingerprint = payload
    memory.save()
    return fingerprint


def _passive_rate(sentences: list[str]) -> float:
    pattern = re.compile(
        r"\b(?:was|were|is|are|been|being|be)\s+\w+(?:ed|en)\b", re.IGNORECASE
    )
    hits = sum(1 for s in sentences if pattern.search(s))
    return 100.0 * hits / len(sentences) if sentences else 0.0


def _first_person_rate(sentences: list[str]) -> float:
    pattern = re.compile(r"\b(?:we|our|us)\b", re.IGNORECASE)
    hits = sum(1 for s in sentences if pattern.search(s))
    return 100.0 * hits / len(sentences) if sentences else 0.0
