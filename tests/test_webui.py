"""The web layer, exercised without a browser.

The part worth testing hard is `build_argv`. It is the boundary between a page
loaded in a tab and a process on the researcher's machine, and the failure it
guards against is not a crash — it is a request that quietly does more than the
interface offers.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from mra import webui
from mra.config import Config


class TestBuildArgv:
    def test_bare_command(self):
        assert webui.build_argv("status", {}) == ["status"]

    def test_nested_command_uses_its_prefix(self):
        assert webui.build_argv("journal_list", {}) == ["journal", "list"]

    def test_positional_then_options(self):
        argv = webui.build_argv("search", {"topic": "NASH fibrosis", "max": 40})
        assert argv == ["search", "NASH fibrosis", "--max=40"]

    def test_repeated_positional_stays_separate_arguments(self):
        argv = webui.build_argv("assess", {"data": ["a.csv", "b notes.md"]})
        assert argv == ["assess", "a.csv", "b notes.md"]

    def test_boolean_option_becomes_a_bare_flag(self):
        assert webui.build_argv("refs", {"file": "d.md", "list": True}) == [
            "refs", "d.md", "--list",
        ]

    def test_false_and_empty_are_dropped_rather_than_sent(self):
        argv = webui.build_argv("refs", {"file": "d.md", "list": False})
        assert argv == ["refs", "d.md"]

    def test_unknown_command_refused(self):
        with pytest.raises(ValueError, match="未知的命令"):
            webui.build_argv("rm", {})

    def test_undeclared_option_refused(self):
        """The page can only ask for fields this module names."""
        with pytest.raises(ValueError, match="不接受参数"):
            webui.build_argv("status", {"workspace": "/etc"})

    def test_option_values_use_equals_so_a_leading_dash_cannot_become_a_flag(self):
        argv = webui.build_argv("assess", {"data": ["d.csv"], "notes": "-5% change"})
        assert "--notes=-5% change" in argv

    def test_positional_may_not_start_with_a_dash(self):
        with pytest.raises(ValueError, match="不能以 - 开头"):
            webui.build_argv("lint", {"file": "--help"})

    def test_repeated_positional_requires_at_least_one(self):
        with pytest.raises(ValueError, match="至少一个"):
            webui.build_argv("assess", {"data": []})

    def test_numbers_are_coerced_not_passed_through(self):
        argv = webui.build_argv("digest", {"limit": "7", "max_cost": "1.5"})
        assert "--limit=7" in argv and "--max-cost=1.5" in argv

    def test_a_null_byte_is_refused(self):
        with pytest.raises(ValueError):
            webui.build_argv("lint", {"file": "a\x00b"})

    def test_every_declared_flag_is_long_form(self):
        """A short flag cannot take `-o=value`, so the equals form would break."""
        for command, spec in webui.COMMANDS.items():
            for name, option in spec.options.items():
                assert option.flag.startswith("--"), f"{command}.{name} uses {option.flag}"


class TestCommandSurface:
    def test_every_command_exists_in_the_cli(self):
        """A button that maps onto no command fails only when someone clicks it."""
        from mra.cli import build_parser

        parser = build_parser()
        actions = [a for a in parser._actions if a.dest == "command"]
        known = set(actions[0].choices)

        for command, spec in webui.COMMANDS.items():
            name = spec.prefix[0] if spec.prefix else command
            assert name in known, f"web offers {command!r}, the CLI has no {name!r}"

    def test_costly_commands_are_all_real(self):
        assert webui.COSTLY <= set(webui.COMMANDS)

    def test_page_is_self_contained(self):
        """No CDN: this has to work on a machine behind a blocked network."""
        page = webui.read_index().decode("utf-8")
        assert "http://" not in page.replace("http://127.0.0.1", "")
        assert "https://" not in page
        assert "<title>" in page


class TestListing:
    def test_lists_a_directory(self, tmp_path):
        (tmp_path / "paper.pdf").write_text("x")
        (tmp_path / "sub").mkdir()
        result = webui.listing(tmp_path)
        names = [entry["name"] for entry in result["entries"]]
        assert names == ["sub", "paper.pdf"]  # directories first

    def test_hidden_entries_are_skipped(self, tmp_path):
        (tmp_path / ".mra").mkdir()
        (tmp_path / "data.csv").write_text("x")
        assert [e["name"] for e in webui.listing(tmp_path)["entries"]] == ["data.csv"]

    def test_a_file_path_falls_back_to_its_directory(self, tmp_path):
        target = tmp_path / "data.csv"
        target.write_text("x")
        assert webui.listing(target)["path"] == str(tmp_path.resolve())


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """One live server, driven over HTTP the way the page drives it."""
    from http.server import ThreadingHTTPServer

    workspace = tmp_path_factory.mktemp("ws") / ".mra"
    workspace.mkdir()

    class Bound(webui.Handler):
        pass

    Bound.token = "test-token"
    Bound.jobs = webui.Jobs()
    Bound.cfg = Config(workspace=workspace)

    httpd = ThreadingHTTPServer((webui.HOST, 0), Bound)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://{webui.HOST}:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class TestServer:
    def _get(self, url, token="test-token", origin=None):
        request = urllib.request.Request(url)
        if token:
            request.add_header("X-MRA-Token", token)
        if origin:
            request.add_header("Origin", origin)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=10)

    def test_the_page_itself_needs_no_token(self, server):
        """Otherwise the browser cannot load anything to type the token into."""
        response = self._get(server + "/", token=None)
        assert response.status == 200
        assert b"<title>" in response.read()

    def test_api_without_a_token_is_refused(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            self._get(server + "/api/state", token=None)
        assert caught.value.code == 403

    def test_api_with_a_wrong_token_is_refused(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            self._get(server + "/api/state", token="guess")
        assert caught.value.code == 403

    def test_a_cross_origin_request_is_refused_even_with_the_token(self, server):
        """A page on another site knowing the token still may not drive this."""
        with pytest.raises(urllib.error.HTTPError) as caught:
            self._get(server + "/api/state", origin="https://evil.example")
        assert caught.value.code == 403

    def test_token_may_travel_in_the_query_for_the_event_stream(self, server):
        """EventSource cannot set headers, so the stream URL carries it."""
        response = self._get(server + "/api/state?t=test-token", token=None)
        assert json.loads(response.read())["provider"]

    def test_state_reports_the_workspace(self, server):
        state = json.loads(self._get(server + "/api/state").read())
        assert state["workspace"].endswith(".mra")
        assert "digest" in state["costly"]

    def test_a_job_runs_and_streams_to_completion(self, server):
        """`guide` costs nothing and always prints, so it exercises the whole
        path — spawn, stream, exit code — without an API key."""
        request = urllib.request.Request(
            server + "/api/run",
            data=json.dumps({"command": "guide", "args": {}}).encode(),
            headers={"X-MRA-Token": "test-token", "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        job = json.loads(opener.open(request, timeout=10).read())["job"]

        stream = self._get(f"{server}/api/stream?job={job}&t=test-token", token=None)
        body = stream.read(200_000).decode("utf-8")
        assert "科研中间体" in body
        assert '"code": 0' in body

    def test_an_unknown_command_is_refused_before_anything_spawns(self, server):
        request = urllib.request.Request(
            server + "/api/run",
            data=json.dumps({"command": "rm", "args": {}}).encode(),
            headers={"X-MRA-Token": "test-token", "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with pytest.raises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=10)
        assert caught.value.code == 400


# --------------------------------------------------------------- in a browser

# The page is the one part of this project that only a browser can check: a
# typo'd element id or a stale function name is valid Python, valid HTML, and a
# blank screen. These run when Playwright and a Chromium build are present and
# skip otherwise, so nobody installing the tool needs either.

def _chromium() -> str:
    from pathlib import Path

    for candidate in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
        return str(candidate)
    pytest.skip("no chromium build available")


@pytest.fixture(scope="module")
def page(server):
    # Skipped inside the fixture, not at import: a module-level importorskip
    # would take the other thirty tests down with it on a machine that has no
    # browser, which is every machine this tool actually gets installed on.
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright not installed"
    )
    with playwright_api.sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=_chromium())
        tab = browser.new_page()
        failures: list[str] = []
        tab.on("pageerror", lambda error: failures.append(str(error)))
        tab.goto(server + "/?t=test-token", wait_until="networkidle")
        tab.wait_for_timeout(600)
        tab.failures = failures
        yield tab
        browser.close()


class TestInBrowser:
    def test_the_page_loads_without_a_script_error(self, page):
        assert page.failures == []
        assert page.title() == "科研中间体 MRA"

    def test_the_sidebar_lists_every_task(self, page):
        assert page.locator("nav button").count() >= 15

    def test_the_header_shows_where_the_data_lives(self, page):
        assert page.inner_text("#ws").endswith(".mra")

    def test_switching_task_swaps_the_panel(self, page):
        page.get_by_role("button", name="评估数据").click()
        page.wait_for_timeout(250)
        assert page.inner_text("#panel h2") == "评估数据"
        assert page.failures == []

    def test_a_command_runs_from_the_page_and_reports_completion(self, page):
        page.get_by_role("button", name="流程速查").click()
        page.wait_for_timeout(250)
        page.get_by_role("button", name="开始").click()
        page.wait_for_selector("pre.out:not(:empty)", timeout=20_000)
        page.wait_for_timeout(1500)
        assert "科研中间体" in page.inner_text("pre.out")
        assert page.inner_text(".status") == "完成。"
        assert page.failures == []

    def test_the_file_picker_opens_and_lists(self, page, tmp_path_factory):
        page.get_by_role("button", name="引用核对").click()
        page.wait_for_timeout(250)
        page.get_by_role("button", name="选择文件…").click()
        page.wait_for_timeout(600)
        assert page.locator("#picker").is_visible()
        assert page.inner_text("#picker-where")
        page.locator("#picker-close").click()
        assert page.failures == []


# ------------------------------------------------- choosing papers in the page

CORPUS = __import__("pathlib").Path(__file__).resolve().parent.parent / "mra" / "examples" / "demo_corpus.xml"


def _seed(workspace):
    from mra.pubmed import parse_efetch_xml
    from mra.store import Store

    with Store(workspace / "knowledge.db") as store:
        store.add_articles(parse_efetch_xml(CORPUS.read_text(encoding="utf-8")))


def _workspace_of(server):
    from pathlib import Path

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    state = json.loads(opener.open(f"{server}/api/state?t=test-token", timeout=10).read())
    return Path(state["workspace"])


def _get_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(opener.open(url, timeout=10).read())


class TestArticlesEndpoint:
    def test_an_empty_project_lists_nothing_and_creates_no_database(self, tmp_path):
        """Opening the picker on a new project must not leave an empty library behind."""
        from http.server import ThreadingHTTPServer

        workspace = tmp_path / ".mra"
        workspace.mkdir()

        class Bound(webui.Handler):
            pass

        Bound.token, Bound.jobs, Bound.cfg = "t", webui.Jobs(), Config(workspace=workspace)
        httpd = ThreadingHTTPServer((webui.HOST, 0), Bound)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            assert _get_json(f"http://{webui.HOST}:{port}/api/articles?t=t") == {"articles": []}
            assert not (workspace / "knowledge.db").exists()
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_lists_the_library_with_whether_each_was_read(self, server):
        workspace = _workspace_of(server)
        _seed(workspace)
        from mra.store import Store

        with Store(workspace / "knowledge.db") as store:
            store.save_card("31234567", {"scientific_question": "q", "evidence_strength": 3})

        rows = _get_json(f"{server}/api/articles?t=test-token")["articles"]
        assert len(rows) == 8
        read = {row["id"]: row["read"] for row in rows}
        assert read["31234567"] is True and read["28001122"] is False

    def test_needs_the_token_like_everything_else(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            _get_json(f"{server}/api/articles")
        assert caught.value.code == 403


class TestPickingPapersInTheBrowser:
    def test_the_picker_lists_the_library(self, page, server):
        _seed(_workspace_of(server))
        page.get_by_role("button", name="异同比较").click()
        page.wait_for_selector(".article", timeout=10_000)
        assert page.locator(".article").count() == 8
        assert page.failures == []

    def test_comparing_one_paper_is_stopped_before_anything_runs(self, page, server):
        _seed(_workspace_of(server))
        page.get_by_role("button", name="异同比较").click()
        page.wait_for_selector(".article", timeout=10_000)
        page.locator(".article input").first.check()
        assert page.inner_text(".picked") == "已选 1 篇"

        messages = []
        page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
        page.get_by_role("button", name="开始").click()
        page.wait_for_timeout(300)
        assert messages == ["至少要选 2 篇，现在是 1 篇。"]
        assert page.inner_text(".status") == ""

    def test_a_mindmap_payload_is_drawn_as_a_tree(self, page):
        page.get_by_role("button", name="思维导图").click()
        page.wait_for_timeout(250)
        page.evaluate("""() => drawMindmap(document.querySelector('.mindmap'), {
            root: "r", multi: false, chars: 9, limit: 300, legend: {},
            nodes: [{id: "r", parent: "", text: "中心", source: ""},
                    {id: "1", parent: "r", text: "方法", source: ""},
                    {id: "1.1", parent: "1", text: "敲除小鼠", source: ""}]})""")
        assert page.locator(".mindmap").is_visible()
        assert page.locator(".mm .node").count() == 3
        assert page.inner_text(".mm .d0") == "中心"
        assert "共 9 字（上限 300）" in page.inner_text(".mindmap .meta")
        assert page.failures == []


class TestExportInBrowser:
    def test_exporting_with_nothing_ticked_writes_the_whole_library(self, page, server):
        workspace = _workspace_of(server)
        _seed(workspace)
        target = workspace.parent / "文献库.ris"
        target.unlink(missing_ok=True)

        page.get_by_role("button", name="导出文献").click()
        page.wait_for_selector(".article", timeout=10_000)
        page.get_by_role("button", name="开始").click()
        page.wait_for_function(
            "() => document.querySelector('.status')?.textContent === '完成。'", timeout=30_000
        )
        assert "已导出 8 篇" in page.inner_text("pre.out")
        assert target.read_text(encoding="utf-8").count("ER  - ") == 8
        assert page.failures == []
