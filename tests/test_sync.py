"""Unattended sync.

Everything here runs offline against a stub PubMed and a stub model. The
properties under test are the ones that make a cron job safe to leave alone:
it must be idempotent, it must stop at a spend ceiling, and one dead watch must
not take the others down with it.
"""

import pytest

from mra import brief as brief_mod
from mra import pipeline
from mra.config import Config
from mra.pubmed import Article, PubMedError
from mra.schemas import LitCard
from mra.store import Store
from mra.usage import Ledger, Usage


@pytest.fixture
def cfg(tmp_path):
    config = Config(workspace=tmp_path / ".mra")
    config.ensure_workspace()
    return config


@pytest.fixture
def store(cfg):
    with Store(cfg.db_path) as s:
        yield s


def article(pmid, title="A paper", abstract="Some findings about fibrosis."):
    return Article(pmid=pmid, title=title, abstract=abstract, year="2024")


class StubPubMed:
    """Returns a fixed result set, or raises for a named query."""

    def __init__(self, results, fail_on: str = ""):
        self.results = results
        self.fail_on = fail_on
        self.queries = []

    def search_and_fetch(self, query, **kwargs):
        self.queries.append(query)
        if self.fail_on and self.fail_on in query:
            raise PubMedError("network unreachable")
        return list(self.results)


class StubLLM:
    """Records calls and bills the ledger a fixed amount per call."""

    def __init__(self, ledger=None, cost_per_call_tokens=0):
        self.ledger = ledger
        self.parse_calls = 0
        self.cost_per_call_tokens = cost_per_call_tokens

    def parse(self, system, messages, schema, **kwargs):
        self.parse_calls += 1
        if self.ledger is not None and self.cost_per_call_tokens:
            self.ledger.record(Usage(calls=1, output_tokens=self.cost_per_call_tokens))
        if schema is LitCard:
            pmid = messages[0]["content"].split("PMID: ")[1].split("\n")[0]
            return LitCard(
                pmid=pmid, scientific_question="q", key_findings=["f"], methods=["m"],
                novelty_claim="n", limitations=["l"], mechanism_keywords=["k"],
                clinical_relevance="c", evidence_strength=3,
            )
        raise AssertionError(f"unexpected schema {schema}")

    def text(self, *a, **k):  # pragma: no cover
        raise AssertionError("sync should not call text()")


@pytest.fixture
def patched_pubmed(monkeypatch):
    def install(stub):
        monkeypatch.setattr(pipeline, "PubMed", lambda **kwargs: stub)
        return stub

    return install


class TestSync:
    def test_no_watches_is_reported_not_silently_successful(self, cfg, store):
        result = pipeline.sync(cfg, store, StubLLM())
        assert not result.ok
        assert any("No watches" in e for e in result.errors)

    def test_fetches_and_extracts_new_records(self, cfg, store, patched_pubmed):
        patched_pubmed(StubPubMed([article("1"), article("2")]))
        store.add_watch("nash", "fibrosis[MeSH]", "NASH")
        llm = StubLLM()

        result = pipeline.sync(cfg, store, llm)

        assert result.watches_run == 1
        assert result.new_ids == ["1", "2"]
        assert result.digested == 2
        assert llm.parse_calls == 2
        assert result.ok

    def test_is_idempotent(self, cfg, store, patched_pubmed):
        """The property that makes it safe on a cron: running twice must not
        duplicate records or re-extract what is already done."""
        patched_pubmed(StubPubMed([article("1"), article("2")]))
        store.add_watch("nash", "fibrosis[MeSH]")

        first = pipeline.sync(cfg, store, StubLLM())
        second_llm = StubLLM()
        second = pipeline.sync(cfg, store, second_llm)

        assert first.new_ids == ["1", "2"]
        assert second.new_ids == []
        assert second_llm.parse_calls == 0, "nothing new means no spend"
        assert store.count_articles() == 2

    def test_no_digest_skips_every_model_call(self, cfg, store, patched_pubmed):
        patched_pubmed(StubPubMed([article("1")]))
        store.add_watch("nash", "q")
        llm = StubLLM()

        result = pipeline.sync(cfg, store, llm, do_digest=False)

        assert result.new_ids == ["1"]
        assert result.digested == 0
        assert llm.parse_calls == 0

    def test_one_failing_watch_does_not_stop_the_others(self, cfg, store, patched_pubmed):
        patched_pubmed(StubPubMed([article("1")], fail_on="BROKEN"))
        store.add_watch("good", "fibrosis[MeSH]")
        store.add_watch("bad", "BROKEN query")

        result = pipeline.sync(cfg, store, StubLLM())

        assert result.watches_run == 1
        assert result.new_ids == ["1"], "the healthy watch still delivered"
        assert any("bad" in e for e in result.errors)
        assert not result.ok

    def test_watch_run_metadata_is_recorded(self, cfg, store, patched_pubmed):
        patched_pubmed(StubPubMed([article("1"), article("2")]))
        store.add_watch("nash", "q")

        pipeline.sync(cfg, store, StubLLM())

        watch = store.get_watch("nash")
        assert watch["last_added"] == 2
        assert watch["last_run_at"], "a run must leave a timestamp"

    def test_stored_query_is_replayed_verbatim(self, cfg, store, patched_pubmed):
        """No re-planning on an unattended run: it costs money and the search
        scope would drift without anyone noticing."""
        stub = patched_pubmed(StubPubMed([]))
        store.add_watch("nash", '("Liver Cirrhosis"[MeSH]) AND macrophage[tiab]')

        pipeline.sync(cfg, store, StubLLM())

        assert stub.queries == ['("Liver Cirrhosis"[MeSH]) AND macrophage[tiab]']


class TestSpendCeiling:
    def test_stops_at_the_ceiling(self, cfg, store, patched_pubmed):
        patched_pubmed(StubPubMed([article(str(i)) for i in range(10)]))
        store.add_watch("nash", "q")

        ledger = Ledger.load(cfg.usage_path, cfg.model)
        # 4000 output tokens on Opus 5 = $0.10 per call.
        llm = StubLLM(ledger=ledger, cost_per_call_tokens=4000)

        result = pipeline.sync(cfg, store, llm, max_cost=0.25)

        assert result.stopped_on_budget
        assert not result.ok
        assert llm.parse_calls < 10, "the ceiling must actually bite"
        assert llm.parse_calls >= 2

    def test_no_ceiling_processes_everything(self, cfg, store, patched_pubmed):
        patched_pubmed(StubPubMed([article(str(i)) for i in range(5)]))
        store.add_watch("nash", "q")
        llm = StubLLM(ledger=Ledger.load(cfg.usage_path, cfg.model), cost_per_call_tokens=4000)

        result = pipeline.sync(cfg, store, llm, max_cost=None)

        assert not result.stopped_on_budget
        assert llm.parse_calls == 5

    def test_interrupted_run_leaves_recoverable_state(self, cfg, store, patched_pubmed):
        """Stopping at the ceiling must not lose the fetched records — a re-run
        picks up the extraction where it left off."""
        patched_pubmed(StubPubMed([article(str(i)) for i in range(10)]))
        store.add_watch("nash", "q")
        ledger = Ledger.load(cfg.usage_path, cfg.model)

        pipeline.sync(cfg, store, StubLLM(ledger=ledger, cost_per_call_tokens=4000),
                      max_cost=0.25)

        assert store.count_articles() == 10, "fetched records are kept"
        assert 0 < store.count_cards() < 10
        # A fresh run with a fresh budget finishes the remainder.
        remaining = len(store.pmids_without_cards())
        pipeline.digest(cfg, store, StubLLM())
        assert store.count_cards() == 10 and remaining > 0


class TestBrief:
    def test_reports_when_nothing_is_new(self, cfg, store):
        path = brief_mod.write_brief(cfg, store, None, [])
        assert "没有新增文献" in path.read_text(encoding="utf-8")

    def test_lists_new_papers_strongest_evidence_first(self, cfg, store):
        store.add_articles([article("1", "Weak paper"), article("2", "Strong paper")])
        store.save_card("1", {"evidence_strength": 2, "key_findings": ["weak finding"]})
        store.save_card("2", {"evidence_strength": 5, "key_findings": ["strong finding"]})

        text = brief_mod.write_brief(cfg, store, None, ["1", "2"]).read_text(encoding="utf-8")
        assert text.index("Strong paper") < text.index("Weak paper")

    def test_errors_are_surfaced_at_the_top(self, cfg, store):
        text = brief_mod.write_brief(
            cfg, store, None, [], errors=["watch 'nash' failed: unreachable"]
        ).read_text(encoding="utf-8")
        assert "本次运行有错误" in text
        assert "unreachable" in text

    def test_no_hypothesis_means_no_invented_verdict(self, cfg, store):
        """With nothing to judge against, staying quiet beats making something up."""
        store.add_articles([article("1")])

        class ExplodingLLM:
            ledger = None

            def parse(self, *a, **k):
                raise AssertionError("must not judge impact without a hypothesis")

        text = brief_mod.write_brief(cfg, store, ExplodingLLM(), ["1"]).read_text(
            encoding="utf-8"
        )
        assert "对当前假说的影响" not in text

    def test_local_documents_render_with_their_own_marker(self, cfg, store):
        store.add_articles([article("local:a1b2c3d4", "My own PDF")])
        text = brief_mod.write_brief(cfg, store, None, ["local:a1b2c3d4"]).read_text(
            encoding="utf-8"
        )
        assert "LOCAL:a1b2c3d4" in text
