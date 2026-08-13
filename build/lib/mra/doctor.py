"""Self-check: can this machine actually talk to the configured model?

Written because the environment this was developed in cannot reach every
provider, so "does DeepSeek work" is a question only the researcher's own
machine can answer. It is equally useful on the default path — a wrong key, an
exhausted balance and a blocked campus network all look the same from the
outside, and all three produce a wall of traceback at the worst moment.

Two probes, deliberately tiny. The second is the one that matters for an
OpenAI-compatible endpoint: structured output is emulated through tool calling
there, and roughly half the commands need it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .config import Config
from .llm import LLM

# Commands split by what they need from the endpoint.
PROSE_COMMANDS = "chat / draft / nativize / polish / finalize / proposal / review 的正文"
STRUCTURED_COMMANDS = (
    "digest / assess / figures / hypothesis / review 的大纲 / journal add / "
    "fingerprint / sync 的简报 / PDF 元数据 / 中文检索词"
)


class Ping(BaseModel):
    """Smallest possible structured reply, to prove the path works."""

    ok: bool = Field(description="Always true")
    model_note: str = Field(description="One short word about yourself")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def prose_ok(self) -> bool:
        return any(c.name == "text" and c.passed for c in self.checks)

    @property
    def structured_ok(self) -> bool:
        return any(c.name == "structured" and c.passed for c in self.checks)


def key_variable(provider: str) -> tuple[str, bool]:
    """Which environment variable holds the key here, and whether it is set."""
    if provider == "anthropic":
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if os.environ.get(name):
                return name, True
        return "ANTHROPIC_API_KEY", False

    for name in ("MRA_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            return name, True
    return "MRA_API_KEY", False


def run(cfg: Config) -> Report:
    """Probe the configured endpoint. Two calls, a few tokens each."""
    from . import backends

    report = Report()
    provider = (cfg.provider or "anthropic").lower()
    _, has_key = key_variable(provider)
    report.checks.append(Check("key", has_key, "" if has_key else "环境变量里没有 key"))
    if not has_key:
        return report

    try:
        llm = LLM(cfg)
    except Exception as exc:  # noqa: BLE001 - a missing SDK is a normal outcome here
        report.checks.append(Check("client", False, str(exc)))
        return report

    started = time.monotonic()
    try:
        result = llm.text(
            "Answer with one word.",
            [{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=64,
        )
        report.checks.append(
            Check("text", True, f"{time.monotonic() - started:.1f}s，回了 {result.text[:20]!r}")
        )
    except Exception as exc:  # noqa: BLE001 - reporting the failure is the job
        report.checks.append(Check("text", False, backends.describe_api_error(exc)))
        return report  # nothing structured can work if prose cannot

    try:
        llm.parse(
            "Call the tool once.",
            [{"role": "user", "content": "Record ok=true and one word about yourself."}],
            Ping,
            max_tokens=512,
        )
        report.checks.append(Check("structured", True))
    except Exception as exc:  # noqa: BLE001
        report.checks.append(Check("structured", False, backends.describe_api_error(exc)))

    return report


def format_report(cfg: Config, report: Report) -> str:
    provider = (cfg.provider or "anthropic").lower()
    variable, has_key = key_variable(provider)

    lines = [
        "配置",
        f"  provider   {provider}",
        f"  base_url   {cfg.base_url or '(默认)'}",
        f"  model      {cfg.model}",
        f"  API key    {'已设置 (' + variable + ')' if has_key else '✗ 没有设置 — 请设 ' + variable}",
        "",
    ]

    found = {c.name: c for c in report.checks}
    for name, label in (("client", "SDK"), ("text", "纯文本调用"), ("structured", "结构化输出")):
        check = found.get(name)
        if check is None:
            continue
        mark = "✓" if check.passed else "✗"
        lines.append(f"{label:<12} {mark} {check.detail}".rstrip())

    lines.append("")
    if report.prose_ok and report.structured_ok:
        lines.append("结论：全部命令都可用。")
    elif report.prose_ok:
        lines += [
            "结论：**只有一半可用**。",
            f"  可用    {PROSE_COMMANDS}",
            f"  不可用  {STRUCTURED_COMMANDS}",
            "",
            "  结构化输出要求这个端点的 tool calling 可用。换一个支持的模型，",
            "  或者这些步骤改回默认的 Anthropic 跑（见 docs/PROVIDERS.md）。",
        ]
    else:
        lines.append("结论：**连不上**，上面那条错误就是原因。")

    return "\n".join(lines)
