"""The evaluation harness itself.

Not the judgements it measures — those need an API key. These check that the
harness reports honestly: that a missed finding is reported as missed, that a
case which cannot run is not silently scored zero-out-of-zero, and that the
markers describing each planted defect actually correspond to a defect.
"""

from __future__ import annotations

import json

import pytest

from mra import evaluation


class TestCases:
    def test_cases_load(self):
        assert evaluation.load_cases()

    def test_every_case_names_a_real_command(self):
        from mra.cli import build_parser

        known = set(next(a for a in build_parser()._actions if a.dest == "command").choices)
        for case in evaluation.load_cases():
            assert case["command"] in known, case["id"]

    def test_every_case_says_why_it_exists(self):
        """A case nobody can explain is a case nobody will maintain."""
        for case in evaluation.load_cases():
            assert case.get("why"), case["id"]

    def test_every_expectation_has_markers_and_a_description(self):
        for case in evaluation.load_cases():
            assert case["expect"], case["id"]
            for expectation in case["expect"]:
                assert expectation["any"], f'{case["id"]}/{expectation["id"]}'
                assert expectation["what"], f'{case["id"]}/{expectation["id"]}'

    def test_case_ids_are_unique(self):
        ids = [case["id"] for case in evaluation.load_cases()]
        assert len(ids) == len(set(ids))

    def test_some_cases_need_no_model(self):
        """`mra eval --free-only` has to be runnable with no key at all."""
        assert any(not case["needs_model"] for case in evaluation.load_cases())

    def test_markers_are_not_so_short_they_match_anything(self):
        """Measured in display columns, not characters.

        Two Chinese characters carry about as much information as four Latin
        ones, so 套话 is a specific marker while "gut" is not — that one matched
        "gut microbiome" before this test existed.
        """
        def columns(text):
            return sum(2 if ord(char) > 0x2E80 else 1 for char in text)

        for case in evaluation.load_cases():
            for expectation in case["expect"]:
                for marker in expectation["any"]:
                    assert columns(marker) >= 4, f'{case["id"]}: {marker!r}'


class TestScoring:
    def _result(self, caught: list[bool]) -> evaluation.Result:
        return evaluation.Result(
            id="c", command="lint", why="w",
            checks=[evaluation.Check(f"e{i}", "what", value) for i, value in enumerate(caught)],
        )

    def test_counts_are_per_expectation(self):
        result = self._result([True, False, True])
        assert (result.caught, result.total) == (2, 3)

    def test_report_totals_across_cases(self):
        report = evaluation.Report(results=[self._result([True]), self._result([False, True])])
        assert (report.caught, report.total) == (2, 3)

    def test_a_missed_finding_is_listed_by_name(self):
        report = evaluation.Report(results=[self._result([True, False])])
        text = evaluation.format_report(report)
        assert "没抓到的" in text
        assert "抓到 1/2" in text

    def test_a_clean_run_lists_nothing_as_missed(self):
        report = evaluation.Report(results=[self._result([True, True])])
        assert "没抓到的" not in evaluation.format_report(report)

    def test_the_report_states_what_the_number_is_not(self):
        """Reporting recall of markers as a quality score would be dishonest,
        so the caveat travels with the number rather than living in a doc."""
        text = evaluation.format_report(evaluation.Report(results=[self._result([True])]))
        assert "不是" in text and "判断质量" in text

    def test_a_case_that_failed_to_run_says_so(self):
        result = self._result([False])
        result.exit_code = 1
        result.error = "No API credentials found"
        text = evaluation.format_report(evaluation.Report(results=[result]))
        assert "没跑起来" in text
        assert "No API credentials found" in text


class TestBaseline:
    def _report(self, caught: bool) -> evaluation.Report:
        return evaluation.Report(results=[evaluation.Result(
            id="c", command="digest", why="w",
            checks=[evaluation.Check("e0", "指出反向因果", caught)],
        )])

    def test_round_trips_through_json(self):
        report = self._report(True)
        restored = json.loads(json.dumps(evaluation.to_json(report)))
        assert restored["caught"] == 1

    def test_a_regression_is_called_out(self):
        was = evaluation.to_json(self._report(True))
        text = evaluation.format_report(self._report(False), baseline=was)
        assert "变差了" in text
        assert "1 → 0" in text

    def test_an_improvement_is_called_out(self):
        was = evaluation.to_json(self._report(False))
        text = evaluation.format_report(self._report(True), baseline=was)
        assert "变好了" in text

    def test_no_change_says_so(self):
        was = evaluation.to_json(self._report(True))
        text = evaluation.format_report(self._report(True), baseline=was)
        assert "持平" in text
        assert "变差了" not in text and "变好了" not in text


class TestRunSelection:
    def test_free_only_drops_the_paid_cases(self, monkeypatch):
        seen = []
        monkeypatch.setattr(evaluation, "_seed", lambda *a: None)
        monkeypatch.setattr(
            evaluation, "_run_case",
            lambda case, *a: (seen.append(case["id"]), evaluation.Result(case["id"], "x", ""))[1],
        )
        monkeypatch.setattr(evaluation, "_spend", lambda *a: None)

        from mra.config import Config

        evaluation.run(Config(), free_only=True)
        paid = {c["id"] for c in evaluation.load_cases() if c["needs_model"]}
        assert not (set(seen) & paid)
        assert seen

    def test_an_unknown_case_id_is_an_error_not_an_empty_pass(self, monkeypatch):
        """Silently reporting 0/0 for a typo'd id would read as success."""
        from mra.config import Config

        with pytest.raises(ValueError, match="没有匹配"):
            evaluation.run(Config(), only="no-such-case")


class TestBrokenCases:
    """A case that produced nothing to check must not read as a model failure.

    The first real run scored one case 0/6 and it looked like a regression. The
    command it invoked prints a count, not the reading, so those six markers
    could never have matched — the case was structurally incapable of passing.
    """

    def test_a_case_with_no_output_is_flagged_not_scored_as_missed(self, tmp_path, monkeypatch):
        import subprocess

        case = {
            "id": "c", "command": "digest", "why": "w",
            "sanity": {"what": "有一张卡片", "any": ["科学问题"]},
            "expect": [{"id": "e", "what": "指出反向因果", "any": ["反向因果"]}],
        }
        monkeypatch.setattr(
            evaluation, "_invoke",
            lambda *a, **k: subprocess.CompletedProcess([], 0, "Extracted 9 cards\n", ""),
        )
        result = evaluation._run_case(case, tmp_path, tmp_path / ".mra")
        assert result.broken
        assert "不能当成模型没抓到" in result.broken

    def test_a_case_that_did_produce_output_is_not_flagged(self, tmp_path, monkeypatch):
        import subprocess

        case = {
            "id": "c", "command": "import", "why": "w",
            "sanity": {"what": "有一张卡片", "any": ["科学问题"]},
            "expect": [{"id": "e", "what": "指出反向因果", "any": ["反向因果"]}],
        }
        monkeypatch.setattr(
            evaluation, "_invoke",
            lambda *a, **k: subprocess.CompletedProcess(
                [], 0, "科学问题\n  这项研究想问什么\n局限\n  存在反向因果的可能\n", ""
            ),
        )
        result = evaluation._run_case(case, tmp_path, tmp_path / ".mra")
        assert not result.broken
        assert result.caught == 1

    def test_the_summary_warns_when_any_case_is_broken(self):
        result = evaluation.Result("c", "digest", "w",
                                   checks=[evaluation.Check("e", "what", False)])
        result.broken = "用例本身没产出可检查的内容"
        text = evaluation.format_report(evaluation.Report(results=[result]))
        assert "用例本身坏了" in text

    def test_the_paid_case_now_runs_a_command_that_prints_its_reading(self):
        """digest prints a count; import --digest prints the card."""
        case = next(c for c in evaluation.load_cases() if c["id"] == "digest-cross-sectional")
        assert case["command"] == "import"
        assert "--digest" in case["args"]

    def test_every_paid_case_declares_a_sanity_marker(self):
        for case in evaluation.load_cases():
            if case["needs_model"]:
                assert case.get("sanity"), case["id"]


class TestSavedBaseline:
    def test_the_saved_run_keeps_what_was_actually_said(self):
        """Pass/fail alone cannot answer "what did it say" three weeks later."""
        result = evaluation.Result("c", "import", "w", output="局限\n  存在反向因果")
        result.checks = [evaluation.Check("e", "what", True)]
        saved = evaluation.to_json(evaluation.Report(results=[result]))
        assert "反向因果" in saved["results"][0]["output"]

    def test_a_broken_flag_survives_the_round_trip(self):
        result = evaluation.Result("c", "digest", "w")
        result.broken = "用例本身没产出可检查的内容"
        saved = evaluation.to_json(evaluation.Report(results=[result]))
        assert saved["results"][0]["broken"]
