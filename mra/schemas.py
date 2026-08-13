"""Structured-output schemas.

These are the contract between the model and the rest of the program. Keeping
them flat and constraint-free matters: structured outputs do not support
recursive schemas or numeric bounds, so ranges are documented in the field
description and clamped in code instead.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryPlan(BaseModel):
    """A PubMed search strategy derived from a clinical question."""

    pubmed_query: str = Field(
        description="A valid PubMed query using field tags and boolean operators, "
        "e.g. (\"Liver Cirrhosis\"[MeSH] OR fibrosis[tiab]) AND macrophage[tiab]"
    )
    mesh_terms: list[str] = Field(description="MeSH descriptors central to the question")
    free_text_terms: list[str] = Field(description="Synonyms and author keywords worth including")
    alternate_queries: list[str] = Field(
        description="1-3 narrower or broader variants to try if the main query returns too "
        "few or too many records"
    )
    rationale: str = Field(description="Why this strategy covers the question")


class SearchTerms(BaseModel):
    """English keywords standing in for a question the index cannot match.

    The knowledge base is English and searched literally, so a Chinese question
    scores zero on every paper in it.
    """

    terms: list[str] = Field(
        description="5-15 English search terms a paper on this topic would "
        "contain, including gene/protein symbols and common synonyms. Empty if "
        "the question has no searchable scientific subject."
    )


class LitCard(BaseModel):
    """Structured summary of one paper (Clause 1.3)."""

    pmid: str
    scientific_question: str = Field(description="The question the paper set out to answer")
    key_findings: list[str] = Field(description="Concrete results, with effect direction")
    methods: list[str] = Field(description="Models, assays, cohorts and key techniques used")
    novelty_claim: str = Field(description="What the authors claim is new")
    limitations: list[str] = Field(
        description="Limitations, including ones the authors do not acknowledge"
    )
    mechanism_keywords: list[str] = Field(description="Pathways, molecules, cell types")
    clinical_relevance: str = Field(description="How this connects to a patient-level problem")
    evidence_strength: int = Field(
        description="1-5. 1 = single in-vitro observation, 3 = solid animal or "
        "observational human data, 5 = randomised or multi-cohort human evidence"
    )


class Hypothesis(BaseModel):
    """A working hypothesis with its evidential basis (Clause 2)."""

    title: str = Field(description="One line, specific enough to be falsifiable")
    statement: str = Field(description="The hypothesis in 2-4 sentences")
    knowledge_gap: str = Field(description="The gap in the literature this addresses")
    rationale: str = Field(
        description="Why this is plausible, citing evidence as [PMID:12345] inline"
    )
    novelty_type: str = Field(
        description="One of: new mechanism, new context, new target, new method, "
        "reconciles conflicting findings, translational extension"
    )
    novelty_level: int = Field(
        description="1-5. 1 = incremental confirmation, 5 = challenges a prevailing model"
    )
    mechanism_chain: list[str] = Field(
        description="The proposed causal chain as ordered steps, each independently testable"
    )
    testable_predictions: list[str] = Field(
        description="Predictions that would be false if the hypothesis is wrong"
    )
    key_experiments: list[str] = Field(description="Experiments that would discriminate best")
    supporting_pmids: list[str] = Field(description="PMIDs supporting this, from the knowledge base")
    challenging_pmids: list[str] = Field(description="PMIDs that complicate or contradict it")
    open_weaknesses: list[str] = Field(description="Where this is most likely to fail")


class SectionStyle(BaseModel):
    name: str = Field(description="Section name as the journal labels it")
    typical_paragraphs: str = Field(description="Typical paragraph count and what each does")
    opening_move: str = Field(description="How the section characteristically opens")
    notes: str = Field(description="Anything distinctive about how this journal handles it")


class JournalProfile(BaseModel):
    """An internalised model of one journal's format and voice (Clause 3.2)."""

    journal: str
    scope_summary: str = Field(description="What this journal publishes and what it rejects")
    article_structure: list[SectionStyle]
    title_conventions: str = Field(description="Title grammar: declarative vs descriptive, length")
    abstract_shape: str = Field(description="Structured or narrative; sentence budget per move")
    introduction_arc: str = Field(description="The narrative sequence from problem to gap to aim")
    results_ordering: str = Field(description="How results are sequenced and tied to figures")
    discussion_norms: str = Field(description="Claim strength, limitation handling, closing move")
    figure_narrative: str = Field(description="How figures carry the argument")
    voice_notes: list[str] = Field(
        description="Concrete grammatical habits: tense, voice, hedging, person"
    )
    common_rejection_reasons: list[str] = Field(
        description="What this journal typically rejects for, at the level of a specific paper"
    )


class DimensionScore(BaseModel):
    dimension: str
    score: int = Field(description="1-5, where 3 is the bar for a solid specialist journal")
    justification: str
    gaps: list[str] = Field(description="Specifically what is missing to reach the next level")


class FitAssessment(BaseModel):
    """Data-to-journal fit assessment (Clause 4.2)."""

    target_journal: str
    dimensions: list[DimensionScore] = Field(
        description="One entry each for: novelty, mechanism depth, clinical relevance, "
        "data robustness, story coherence"
    )
    overall_verdict: str = Field(
        description="One of: ready to write, needs targeted additions, needs a different "
        "target journal, needs a new experimental direction"
    )
    strongest_claim: str = Field(description="The most defensible headline claim these data support")
    overclaims: list[str] = Field(description="Claims the data would not survive review on")
    suggested_experiments: list[str] = Field(
        description="Ordered by how much each raises the fit score per unit of effort"
    )
    alternative_journals: list[str] = Field(description="Better-matched targets if fit is weak")


class JournalCandidate(BaseModel):
    """One journal proposed as a target for the work in hand."""

    journal: str = Field(
        description="The journal's full name as it would be typed into a submission "
        "system, e.g. 'Journal of Hepatology', not 'J Hepatol'"
    )
    tier: str = Field(
        description="One of: reach, realistic, safe. 'realistic' means submission "
        "today has a fair chance; 'reach' means it needs the additions listed in gaps"
    )
    why: str = Field(
        description="Why this journal specifically, in terms of its scope and what it "
        "has published recently — not generic praise of the work"
    )
    gaps: list[str] = Field(
        description="What this journal in particular would demand that the data do not "
        "yet show. Empty if the work clears its bar as it stands."
    )
    odds: str = Field(
        description="Rough chance of surviving to review as the data stand, as a short "
        "phrase with a caveat, e.g. 'moderate, if the human cohort holds up'"
    )


class JournalRecommendation(BaseModel):
    """Ranked journal targets for a body of data (Clause 4.2, no target given).

    The dimension scores are the same five as FitAssessment, but scored against
    the field rather than against one journal's bar — the whole point is that no
    target has been chosen yet.
    """

    dimensions: list[DimensionScore] = Field(
        description="One entry each for: novelty, mechanism depth, clinical relevance, "
        "data robustness, story coherence"
    )
    strongest_claim: str = Field(description="The most defensible headline claim these data support")
    overclaims: list[str] = Field(description="Claims the data would not survive review on")
    candidates: list[JournalCandidate] = Field(
        description="4-6 journals, best match first, spanning reach through safe"
    )
    suggested_experiments: list[str] = Field(
        description="Ordered by how much each raises the reachable tier per unit of effort"
    )
    reality_check: str = Field(
        description="The one thing the researcher is most likely to be wrong about in "
        "their own read of this data's ceiling"
    )


class ReviewSection(BaseModel):
    """One section of a review, with the papers it is answerable from."""

    heading: str = Field(description="The section heading as it will appear")
    argument: str = Field(
        description="What this section establishes, in one or two sentences. Not a "
        "topic label — the claim the section exists to make."
    )
    identifiers: list[str] = Field(
        description="PMIDs or local: ids from the knowledge base that this section "
        "must draw on. Only ids that appeared in the retrieved context."
    )


class ReviewOutline(BaseModel):
    """The plan for a review, produced before any of it is written.

    Written as a separate step so the researcher can correct the structure for a
    few cents rather than after paying for several thousand words, and so each
    section can be written against its own papers instead of the whole corpus.
    """

    title: str = Field(description="A specific title, not a topic label")
    scope: str = Field(
        description="What this review covers and — explicitly — what it does not, "
        "given the papers actually in the knowledge base"
    )
    sections: list[ReviewSection] = Field(
        description="4-8 sections in reading order, each with a distinct argument"
    )
    controversies: list[str] = Field(
        description="Points where the stored papers disagree with each other. Name "
        "the disagreeing ids. Empty only if the corpus genuinely contains no conflict."
    )
    gaps: list[str] = Field(
        description="Questions this literature leaves open — the material for the "
        "closing section and for the researcher's own next study"
    )
    unsupported: list[str] = Field(
        description="Claims a reader would expect a review of this topic to make "
        "that the stored papers cannot support. Better stated here than written."
    )


class WritingFingerprint(BaseModel):
    """The researcher's own writing habits, learned from their prior text."""

    mean_sentence_length: float
    sentence_length_variability: str = Field(description="high, moderate, or low, with a note")
    preferred_connectives: list[str]
    hedging_style: str = Field(description="How this author signals uncertainty")
    voice_preference: str = Field(description="Active/passive balance and person")
    paragraph_habit: str = Field(description="Typical paragraph length and internal structure")
    recurring_phrases: list[str] = Field(description="Phrases the author reuses across papers")
    distinctive_traits: list[str] = Field(
        description="What makes this prose recognisably theirs rather than generic"
    )


class LocalArticleMeta(BaseModel):
    """Bibliographic metadata read off the front matter of a local document.

    Every field is allowed to be empty. A working paper, a thesis chapter or a
    badly-extracted scan genuinely may not carry a journal or a year, and an
    invented one is worse than a blank.
    """

    title: str = Field(description="The paper's title, or empty if not determinable")
    authors: list[str] = Field(
        description="Author surnames with initials, e.g. 'Chen W'. Empty list if unreadable."
    )
    journal: str = Field(description="Journal or preprint server; empty if absent")
    year: str = Field(description="Four-digit publication year; empty if absent")
    doi: str = Field(description="DOI without the https://doi.org/ prefix; empty if absent")
    pmid: str = Field(description="PMID if the document prints one; otherwise empty")
    keywords: list[str] = Field(description="Author keywords or MeSH-like terms, if listed")
    document_type: str = Field(
        description="One of: research article, review, preprint, thesis, protocol, "
        "conference abstract, other"
    )


class BriefImpact(BaseModel):
    """How one newly-arrived paper bears on the researcher's current hypothesis."""

    identifier: str = Field(description="The PMID or local: id of the paper being judged")
    stance: str = Field(
        description="One of: weakens, supports, reframes, unrelated. Use 'weakens' when "
        "the finding would make a reviewer doubt the hypothesis, 'reframes' when it "
        "neither supports nor contradicts but changes what the right question is."
    )
    reason: str = Field(
        description="One or two sentences naming the specific finding that drives the "
        "stance, with its effect size or direction where the paper reports one"
    )
    action: str = Field(
        description="What the researcher should do about it, or empty when nothing is "
        "needed. Be concrete: which experiment, which claim to soften, which paper to read."
    )
