"""Measuring whether the judgements are still there.

Four hundred and eighty tests check the plumbing — that JSON parses, that a
forged PMID is caught, that a Chinese column lines up. Not one of them checks
the thing the tool is actually for. Swap the provider, edit a prompt, take a
model version bump, and the effect on the quality of the reading was, until
this module, unmeasurable except by reading two outputs side by side.

The material makes this cheap, because the defects were planted on purpose:
demo_notes.md states a number that demo_data.csv does not contain, the demo
corpus holds papers that contradict each other, and evals/cross_sectional.txt
is a fabricated study written to be causally overreaching, effect-size-free and
mechanistically computational. The answers are known, so the check is pass/fail
rather than a judge model scoring a judge model.

**What this measures, precisely.** An expectation passes when any of its markers
appears in the output. That is a necessary condition for having made the point,
not a sufficient one: a model can print "reverse causation" without arguing it,
and this will count that as caught. What it detects reliably is the negative —
a model that never mentions it certainly did not make the argument. That
asymmetry is enough to catch regression, which is what this is for. It is not a
quality score, and reporting it as one would be dishonest.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Config

# Commands whose case needs the corpus in place to retrieve against.
SEEDED = {"assess", "figures", "digest", "review", "chat"}


@dataclass
class Check:
    id: str
    what: str
    caught: bool


@dataclass
class Result:
    id: str
    command: str
    why: str
    checks: list[Check] = field(default_factory=list)
    exit_code: int = 0
    error: str = ""
    broken: str = ""
    output: str = ""

    @property
    def caught(self) -> int:
        return sum(1 for check in self.checks if check.caught)

    @property
    def total(self) -> int:
        return len(self.checks)


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    spend: float | None = None

    @property
    def caught(self) -> int:
        return sum(r.caught for r in self.results)

    @property
    def broken(self) -> list[str]:
        return [r.id for r in self.results if r.broken]

    @property
    def total(self) -> int:
        return sum(r.total for r in self.results)


def load_cases() -> list[dict[str, Any]]:
    raw = (resources.files("mra") / "evals" / "cases.json").read_text(encoding="utf-8")
    return json.loads(raw)["cases"]


def run(
    cfg: Config,
    *,
    free_only: bool = False,
    only: str = "",
    on_case=None,
) -> Report:
    """Run every case in a throwaway workspace.

    A temporary workspace rather than the researcher's own: these cases import
    documents and would otherwise pollute a real knowledge base with fabricated
    studies, which is precisely the contamination the tool exists to prevent.
    """
    cases = [c for c in load_cases() if not (free_only and c["needs_model"])]
    if only:
        cases = [c for c in cases if c["id"] == only]
    if not cases:
        raise ValueError(f"没有匹配的用例：{only!r}" if only else "没有可跑的用例")

    report = Report(model=cfg.model, provider=cfg.provider or "anthropic")

    with tempfile.TemporaryDirectory(prefix="mra-eval-") as temporary:
        home = Path(temporary)
        workspace = home / ".mra"
        _seed(home, workspace)

        for case in cases:
            if on_case:
                on_case(case)
            report.results.append(_run_case(case, home, workspace))

        report.spend = _spend(workspace, cfg)

    return report


def _seed(home: Path, workspace: Path) -> None:
    """Lay out the fixtures and build the knowledge base the cases retrieve from."""
    for name in ("demo_corpus.xml", "demo_data.csv", "demo_notes.md"):
        source = resources.files("mra") / "examples" / name
        with resources.as_file(source) as path:
            shutil.copyfile(path, home / name)

    source = resources.files("mra") / "evals" / "cross_sectional.txt"
    with resources.as_file(source) as path:
        shutil.copyfile(path, home / "cross_sectional.txt")

    (home / "forged.md").write_text(
        "TREM2+ macrophages accumulate in fibrous septa [PMID:34556677].\n"
        "A second report claims the opposite [PMID:99999999].\n"
        "Our own unpublished series agrees [LOCAL:deadbeef].\n",
        encoding="utf-8",
    )
    (home / "ai_sounding.md").write_text(
        "In today's rapidly evolving landscape of hepatology research, it is "
        "important to note that macrophages play a crucial role. Furthermore, "
        "the utilisation of single-cell approaches has enabled the "
        "identification of novel populations. Moreover, the characterisation of "
        "these populations facilitates the elucidation of mechanisms. "
        "Additionally, the investigation of these mechanisms provides valuable "
        "insights. In conclusion, it is worth noting that further research is "
        "needed to fully understand this complex and multifaceted process.\n",
        encoding="utf-8",
    )

    _invoke(["init", "--email=eval@example.invalid"], home, workspace)
    # The corpus is what assess and figures retrieve against. cross_sectional.txt
    # is deliberately NOT imported here — its case imports it, because the
    # reading is only printed by the command that produces it.
    _invoke(["import", "demo_corpus.xml"], home, workspace)


def _run_case(case: dict[str, Any], home: Path, workspace: Path) -> Result:
    result = Result(id=case["id"], command=case["command"], why=case.get("why", ""))

    argv = [case["command"]]
    if case.get("file"):
        argv.append(case["file"])
    argv.extend(case.get("data", []))
    argv.extend(case.get("args", []))

    completed = _invoke(argv, home, workspace)
    result.exit_code = completed.returncode
    result.output = completed.stdout

    # Some commands signal a finding by exiting non-zero — `refs` returns 1 when
    # it catches a forged citation, which is the behaviour being tested. Each
    # case declares the code that means "worked", defaulting to 0.
    if completed.returncode != case.get("exit_code", 0):
        result.error = _last_meaningful_line(completed.stdout)

    haystack = completed.stdout.lower()

    # A case that produced nothing to check scores zero on every expectation,
    # which is indistinguishable from a model that missed everything — and that
    # is exactly how the first real run reported a case that could never pass.
    sanity = case.get("sanity")
    if sanity and not any(m.lower() in haystack for m in sanity["any"]):
        result.broken = (
            f"用例本身没产出可检查的内容（期望：{sanity['what']}）。"
            "下面的 ✗ 不能当成模型没抓到。"
        )

    for expectation in case["expect"]:
        found = any(marker.lower() in haystack for marker in expectation["any"])
        result.checks.append(Check(expectation["id"], expectation["what"], found))

    return result


def _invoke(argv: list[str], cwd: Path, workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "mra", f"--workspace={workspace}", *argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        stdin=subprocess.DEVNULL,
        timeout=900,
    )


def _last_meaningful_line(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()[:200]
    return "(no output)"


def _spend(workspace: Path, cfg: Config) -> float | None:
    usage = workspace / "usage.json"
    if not usage.exists():
        return None
    try:
        from .usage import Ledger

        return Ledger.load(usage, cfg.model, cfg.prices).lifetime.cost(cfg.model)
    except Exception:
        return None


# ------------------------------------------------------------------ reporting


def format_report(
    report: Report,
    baseline: dict[str, Any] | None = None,
    show_misses: bool = False,
) -> str:
    """Render the run.

    `show_misses` prints the output of any case that missed something. Without
    it a miss is unactionable: there is no way to tell a model that did not make
    the point from a marker list too narrow to recognise that it did — and the
    second kind silently inflates every future regression report.
    """
    lines = [
        f"评估结果  {report.model}（{report.provider}）",
        "",
    ]
    previous = _baseline_map(baseline)

    for result in report.results:
        head = f"{_mark(result.caught, result.total)} {result.id}  {result.caught}/{result.total}"
        lines.append(head)
        if result.why:
            lines.append(f"    {result.why}")
        for check in result.checks:
            was = previous.get((result.id, check.id))
            change = ""
            if was is not None and was != check.caught:
                change = "  ← 变好了" if check.caught else "  ← 变差了"
            lines.append(f"      {'✓' if check.caught else '✗'} {check.what}{change}")
        if result.broken:
            lines.append(f"      ! {result.broken}")
        if result.error:
            lines.append(f"      ! 这条没跑起来（退出码 {result.exit_code}）：{result.error}")
        lines.append("")

    lines.append("─" * 60)
    if report.broken:
        lines.append(
            f"⚠ 有 {len(report.broken)} 条用例本身坏了（{'、'.join(report.broken)}）——"
            "它们的分数不能当成模型表现。"
        )
    lines.append(f"总计  抓到 {report.caught}/{report.total}")
    if report.spend is not None:
        lines.append(f"花费  ${report.spend:.2f}")

    if previous:
        before = sum(1 for value in previous.values() if value)
        delta = report.caught - before
        arrow = "持平" if delta == 0 else (f"好了 {delta} 条" if delta > 0 else f"差了 {-delta} 条")
        lines.append(f"对比基线  {before} → {report.caught}（{arrow}）")

    if show_misses:
        for result in report.results:
            if result.caught == result.total or not result.output:
                continue
            lines.append("")
            lines.append(f"── {result.id} 的完整输出 " + "─" * 30)
            lines.append(result.output.strip())
            lines.append("─" * 50)

    missed = [
        (r.id, c.what) for r in report.results for c in r.checks if not c.caught
    ]
    if missed:
        lines.append("")
        lines.append("没抓到的：")
        for case_id, what in missed:
            lines.append(f"  · [{case_id}] {what}")

    lines.append("")
    lines.append(
        "这个分数量的是「已知问题被提到了没有」，不是「判断质量」。"
        "提到不等于论证到位，所以高分不能当成好；但没提到就是确实没说，"
        "所以掉分是真的掉了。"
    )
    return "\n".join(lines)


def to_json(report: Report) -> dict[str, Any]:
    return {
        "model": report.model,
        "provider": report.provider,
        "spend": report.spend,
        "caught": report.caught,
        "total": report.total,
        "results": [
            {
                "id": result.id,
                "command": result.command,
                "exit_code": result.exit_code,
                # The output travels with the scores. A baseline that records
                # only pass/fail cannot answer the question you have three weeks
                # later — not "did it catch this" but "what did it say".
                "output": result.output,
                "broken": result.broken,
                "checks": [
                    {"id": check.id, "what": check.what, "caught": check.caught}
                    for check in result.checks
                ],
            }
            for result in report.results
        ],
    }


def _baseline_map(baseline: dict[str, Any] | None) -> dict[tuple[str, str], bool]:
    if not baseline:
        return {}
    return {
        (result["id"], check["id"]): check["caught"]
        for result in baseline.get("results", [])
        for check in result.get("checks", [])
    }


def _mark(caught: int, total: int) -> str:
    if caught == total:
        return "✓"
    return "✗" if caught == 0 else "~"
