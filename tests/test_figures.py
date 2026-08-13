"""Figure planning.

The mechanical guarantee here is the same one citations get: a panel that cites
a column nobody has is the figure equivalent of a fabricated reference — it
looks actionable and is not.
"""

import pytest

from mra import figures
from mra.config import Config
from mra.pubmed import Article
from mra.schemas import FigurePanel, FigurePlan, FigureSet
from mra.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "kb.db") as s:
        s.add_articles([
            Article(pmid="31234567", title="Macrophage TGF-beta1 drives portal fibrosis",
                    abstract="Myeloid Tgfb1 deletion reduced Sirius red area by 38%.",
                    journal_abbrev="Hepatology", year="2019"),
        ])
        s.save_journal("hepatology", {"journal": "Hepatology",
                                      "figure_narrative": "Each figure closes one question."})
        yield s


@pytest.fixture
def data_file(tmp_path):
    path = tmp_path / "counts.csv"
    path.write_text(
        "sample_id,stage,trem2_pos_per_mm2,asma_area_pct\n"
        + "\n".join(f"S{i},F{i % 4},{i * 3.1},{i * 0.7}" for i in range(30)),
        encoding="utf-8",
    )
    return path


def _panel(label="A", source="counts.csv: trem2_pos_per_mm2, stage"):
    return FigurePanel(label=label, claim="c", shows="s", plot_type="p",
                       source=source, caveats=[])


def _set(**overrides):
    payload = dict(
        figures=[FigurePlan(number=1, handle="h", argument="a", panels=[_panel()],
                            caption="cap", missing=[])],
        story="one figure",
        caption_overclaims=["'drives' over a correlation panel"],
        better_as_table=["the antibody list"],
        supplementary=["QC panel"],
    )
    payload.update(overrides)
    return FigureSet(**payload)


class StubLLM:
    def __init__(self, result):
        self.result = result
        self.system = None

    def parse(self, system, messages, schema, **kwargs):
        self.system = system
        return self.result

    def text(self, *a, **k):  # pragma: no cover
        raise AssertionError("figure planning should not call text()")


class TestPlanning:
    def test_the_journal_figure_convention_is_used(self, store, data_file, tmp_path):
        """This is the one thing JournalProfile.figure_narrative was extracted
        for, and it went unused until now."""
        llm = StubLLM(_set())
        figures.plan(Config(workspace=tmp_path), store, llm, [data_file], journal="Hepatology")
        assert "Each figure closes one question." in "\n".join(llm.system)

    def test_no_journal_still_works(self, store, data_file, tmp_path):
        llm = StubLLM(_set())
        figures.plan(Config(workspace=tmp_path), store, llm, [data_file])
        prompt = "\n".join(llm.system)
        assert "No journal chosen yet" in prompt

    def test_the_data_columns_reach_the_prompt(self, store, data_file, tmp_path):
        llm = StubLLM(_set())
        figures.plan(Config(workspace=tmp_path), store, llm, [data_file])
        assert "trem2_pos_per_mm2" in "\n".join(llm.system)

    def test_the_corpus_reaches_the_prompt(self, store, data_file, tmp_path):
        """Which comparison is actually new depends on what is already published."""
        llm = StubLLM(_set())
        figures.plan(Config(workspace=tmp_path), store, llm, [data_file],
                     notes="macrophage portal fibrosis")
        assert "[PMID:31234567]" in "\n".join(llm.system)

    def test_figures_come_back_in_reading_order(self, store, data_file, tmp_path):
        out_of_order = _set(figures=[
            FigurePlan(number=3, handle="c", argument="a", panels=[_panel()],
                       caption="x", missing=[]),
            FigurePlan(number=1, handle="a", argument="a", panels=[_panel()],
                       caption="x", missing=[]),
        ])
        result = figures.plan(Config(workspace=tmp_path), store,
                              StubLLM(out_of_order), [data_file])
        assert [f.number for f in result.figures] == [1, 3]


class TestSourceChecking:
    def test_a_panel_citing_a_real_column_passes(self, data_file):
        assert figures.unsourced_panels(_set(), [data_file]) == []

    def test_a_panel_citing_an_invented_column_is_flagged(self, data_file):
        invented = _set(figures=[FigurePlan(
            number=1, handle="h", argument="a",
            panels=[_panel(source="counts.csv: hydroxyproline_ug_per_g")],
            caption="x", missing=[])])
        flagged = figures.unsourced_panels(invented, [data_file])
        assert flagged and "Fig 1A" in flagged[0]

    def test_an_honest_admission_is_not_flagged(self, data_file):
        """Saying so is the behaviour we want; flagging it would punish honesty."""
        honest = _set(figures=[FigurePlan(
            number=1, handle="h", argument="a",
            panels=[_panel(source="not in the supplied data — needs a new stain")],
            caption="x", missing=["the stain"])])
        assert figures.unsourced_panels(honest, [data_file]) == []

    def test_a_missing_file_does_not_crash_the_check(self, tmp_path):
        assert figures.unsourced_panels(_set(), [tmp_path / "gone.csv"]) == []

    def test_naming_only_the_file_is_accepted(self, data_file):
        """A panel can legitimately use the whole table."""
        whole = _set(figures=[FigurePlan(
            number=1, handle="h", argument="a",
            panels=[_panel(source="counts.csv, all rows")],
            caption="x", missing=[])])
        assert figures.unsourced_panels(whole, [data_file]) == []

    def test_a_prose_source_with_no_column_is_flagged(self, data_file):
        vague = _set(figures=[FigurePlan(
            number=1, handle="h", argument="a",
            panels=[_panel(source="the immunofluorescence quantification")],
            caption="x", missing=[])])
        assert figures.unsourced_panels(vague, [data_file])


class TestRendering:
    def test_panels_and_caption_are_shown(self):
        text = figures.format_figures(_set())
        assert "Figure 1 — h" in text
        assert "图注草稿" in text

    def test_caption_overclaims_are_prominent(self):
        text = figures.format_figures(_set())
        assert "撑不住的图注" in text
        assert "drives" in text

    def test_missing_data_is_listed_per_figure(self):
        needy = _set(figures=[FigurePlan(
            number=1, handle="h", argument="a", panels=[_panel()],
            caption="x", missing=["a vehicle arm"])])
        assert "a vehicle arm" in figures.format_figures(needy)

    def test_empty_optional_sections_are_omitted(self):
        bare = _set(caption_overclaims=[], better_as_table=[], supplementary=[])
        text = figures.format_figures(bare)
        assert "撑不住的图注" not in text
        assert "建议放补充材料" not in text
