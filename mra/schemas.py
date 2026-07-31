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
