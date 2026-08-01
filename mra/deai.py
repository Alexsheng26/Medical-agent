"""Deterministic AI-tell lint for English scientific prose.

What this is: a rule-based detector for the surface habits that make generated
text recognisable — a fixed stock of connective phrases, unnaturally even
sentence rhythm, uniform paragraphs, and evidence-free intensifiers.

What this is not: a model of any commercial AI detector. Those are statistical
classifiers over token likelihood and are not reproducible from the outside. A
low score here means the text no longer carries the obvious tells; it is not a
prediction of what GPTZero or Turnitin will report. Treat this as an editor's
checklist that runs in a second, and keep a human read before submission.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

# Phrases that are rare in human-written papers and common in generated text.
# Weight reflects how strongly each one gives the game away.
TELLTALE_PHRASES: dict[str, float] = {
    r"\bdelve[sd]? into\b": 3.0,
    r"\bit is worth noting that\b": 2.5,
    r"\bit should be noted that\b": 2.0,
    r"\bplays? a (?:crucial|vital|pivotal|key|significant) role\b": 2.5,
    r"\bshed(?:s|ding)? light on\b": 2.0,
    r"\bpave[sd]? the way\b": 2.5,
    r"\bin the realm of\b": 3.0,
    r"\bin the landscape of\b": 3.0,
    r"\ba testament to\b": 3.0,
    r"\bunderscore[sd]? the importance\b": 2.5,
    r"\bhighlight(?:s|ing)? the importance\b": 2.0,
    r"\bcomprehensive (?:understanding|overview|analysis)\b": 2.0,
    r"\bintricate (?:interplay|network|mechanism|balance)\b": 3.0,
    r"\bmultifaceted\b": 2.0,
    r"\bmyriad\b": 2.0,
    r"\bever-(?:growing|evolving|increasing)\b": 2.5,
    r"\bit is important to (?:note|recognize|consider)\b": 2.0,
    r"\bcannot be overstated\b": 3.0,
    r"\bopens? (?:up )?new avenues\b": 2.5,
    r"\bholds? (?:great |significant )?promise\b": 2.0,
    r"\bpotential to revolutionize\b": 3.0,
    r"\bat the forefront of\b": 2.0,
    r"\bgrowing body of (?:evidence|literature)\b": 1.5,
    r"\bthis study (?:aims|seeks) to\b": 1.0,
    r"\bin summary,": 1.0,
    r"\bin conclusion,": 1.0,
    r"\bfirst and foremost\b": 2.5,
    r"\bnot only\b[^.]{1,80}\bbut also\b": 1.5,
    r"\bleverag(?:e|es|ed|ing)\b": 1.5,
    r"\butiliz(?:e|es|ed|ing)\b": 1.0,  # "use" is almost always the better word
    r"\bdemonstrat(?:e|es|ed) the potential\b": 1.5,
    r"\bfurther research is (?:needed|warranted|required)\b": 1.5,
    r"\bpar(?:adigm)? shift\b": 2.0,
    r"\bunlock(?:s|ing)? the (?:potential|secrets)\b": 3.0,
    r"\bnavigat(?:e|es|ing) the complexit(?:y|ies)\b": 3.0,
    r"\bplethora of\b": 2.0,
    r"\bseamless(?:ly)?\b": 2.0,
    r"\bdive deep(?:er)? into\b": 2.5,
    r"\brobust(?:ly)? (?:framework|approach|methodology)\b": 1.5,
}

# Sentence-initial connectives. A few are fine; a wall of them is a tell.
FORMULAIC_OPENERS = [
    "furthermore",
    "moreover",
    "additionally",
    "consequently",
    "notably",
    "importantly",
    "overall",
    "collectively",
    "interestingly",
    "remarkably",
    "crucially",
    "significantly",
    "in addition",
    "on the other hand",
    "taken together",
]

# Intensifiers that assert importance instead of showing it.
EMPTY_INTENSIFIERS = [
    "crucial",
    "vital",
    "pivotal",
    "profound",
    "remarkable",
    "compelling",
    "striking",
    "groundbreaking",
    "cutting-edge",
    "state-of-the-art",
]

# Stored as single whitespace-delimited tokens, because that is how the splitter
# sees them: "et al." is checked as the token "al.".
#
# These practically never end a sentence, so a following capital is still part of
# the same sentence ("e.g. CCl4 and CDAHFD were compared").
_NEVER_TERMINAL = {"e.g.", "i.e.", "cf.", "vs.", "al.", "resp.", "viz."}

# These are abbreviations that can also legitimately end a sentence ("... for 12
# wk. Fibrosis increased."), so they only suppress a split when what follows
# starts lowercase or with a digit.
_AMBIGUOUS_ABBREVIATIONS = {
    "fig.", "figs.", "eq.", "ref.", "refs.", "approx.", "ca.", "no.", "dr.",
    "prof.", "min.", "max.", "sd.", "se.", "std.", "wk.", "hr.", "mo.", "yr.", "ver.",
}

# Findings that describe the document as a whole rather than scaling with its
# length. Scoring these per-100-words would let a short clean passage look
# saturated on the strength of one structural observation.
_STRUCTURAL_KINDS = {"uniform rhythm", "uniform paragraphs", "nominalisation"}

# No single signal may saturate the score on its own.
_MAX_FINDING_WEIGHT = 25.0

_SENT_SPLIT = re.compile(r"(?<=[.!?])[\s\n]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


@dataclass
class Finding:
    kind: str
    detail: str
    weight: float
    evidence: list[str] = field(default_factory=list)
    suggestion: str = ""


@dataclass
class DeAIReport:
    score: float  # 0 = no detected tells, 100 = saturated with them
    findings: list[Finding]
    metrics: dict[str, float]

    @property
    def verdict(self) -> str:
        if self.score < 15:
            return "clean"
        if self.score < 35:
            return "minor tells"
        if self.score < 60:
            return "clearly patterned"
        return "heavily patterned"

    def summary(self) -> str:
        lines = [
            f"AI-tell lint score: {self.score:.1f}/100 ({self.verdict})",
            "  (heuristic, not an AI-detector prediction — see mra/deai.py docstring)",
            "",
            "Metrics:",
            f"  sentences                 {int(self.metrics['sentences'])}",
            f"  mean sentence length      {self.metrics['mean_sentence_len']:.1f} words",
            f"  sentence length st.dev.   {self.metrics['stdev_sentence_len']:.1f} "
            f"(burstiness {self.metrics['burstiness']:.2f}; human papers usually > 0.35)",
            f"  formulaic opener rate     {self.metrics['opener_rate'] * 100:.1f}% of sentences",
            f"  paragraph length st.dev.  {self.metrics['paragraph_stdev']:.1f} words",
        ]
        if not self.findings:
            lines += ["", "No flagged patterns."]
            return "\n".join(lines)

        lines += ["", "Findings (highest impact first):"]
        for finding in sorted(self.findings, key=lambda f: -f.weight):
            lines.append(f"  [{finding.weight:4.1f}] {finding.kind}: {finding.detail}")
            for example in finding.evidence[:3]:
                lines.append(f"           · {example}")
            if finding.suggestion:
                lines.append(f"           → {finding.suggestion}")
        return "\n".join(lines)


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping common scientific abbreviations intact."""
    rough = _SENT_SPLIT.split(text.replace("\n", " "))
    sentences: list[str] = []
    for piece in rough:
        piece = piece.strip()
        if not piece:
            continue
        # Re-join when the previous fragment ended on an abbreviation or an
        # initial rather than a real sentence boundary.
        if sentences and sentences[-1].split():
            raw_tail = sentences[-1].split()[-1]
            tail = raw_tail.lower()
            # Case matters for the initial check: "W." is an initial, "w." is not.
            is_initial = bool(re.fullmatch(r"\(?[A-Z]\.", raw_tail))
            continues_lowercase = bool(re.match(r"[a-z0-9]", piece))

            if tail in _NEVER_TERMINAL or (
                (tail in _AMBIGUOUS_ABBREVIATIONS or is_initial) and continues_lowercase
            ):
                sentences[-1] = f"{sentences[-1]} {piece}"
                continue
        sentences.append(piece)
    return sentences


# Editorial annotations the drafting step inserts, and citation markers. Neither
# is prose: `[DATA NEEDED: ...]` is a question addressed to the researcher and
# will be answered or deleted before submission.
ANNOTATION_RE = re.compile(
    r"\[(?:DATA|CITATION) NEEDED[^\]]*\]|\[(?:PMID|LOCAL):[^\]]*\]",
    re.IGNORECASE,
)


# Structural markup, stripped so that the words survive but the syntax does not.
# `### Septal density rises with stage` is a heading whose words belong to the
# document's voice; `###` is not the sentence's first word.
_MARKDOWN_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|>\s?|\d+\.\s+)", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)


def strip_annotations(text: str) -> str:
    """Remove editorial markup before scoring.

    A draft that honestly flags every missing number gets a long run of
    `[DATA NEEDED: ...]` blocks, and scoring them as prose punishes exactly the
    behaviour we want: they registered as sentences all starting with the same
    word, and their contents produced 'triadic list' findings. Worse, `polish`
    is driven by these findings, so the rewrite was being told to recast the
    markers — while `writing.py` separately warns when a rewrite removes them.
    Two halves of the tool pulling against each other. Markdown structure is
    dropped for the same reason: four `###` headings were being reported as
    "4 sentences begin with '###'", which is a fact about the file format.
    """
    text = ANNOTATION_RE.sub(" ", text)
    text = _MARKDOWN_LINE_RE.sub("", text)
    text = _EMPHASIS_RE.sub(r"\2", text)
    # An annotation sitting before the full stop would otherwise leave `stage .`,
    # which the sentence splitter reads as its own fragment.
    text = re.sub(r"\s+([.,;:!?)])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def analyze(text: str) -> DeAIReport:
    """Score English prose for surface AI tells."""
    text = strip_annotations(text)
    sentences = split_sentences(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    words = _WORD.findall(text)
    findings: list[Finding] = []

    lengths = [len(_WORD.findall(s)) for s in sentences] or [0]
    mean_len = statistics.fmean(lengths)
    stdev_len = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    burstiness = (stdev_len / mean_len) if mean_len else 0.0

    # --- 1. telltale phrases -------------------------------------------------
    for pattern, weight in TELLTALE_PHRASES.items():
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            findings.append(
                Finding(
                    kind="stock phrase",
                    detail=f"{len(matches)}× {_readable(pattern)}",
                    weight=weight * len(matches),
                    evidence=[_context(text, pattern)],
                    suggestion="Replace with the specific claim, or delete the sentence.",
                )
            )

    # --- 2. even sentence rhythm --------------------------------------------
    if len(sentences) >= 6 and burstiness < 0.35:
        findings.append(
            Finding(
                kind="uniform rhythm",
                detail=f"sentence lengths cluster tightly (burstiness {burstiness:.2f})",
                weight=12.0 * (0.35 - burstiness) / 0.35,
                suggestion="Break one long sentence into two, and let another run long. "
                "Human methods sections swing between 8 and 40 words.",
            )
        )

    # --- 3. formulaic openers ------------------------------------------------
    opener_hits = [
        s for s in sentences
        if any(s.lower().lstrip("(").startswith(o) for o in FORMULAIC_OPENERS)
    ]
    opener_rate = len(opener_hits) / len(sentences) if sentences else 0.0
    if opener_rate > 0.12:
        findings.append(
            Finding(
                kind="formulaic openers",
                detail=f"{len(opener_hits)}/{len(sentences)} sentences open with a stock connective",
                weight=25.0 * (opener_rate - 0.12) / 0.12,
                evidence=[s[:70] for s in opener_hits[:3]],
                suggestion="Cut most of them. Sequence usually carries the logic on its own.",
            )
        )

    # --- 4. uniform paragraphs ----------------------------------------------
    # Judged on relative spread, not absolute: four paragraphs within a few words
    # of each other is ordinary writing, whereas eight in lockstep is a pattern.
    para_lengths = [len(_WORD.findall(p)) for p in paragraphs]
    para_mean = statistics.fmean(para_lengths) if para_lengths else 0.0
    para_stdev = statistics.pstdev(para_lengths) if len(para_lengths) > 2 else 0.0
    para_cv = (para_stdev / para_mean) if para_mean else 1.0
    if len(paragraphs) >= 5 and para_cv < 0.30:
        findings.append(
            Finding(
                kind="uniform paragraphs",
                detail=f"{len(paragraphs)} paragraphs of near-identical length "
                f"(st.dev. {para_stdev:.1f} words, variation {para_cv:.2f})",
                weight=6.0,
                suggestion="Real sections have a short paragraph where the point is simple "
                "and a long one where the argument is hard.",
            )
        )

    # --- 5. evidence-free intensifiers --------------------------------------
    intensifier_hits = [
        w for w in EMPTY_INTENSIFIERS
        if re.search(rf"\b{re.escape(w)}\b", text, flags=re.IGNORECASE)
    ]
    if len(intensifier_hits) >= 3:
        findings.append(
            Finding(
                kind="assertive filler",
                detail=f"{len(intensifier_hits)} distinct importance-asserting adjectives",
                weight=2.0 * len(intensifier_hits),
                evidence=intensifier_hits[:5],
                suggestion="Show the number or the consequence instead of labelling it important.",
            )
        )

    # --- 6. rule-of-three lists ---------------------------------------------
    triples = re.findall(r"\b\w+, \w+,? and \w+\b", text)
    if len(triples) >= 4:
        findings.append(
            Finding(
                kind="triadic lists",
                detail=f"{len(triples)} 'X, Y, and Z' constructions",
                weight=1.5 * len(triples),
                evidence=triples[:3],
                suggestion="Vary list length; two or four items read as human.",
            )
        )

    # --- 7. repeated sentence openings ---------------------------------------
    first_words = [s.split()[0].lower() for s in sentences if s.split()]
    repeats = {w: first_words.count(w) for w in set(first_words) if first_words.count(w) >= 4}
    if repeats:
        worst = max(repeats.items(), key=lambda kv: kv[1])
        findings.append(
            Finding(
                kind="repeated openings",
                detail=f"{worst[1]} sentences begin with '{worst[0]}'",
                weight=2.0 * worst[1],
                suggestion="Recast some of them to lead with the subject or the finding.",
            )
        )

    # --- 8. nominalisation density -------------------------------------------
    nominal = [w for w in words if re.search(r"(tion|ment|ance|ence|ity|ness)$", w.lower())]
    nominal_rate = len(nominal) / len(words) if words else 0.0
    if nominal_rate > 0.09:
        findings.append(
            Finding(
                kind="nominalisation",
                detail=f"{nominal_rate * 100:.1f}% of words are abstract nouns",
                weight=30.0 * (nominal_rate - 0.09),
                suggestion="Turn nouns back into verbs: 'the quantification of X was performed' "
                "→ 'we quantified X'.",
            )
        )

    # Cap each finding so no single signal can saturate the score and hide the rest.
    for finding in findings:
        finding.weight = min(finding.weight, _MAX_FINDING_WEIGHT)

    # Lexical findings scale with how much text there is, so they are scored as a
    # density. Structural findings describe the document once, so they are added
    # flat. Each half is capped, which keeps the two comparable.
    lexical = sum(f.weight for f in findings if f.kind not in _STRUCTURAL_KINDS)
    structural = sum(f.weight for f in findings if f.kind in _STRUCTURAL_KINDS)

    lexical_density = lexical / max(len(words) / 100.0, 1.0)
    score = min(100.0, min(70.0, lexical_density * 6.0) + min(30.0, structural))

    return DeAIReport(
        score=score,
        findings=findings,
        metrics={
            "words": float(len(words)),
            "sentences": float(len(sentences)),
            "paragraphs": float(len(paragraphs)),
            "mean_sentence_len": mean_len,
            "stdev_sentence_len": stdev_len,
            "burstiness": burstiness,
            "opener_rate": opener_rate,
            "paragraph_stdev": para_stdev,
            "nominalisation_rate": nominal_rate,
        },
    )


def _readable(pattern: str) -> str:
    """Turn a regex back into something a human can read in a report."""
    text = pattern.replace(r"\b", "").replace(r"(?:", "(").replace("[^.]{1,80}", " ... ")
    return re.sub(r"\\", "", text)


def _context(text: str, pattern: str, width: int = 60) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    return "…" + " ".join(text[start:end].split()) + "…"
