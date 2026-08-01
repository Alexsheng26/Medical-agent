"""End-to-end tests for the commands that run without an API key."""

import json
from pathlib import Path

import pytest

from mra import cli, prompts
from mra.config import Config
from mra.pubmed import Article
from mra.store import Store


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / ".mra"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return ws


def run(args, workspace):
    return cli.main(["--workspace", str(workspace), *args])


def test_init_creates_workspace(workspace, capsys):
    assert run(["init", "--email", "a@b.org"], workspace) == 0
    assert (workspace / "mra.config.json").exists()
    assert (workspace / "knowledge.db").exists()

    saved = json.loads((workspace / "mra.config.json").read_text())
    assert saved["ncbi_email"] == "a@b.org"
    assert "ncbi_api_key" not in saved, "secrets must not be written to disk"


def test_status_on_empty_workspace(workspace, capsys):
    assert run(["status"], workspace) == 0
    out = capsys.readouterr().out
    assert "Articles      0" in out


def test_guide_prints_workflow(workspace, capsys):
    assert run(["guide"], workspace) == 0
    assert "科研中间体" in capsys.readouterr().out


def test_lint_runs_without_api_key(workspace, tmp_path, capsys):
    target = tmp_path / "draft.md"
    target.write_text(
        "In the realm of fibrosis, it is worth noting that macrophages play a "
        "crucial role. Furthermore, this cannot be overstated.",
        encoding="utf-8",
    )
    assert run(["lint", str(target)], workspace) == 0
    out = capsys.readouterr().out
    assert "AI-tell lint score" in out
    assert "stock phrase" in out


def test_refs_flags_fabricated_citation(workspace, tmp_path, capsys):
    run(["init"], workspace)
    with Store(workspace / "knowledge.db") as store:
        store.add_articles([Article(pmid="31234567", title="Real paper", year="2019")])

    target = tmp_path / "draft.md"
    target.write_text("Real [PMID:31234567] and fake [PMID:99999999].", encoding="utf-8")

    # Non-zero exit so this can gate a pre-submission script.
    assert run(["refs", str(target)], workspace) == 1
    assert "99999999" in capsys.readouterr().out


def test_refs_passes_when_all_citations_resolve(workspace, tmp_path):
    run(["init"], workspace)
    with Store(workspace / "knowledge.db") as store:
        store.add_articles([Article(pmid="31234567", title="Real paper", year="2019")])

    target = tmp_path / "draft.md"
    target.write_text("Supported [PMID:31234567].", encoding="utf-8")
    assert run(["refs", str(target), "--list"], workspace) == 0


class TestImport:
    """The offline path into the knowledge base, for restricted networks."""

    FIXTURE = Path(__file__).parent / "fixtures" / "efetch_sample.xml"

    def test_imports_without_api_key_or_network(self, workspace, capsys):
        run(["init"], workspace)
        assert run(["import", str(self.FIXTURE), "--topic", "fibrosis"], workspace) == 0

        out = capsys.readouterr().out
        assert "Read 3; 2 new, 1 skipped" in out

        with Store(workspace / "knowledge.db") as store:
            assert store.count_articles() == 2
            assert store.get_article("31234567").journal_abbrev == "Hepatology"

    def test_reimport_does_not_duplicate(self, workspace):
        run(["init"], workspace)
        run(["import", str(self.FIXTURE)], workspace)
        run(["import", str(self.FIXTURE)], workspace)

        with Store(workspace / "knowledge.db") as store:
            assert store.count_articles() == 2

    def test_multiple_files_in_one_call(self, workspace, tmp_path):
        run(["init"], workspace)
        second = tmp_path / "more.xml"
        second.write_text(
            self.FIXTURE.read_text(encoding="utf-8").replace("31234567", "40000001"),
            encoding="utf-8",
        )
        run(["import", str(self.FIXTURE), str(second)], workspace)

        with Store(workspace / "knowledge.db") as store:
            assert store.count_articles() == 3

    def test_missing_file_reports_clearly(self, workspace, capsys, tmp_path):
        run(["init"], workspace)
        assert run(["import", str(tmp_path / "nope.xml")], workspace) == 1
        assert "No such file" in capsys.readouterr().err

    def test_imported_records_are_searchable(self, workspace):
        run(["init"], workspace)
        run(["import", str(self.FIXTURE)], workspace)

        with Store(workspace / "knowledge.db") as store:
            hits = store.search("macrophage TGF portal fibrosis")
            assert hits[0][0].pmid == "31234567"


def test_memory_refresh_without_api_key(workspace, capsys):
    run(["init"], workspace)
    with Store(workspace / "knowledge.db") as store:
        store.add_articles(
            [Article(pmid="1", title="t", mesh_terms=["Liver Cirrhosis", "Macrophages"])]
        )
    assert run(["memory", "--refresh"], workspace) == 0
    assert "liver cirrhosis" in capsys.readouterr().out


def test_export_writes_json(workspace, tmp_path):
    run(["init"], workspace)
    with Store(workspace / "knowledge.db") as store:
        store.add_articles([Article(pmid="1", title="A paper", year="2020")])

    out = tmp_path / "export.json"
    assert run(["export", "-o", str(out)], workspace) == 0
    data = json.loads(out.read_text())
    assert data["articles"][0]["article"]["pmid"] == "1"


def test_missing_api_key_gives_a_useful_message(workspace, capsys):
    run(["init"], workspace)
    assert run(["chat", "hello"], workspace) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_unknown_journal_is_reported_clearly(workspace, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    run(["init"], workspace)
    data = tmp_path / "d.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")

    assert run(["assess", str(data), "--journal", "Nonexistent"], workspace) == 1
    err = capsys.readouterr().err
    assert "No profile for" in err
    assert "journal add" in err, "the error should name the fix"


def test_no_command_prints_help(workspace, capsys):
    assert run([], workspace) == 0
    assert "usage:" in capsys.readouterr().out


class TestPrompts:
    def test_every_prompt_loads(self):
        for name in ["core", "query_plan", "extract", "dialogue", "hypothesis",
                     "proposal", "journal_profile", "evaluate", "draft",
                     "nativize", "deai_rewrite", "fingerprint"]:
            assert prompts.load(name), f"{name} is empty"

    def test_placeholders_are_substituted(self):
        text = prompts.load("dialogue", context="MY-CONTEXT")
        assert "MY-CONTEXT" in text
        assert "{context}" not in text

    def test_core_switches_language(self):
        assert "中文" in prompts.core("zh")
        assert "中文" not in prompts.core("en")

    def test_missing_prompt_lists_alternatives(self):
        with pytest.raises(FileNotFoundError, match="Available:"):
            prompts.load("does_not_exist")


class TestConfig:
    def test_env_overrides_file(self, tmp_path, monkeypatch):
        ws = tmp_path / ".mra"
        Config(workspace=ws, model="from-file").save()
        monkeypatch.setenv("MRA_MODEL", "from-env")
        assert Config.load(ws).model == "from-env"

    def test_defaults_to_opus_5(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MRA_MODEL", raising=False)
        assert Config.load(tmp_path / ".mra").model == "claude-opus-5"

    def test_workspace_paths(self, tmp_path):
        cfg = Config(workspace=tmp_path / "ws")
        assert cfg.db_path == tmp_path / "ws" / "knowledge.db"
        assert cfg.drafts_dir == tmp_path / "ws" / "drafts"
