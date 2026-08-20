"""Several projects side by side, and one place to see across them.

The tool was built around one knowledge base per folder, which is right: a
hypothesis about liver fibrosis has no business being retrieved while someone
asks about pancreatic cancer. But a PI runs several at once, and the folder
model gave no way to ask the question a PI actually has — who is moving, how
far along, what has it cost.

A project is a directory under a root that contains a `.mra`. That is the whole
model. There is deliberately no registry file: a registry drifts from what is
on disk the first time someone renames or copies a folder, and then the tool is
confidently wrong about work that exists. Scanning cannot drift.

The root's own `.mra`, if there is one, is a project too — that is where the
single-project layout everyone already has lives, and it must keep working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .store import Store

# The name given to a `.mra` sitting directly in the root, i.e. a workspace
# created before projects existed.
DEFAULT_NAME = "默认"

# Windows forbids these outright; the rest would let a name escape the root.
FORBIDDEN = set('<>:"/\\|?*')


@dataclass
class Project:
    name: str
    workspace: Path
    articles: int = 0
    cards: int = 0
    hypotheses: int = 0
    latest: str = ""
    spend: float | None = None
    is_default: bool = False


def root() -> Path:
    """Where projects live.

    MRA_ROOT is what the launcher sets, so double-clicking always lands in the
    same place regardless of where cmd happened to start.
    """
    return Path(os.environ.get("MRA_ROOT") or Path.cwd())


def safe_name(name: str) -> str:
    """Reject anything that would land outside the root.

    The web interface passes names straight from a text box, so this is a
    boundary, not a nicety.
    """
    cleaned = (name or "").strip().strip(".")
    if not cleaned:
        raise ValueError("课题名不能为空")
    if cleaned in {".", ".."} or cleaned.startswith(".."):
        raise ValueError("课题名不能是 . 或 ..")
    if any(char in FORBIDDEN for char in cleaned):
        raise ValueError('课题名不能包含 < > : " / \\ | ? *')
    if len(cleaned) > 60:
        raise ValueError("课题名太长了（最多 60 个字符）")
    return cleaned


def workspace_for(name: str, base: Path | None = None) -> Path:
    base = base or root()
    if name == DEFAULT_NAME:
        return base / ".mra"
    return base / safe_name(name) / ".mra"


def discover(base: Path | None = None) -> list[Project]:
    """Every project under the root, cheapest fields first.

    A directory without a `.mra` is somebody's data folder, not a project, and
    is skipped rather than offered as an empty one.
    """
    base = base or root()
    found: list[Project] = []

    if (base / ".mra").is_dir():
        found.append(Project(DEFAULT_NAME, base / ".mra", is_default=True))

    if base.is_dir():
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (entry / ".mra").is_dir():
                found.append(Project(entry.name, entry / ".mra"))

    for project in found:
        _fill(project)
    return found


def create(name: str, base: Path | None = None) -> Path:
    base = base or root()
    workspace = workspace_for(name, base)
    if workspace.exists():
        raise ValueError(f"课题「{name}」已经存在了：{workspace.parent}")
    workspace.mkdir(parents=True)
    return workspace


def _fill(project: Project) -> None:
    """Read the numbers a PI actually asks for. Failures leave zeros."""
    database = project.workspace / "knowledge.db"
    if database.exists():
        try:
            with Store(database) as store:
                project.articles = store.count_articles()
                project.cards = store.count_cards()
                versions = store.list_hypotheses()
                project.hypotheses = len(versions)
                newest = store.latest_hypothesis()
                if newest:
                    project.latest = newest[1].get("title", "")
        except Exception:  # a half-written or locked database is not a crash
            pass

    usage = project.workspace / "usage.json"
    if not usage.exists():
        return
    try:
        # usage.json stores token counts, not money — the price table turns one
        # into the other, and it lives in the project's own config because a
        # project pointed at DeepSeek is priced differently from one on Claude.
        from .config import Config
        from .usage import Ledger

        cfg = Config.load(project.workspace)
        ledger = Ledger.load(usage, cfg.model, cfg.prices)
        project.spend = ledger.lifetime.cost(cfg.model)
    except Exception:
        pass


def format_list(projects: list[Project], current: Path | None = None) -> str:
    """The cross-project view: who is moving, how far, what it cost."""
    if not projects:
        return (
            "还没有任何课题。\n\n"
            "新建一个：`mra project new 肝纤维化`，或者在网页界面右上角点「新建课题」。\n"
            "每个课题是一个独立的文献库和假说线，互不干扰。"
        )

    lines = [f"共 {len(projects)} 个课题。", ""]
    lines.append(
        _pad("课题", 22) + _pad("文献", 7) + _pad("已读", 7)
        + _pad("假说", 7) + _pad("花费", 10) + "当前假说"
    )
    lines.append("─" * 60)

    for project in projects:
        here = "▸ " if current and project.workspace == current else "  "
        spend = "—" if project.spend is None else f"${project.spend:.2f}"
        lines.append(
            _pad(here + project.name, 22)
            + _pad(str(project.articles), 7)
            + _pad(str(project.cards), 7)
            + _pad(str(project.hypotheses), 7)
            + _pad(spend, 10)
            + (_clip(project.latest) or "—")
        )

    lines.append("")
    lines.append("▸ 是当前这个。切换：网页界面右上角的下拉框，或 `mra --project <名字> status`。")

    stale = [p for p in projects if p.articles and not p.cards]
    if stale:
        lines.append("")
        lines.append(
            "这几个导了文献但一篇都没读："
            + "、".join(p.name for p in stale)
            + " —— 跑「提炼文献」或导入时勾上「读完直接出分析」。"
        )
    return "\n".join(lines)


def _columns(text: str) -> int:
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(1, width - _columns(text))


def _clip(text: str, width: int = 40) -> str:
    text = " ".join((text or "").split())
    if _columns(text) <= width:
        return text
    out, used = "", 0
    for char in text:
        size = 2 if ord(char) > 0x2E80 else 1
        if used + size > width - 1:
            break
        out += char
        used += size
    return out + "…"
