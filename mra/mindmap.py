"""Mind maps of one or several papers, under a word budget.

Asked for by the first researcher to use the tool on real papers: "300 字以内
的思维导图". The budget is the feature. A model asked for a short map writes a
long one about half the time, and a limit the tool states but does not keep is
worse than no limit — the researcher stops trusting every other number it
prints. So the count is done here, in code, after the model answers: over the
budget gets one redraw with the overrun named, and if that is still over, the
ends of the deepest branches are cut and listed, so nothing disappears quietly.

Counting follows the convention of Chinese word processors: each Chinese
character is one 字, each English word or number is one, punctuation and
spaces are free. "TGF-β1 敲除 → 纤维化 ↓38%" is seven: 1 + 2 + 3 + 1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from . import prompts, selection
from .config import Config
from .llm import LLM
from .schemas import MindMap, MindNode

DEFAULT_LIMIT = 300
# Below this a map is a root and two words; refuse rather than pretend.
MINIMUM_LIMIT = 30

# Prefix for the line the browser turns into a drawing. Printed only when the
# web interface ran the command, so a terminal never sees a blob of JSON.
WEB_PREFIX = "⟨导图⟩ "

_HAN = "㐀-䶿一-鿿豈-﫿぀-ヿ가-힯"
_WORD = "A-Za-z0-9À-ɏͰ-Ͽ"
_TOKEN = re.compile(rf"[{_HAN}]|[{_WORD}]+(?:[.\-'’][{_WORD}]+)*")


def count(text: str) -> int:
    """字数: Chinese characters and English words, one each."""
    return len(_TOKEN.findall(text))


def total(nodes: list[MindNode]) -> int:
    return sum(count(node.text) for node in nodes)


@dataclass
class Drawn:
    nodes: list[MindNode]
    root: str
    limit: int
    redrawn: bool = False
    pruned: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return total(self.nodes)


# ------------------------------------------------------------------- the tree


def normalise(nodes: list[MindNode]) -> tuple[list[MindNode], str]:
    """Make the flat list a tree: one root, real parents, no cycles.

    The model is asked for exactly that and usually delivers it. When it does
    not — two roots, a parent id that was renamed half way, a node that is its
    own grandparent — the stray node is hung off the root rather than dropped:
    a misplaced branch is visible and fixable, a missing one is not.
    """
    unique: dict[str, MindNode] = {}
    for node in nodes:
        if node.id and node.id not in unique and node.text.strip():
            unique[node.id] = node
    if not unique:
        raise ValueError("模型没有给出任何节点。")

    ordered = list(unique.values())
    root = next((n for n in ordered if not n.parent), ordered[0])

    parent: dict[str, str] = {}
    for node in ordered:
        if node.id == root.id:
            parent[node.id] = ""
        elif not node.parent or node.parent not in unique or node.parent == node.id:
            parent[node.id] = root.id
        else:
            parent[node.id] = node.parent

    # Walk up from every node; one that never reaches the root is in a cycle.
    for node in ordered:
        seen, current = set(), node.id
        while current and current != root.id and current not in seen:
            seen.add(current)
            current = parent.get(current, "")
        if current != root.id:
            parent[node.id] = root.id

    fixed = [n.model_copy(update={"parent": parent[n.id]}) for n in ordered]
    return fixed, root.id


def children(nodes: list[MindNode]) -> dict[str, list[MindNode]]:
    kids: dict[str, list[MindNode]] = {}
    for node in nodes:
        kids.setdefault(node.parent, []).append(node)
    return kids


def depths(nodes: list[MindNode], root: str) -> dict[str, int]:
    parent = {n.id: n.parent for n in nodes}
    result = {}
    for node in nodes:
        depth, current = 0, node.id
        while current != root and current:
            current = parent.get(current, "")
            depth += 1
        result[node.id] = depth
    return result


def prune(nodes: list[MindNode], root: str, limit: int) -> tuple[list[MindNode], list[str]]:
    """Cut leaves until the map fits: deepest level first, last-listed first.

    The deepest leaves are the detail under a detail, and the last-listed are
    what the model ranked lowest. Never the root, and never silently — every
    cut is returned so the researcher sees what the budget cost.
    """
    kept = list(nodes)
    removed: list[str] = []
    while limit and total(kept) > limit:
        kids = children(kept)
        leaves = [n for n in kept if n.id != root and not kids.get(n.id)]
        if not leaves:
            break
        level = depths(kept, root)
        deepest = max(level[n.id] for n in leaves)
        victim = [n for n in leaves if level[n.id] == deepest][-1]
        kept.remove(victim)
        removed.append(victim.text)
    return kept, removed


# -------------------------------------------------------------------- drawing


def draw(
    cfg: Config,
    llm: LLM,
    papers: list[selection.Paper],
    *,
    limit: int = DEFAULT_LIMIT,
    focus: str = "",
) -> Drawn:
    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "mindmap",
            limit=limit if limit else "（不限）",
            focus=focus.strip() or "（没有特别要求。按通常的分支来。）",
            material=selection.material(papers),
        ),
    ]
    ask = "Draw the mind map."

    first = llm.parse(system, [{"role": "user", "content": ask}], MindMap, max_tokens=8000, cache_upto=0)
    nodes, root = normalise(first.nodes)
    drawn = Drawn(nodes, root, limit)
    if not limit or drawn.chars <= limit:
        return drawn

    overrun = (
        f"{ask}\n\n上一版 {drawn.chars} 字，上限 {limit} 字，超了 {drawn.chars - limit} 字。"
        "重画：先砍次要分支，再把句子压成短语。数字、模型/人群、最要紧的局限保留。\n\n"
        f"上一版：\n{plain_tree(drawn)}"
    )
    second = llm.parse(system, [{"role": "user", "content": overrun}], MindMap, max_tokens=8000, cache_upto=0)
    nodes, root = normalise(second.nodes)
    drawn = Drawn(nodes, root, limit, redrawn=True)

    if drawn.chars > limit:
        drawn.nodes, drawn.pruned = prune(drawn.nodes, drawn.root, limit)
    return drawn


# ------------------------------------------------------------------ rendering


def _label(node: MindNode, papers: list[selection.Paper]) -> str:
    if len(papers) > 1 and node.source.strip():
        return f"{node.text}  [{node.source.strip()}]"
    return node.text


def plain_tree(drawn: Drawn, papers: list[selection.Paper] | None = None) -> str:
    """The map as an indented tree with box-drawing branches."""
    papers = papers or []
    kids = children(drawn.nodes)
    by_id = {n.id: n for n in drawn.nodes}
    lines = [_label(by_id[drawn.root], papers)]

    def walk(node_id: str, prefix: str) -> None:
        branch = kids.get(node_id, [])
        for index, child in enumerate(branch):
            last = index == len(branch) - 1
            lines.append(f"{prefix}{'└─ ' if last else '├─ '}{_label(child, papers)}")
            walk(child.id, prefix + ("   " if last else "│  "))

    walk(drawn.root, "")
    return "\n".join(lines)


def format_map(drawn: Drawn, papers: list[selection.Paper]) -> str:
    budget = f"上限 {drawn.limit}" if drawn.limit else "不限字数"
    lines = [f"思维导图  共 {drawn.chars} 字（{budget}）", ""]
    if len(papers) > 1:
        lines += [f"  {paper.legend()}" for paper in papers] + [""]
    else:
        lines += [f"  {papers[0].identifier}  {papers[0].short_title}", ""] if papers else []
    lines.append(plain_tree(drawn, papers))

    if drawn.pruned:
        lines += [
            "",
            f"重画一次仍然超出 {drawn.limit} 字，为了守住上限，删掉了这几个末端分支：",
            *[f"  ✂ {text}" for text in drawn.pruned],
        ]
    return "\n".join(lines)


def to_markdown(drawn: Drawn, papers: list[selection.Paper]) -> str:
    """Nested Markdown: XMind, MindNode and most outliners import it directly."""
    kids = children(drawn.nodes)
    by_id = {n.id: n for n in drawn.nodes}
    lines = [f"# {_label(by_id[drawn.root], papers)}", ""]

    def walk(node_id: str, depth: int) -> None:
        for child in kids.get(node_id, []):
            lines.append(f"{'  ' * depth}- {_label(child, papers)}")
            walk(child.id, depth + 1)

    walk(drawn.root, 0)
    if len(papers) > 1:
        lines.append("- 文献")
        lines += [f"  - {paper.label} = {paper.identifier} {paper.short_title}" for paper in papers]
    return "\n".join(lines) + "\n"


def to_opml(drawn: Drawn, papers: list[selection.Paper]) -> str:
    """OPML: the outline format 幕布, XMind and OmniOutliner all read."""
    kids = children(drawn.nodes)
    by_id = {n.id: n for n in drawn.nodes}

    def outline(node: MindNode, indent: int) -> list[str]:
        pad = "  " * indent
        branch = kids.get(node.id, [])
        text = quoteattr(_label(node, papers))
        if not branch:
            return [f"{pad}<outline text={text}/>"]
        inner = [line for child in branch for line in outline(child, indent + 1)]
        return [f"{pad}<outline text={text}>", *inner, f"{pad}</outline>"]

    root = by_id[drawn.root]
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        f"  <head><title>{quoteattr(root.text)[1:-1]}</title></head>",
        "  <body>",
        *outline(root, 2),
        "  </body>",
        "</opml>",
    ]) + "\n"


def export(drawn: Drawn, papers: list[selection.Paper], suffix: str) -> str:
    return to_opml(drawn, papers) if suffix.lower() == ".opml" else to_markdown(drawn, papers)


def web_line(drawn: Drawn, papers: list[selection.Paper]) -> str:
    """One line the browser draws from. Everything it needs, nothing it must infer."""
    payload = {
        "root": drawn.root,
        "nodes": [n.model_dump() for n in drawn.nodes],
        "legend": {p.label: f"{p.identifier}  {p.short_title}" for p in papers},
        "multi": len(papers) > 1,
        "chars": drawn.chars,
        "limit": drawn.limit,
    }
    return WEB_PREFIX + json.dumps(payload, ensure_ascii=False)
