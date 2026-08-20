"""Several projects side by side."""

from __future__ import annotations

import pytest

from mra import projects
from mra.pubmed import Article
from mra.store import Store


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("MRA_ROOT", str(tmp_path))
    return tmp_path


def _make(root, name, articles=0, cards=0):
    workspace = root / name / ".mra" if name else root / ".mra"
    workspace.mkdir(parents=True)
    if articles:
        with Store(workspace / "knowledge.db") as store:
            store.add_articles([
                Article(pmid=str(30000000 + i), title=f"Paper {i}", abstract="Body.")
                for i in range(articles)
            ])
            for i in range(cards):
                store.save_card(str(30000000 + i), {
                    "pmid": str(30000000 + i), "scientific_question": "Q",
                    "key_findings": ["F"], "methods": ["M"], "novelty_claim": "N",
                    "limitations": ["L"], "mechanism_keywords": ["K"],
                    "clinical_relevance": "C", "evidence_strength": 3,
                })
    return workspace


class TestNames:
    @pytest.mark.parametrize("name", ["", "   ", ".", "..", "../escape", "a/b", "a\\b", "x" * 61])
    def test_dangerous_names_are_refused(self, name):
        """The web interface passes this straight from a text box."""
        with pytest.raises(ValueError):
            projects.safe_name(name)

    def test_chinese_names_are_fine(self):
        assert projects.safe_name(" 肝纤维化 ") == "肝纤维化"
        assert projects.safe_name("胰腺癌-张三") == "胰腺癌-张三"

    def test_a_name_cannot_escape_the_root(self, root):
        for attempt in ["../..", "a/../../b"]:
            with pytest.raises(ValueError):
                projects.workspace_for(attempt, root)

    def test_resolved_paths_stay_under_the_root(self, root):
        assert projects.workspace_for("肝纤维化", root).is_relative_to(root)


class TestDiscovery:
    def test_an_empty_root_has_no_projects(self, root):
        assert projects.discover(root) == []

    def test_a_bare_workspace_becomes_the_default_project(self, root):
        """Everyone's existing single-project layout has to keep working."""
        _make(root, "")
        found = projects.discover(root)
        assert [p.name for p in found] == [projects.DEFAULT_NAME]
        assert found[0].is_default

    def test_subfolders_with_a_workspace_are_projects(self, root):
        _make(root, "")
        _make(root, "肝纤维化")
        _make(root, "胰腺癌")
        assert [p.name for p in projects.discover(root)] == [
            projects.DEFAULT_NAME, "肝纤维化", "胰腺癌",
        ]

    def test_a_folder_without_a_workspace_is_not_a_project(self, root):
        """A data folder beside the projects is not an empty project."""
        (root / "我的PDF").mkdir()
        _make(root, "肝纤维化")
        assert [p.name for p in projects.discover(root)] == ["肝纤维化"]

    def test_hidden_folders_are_skipped(self, root):
        (root / ".venv" / ".mra").mkdir(parents=True)
        assert projects.discover(root) == []

    def test_counts_come_from_each_project_separately(self, root):
        _make(root, "肝纤维化", articles=5, cards=2)
        _make(root, "胰腺癌", articles=1)
        by_name = {p.name: p for p in projects.discover(root)}
        assert (by_name["肝纤维化"].articles, by_name["肝纤维化"].cards) == (5, 2)
        assert (by_name["胰腺癌"].articles, by_name["胰腺癌"].cards) == (1, 0)

    def test_a_broken_database_does_not_take_the_listing_down(self, root):
        """One corrupt project must not hide the other five."""
        workspace = _make(root, "坏了")
        (workspace / "knowledge.db").write_text("not a database")
        _make(root, "好的", articles=3)
        found = {p.name: p for p in projects.discover(root)}
        assert found["坏了"].articles == 0
        assert found["好的"].articles == 3


class TestCreate:
    def test_creates_the_workspace(self, root):
        workspace = projects.create("肝纤维化", root)
        assert workspace.is_dir()
        assert workspace == root / "肝纤维化" / ".mra"

    def test_refuses_to_clobber_an_existing_project(self, root):
        projects.create("肝纤维化", root)
        with pytest.raises(ValueError, match="已经存在"):
            projects.create("肝纤维化", root)


class TestListing:
    def test_empty_says_how_to_start(self, root):
        assert "还没有任何课题" in projects.format_list([])

    def test_marks_the_current_project(self, root):
        _make(root, "甲", articles=1)
        _make(root, "乙", articles=1)
        found = projects.discover(root)
        text = projects.format_list(found, current=root / "乙" / ".mra")
        rows = text.split("─" * 60)[1].split("\n\n")[0]
        marked = [line for line in rows.splitlines() if line.startswith("▸")]
        assert len(marked) == 1 and "乙" in marked[0]

    def test_flags_projects_with_unread_literature(self, root):
        """Importing and never digesting is the failure this tool invites."""
        _make(root, "读过了", articles=2, cards=2)
        _make(root, "没读", articles=2)
        text = projects.format_list(projects.discover(root))
        assert "没读" in text.split("导了文献但一篇都没读")[1]
        assert "读过了" not in text.split("导了文献但一篇都没读")[1]

    def test_no_nag_when_everything_is_read(self, root):
        _make(root, "读过了", articles=2, cards=2)
        assert "一篇都没读" not in projects.format_list(projects.discover(root))

    def test_columns_line_up_despite_chinese(self, root):
        _make(root, "肝纤维化", articles=1)
        header = next(
            line for line in projects.format_list(projects.discover(root)).splitlines()
            if "课题" in line and "文献" in line
        )
        assert projects._columns(header[: header.index("文献")]) == 22
