"""Static checks on 启动.bat.

The launcher is the only file in this project that cannot be exercised by the
test suite — it runs under cmd.exe, and a wrong line ending or a typo'd label
shows up as a blank window on someone else's machine, days later. These tests
check the failure modes that are checkable without cmd: line endings, label
resolution, and the two cmd parsing rules this file's structure depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "启动.bat"

# `echo` carries prose that legitimately contains parentheses and colons; REM is
# commentary. Neither participates in cmd's block structure.
PROSE = re.compile(r"^\s*(echo|rem|::)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def raw() -> str:
    return LAUNCHER.read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def lines(raw: str) -> list[str]:
    return raw.split("\r\n")


def test_launcher_exists():
    assert LAUNCHER.is_file(), "the Windows launcher is the documented entry point"


def test_every_line_ends_crlf(raw: str):
    """cmd.exe misparses labels and goto in an LF-only batch file.

    Checked against the bytes on disk rather than trusting .gitattributes,
    because `eol=crlf` only converts on checkout — a ZIP download gets the blob
    as stored, and Code -> Download ZIP is how most Windows users arrive.
    """
    stray = [i for i, ch in enumerate(raw) if ch == "\n" and raw[i - 1 : i] != "\r"]
    assert not stray, f"{len(stray)} LF line ending(s) without a preceding CR"


def test_no_lone_carriage_returns(raw: str):
    assert "\r" not in raw.replace("\r\n", ""), "a CR not paired with LF"


def test_goto_targets_all_exist(lines: list[str]):
    labels = {
        line.strip()[1:].split()[0].lower()
        for line in lines
        if line.strip().startswith(":") and not line.strip().startswith("::")
    }
    targets = set()
    for line in lines:
        if PROSE.match(line):
            continue
        for match in re.finditer(r"goto\s+:?(\w+)", line, re.IGNORECASE):
            targets.add(match.group(1).lower())

    # `goto :eof` is cmd's built-in return from a `call`, not a label.
    targets.discard("eof")
    missing = sorted(targets - labels)
    assert not missing, f"goto targets with no label: {missing}"


def test_labels_are_unique(lines: list[str]):
    """Duplicate labels are legal and silently take the first — a trap."""
    seen: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if text.startswith(":") and not text.startswith("::"):
            name = text[1:].split()[0].lower()
            assert name not in seen, f"label :{name} repeated at line {number}"
            seen[name] = number


def test_no_set_p_inside_a_parenthesised_block(lines: list[str]):
    """`set /p` inside a block reads input after cmd already expanded the var.

    cmd expands %VAR% for the whole block at parse time, so a key prompted for
    inside `if (...)` is always saved empty. This file uses labels instead; the
    test keeps it that way.
    """
    depth = 0
    for number, line in enumerate(lines, 1):
        if PROSE.match(line):
            continue
        outside_quotes = re.sub(r'"[^"]*"', "", line)
        if depth > 0 and re.search(r"\bset\s+/p\b", line, re.IGNORECASE):
            pytest.fail(f"line {number}: set /p inside a parenthesised block")
        depth += outside_quotes.count("(") - outside_quotes.count(")")
        depth = max(depth, 0)


def test_menu_digits_all_dispatch(lines: list[str]):
    """Every number the menu offers has to go somewhere."""
    offered = set()
    for line in lines:
        match = re.match(r"^echo\s+(\d+)\s+\S", line.strip())
        if match:
            offered.add(match.group(1))

    dispatched = set(re.findall(r'if\s+"%CHOICE%"=="(\d+)"', "\r\n".join(lines)))

    assert offered, "no menu items parsed — did the menu format change?"
    assert offered <= dispatched, f"menu items with no handler: {sorted(offered - dispatched)}"


def test_no_multiline_parenthesised_blocks(lines: list[str]):
    """A block spanning lines is where cmd's UTF-8 handling comes apart.

    Under `chcp 65001` cmd re-reads a batch file by byte offset while executing
    a block, and multi-byte characters desync those offsets: a line gets cut
    mid-character and its tail is run as a command. Observed on the first real
    Windows run — an error message inside `if errorlevel 1 (...)` came back as

        '<mojibake>长，试着把整个文件夹移到' is not recognized as an
        internal or external command

    printing garbage in place of the explanation the researcher needed. Labels
    and goto have no such failure mode, so the file uses them throughout.
    """
    offenders = []
    for number, line in enumerate(lines, 1):
        if PROSE.match(line):
            continue
        outside_quotes = re.sub(r'"[^"]*"', "", line)
        # `for /f ... do (` and `if ... (` opening a block, i.e. a trailing (
        if re.search(r"\(\s*$", outside_quotes):
            offenders.append(number)

    assert not offenders, (
        f"multi-line parenthesised block(s) at line(s) {offenders} — use a label "
        "and goto instead; cmd splits Chinese text inside blocks"
    )


def test_interpreter_is_absolute_everywhere(lines: list[str]):
    """%RUN% has to be absolute: after pushd, a relative path resolves into the
    researcher's data folder, where no interpreter lives."""
    assignments = [
        line
        for line in lines
        # `set "RUN="` clears it at the top; only real values need checking.
        if re.match(r'^\s*set\s+"RUN=.+"', line, re.IGNORECASE)
    ]
    assert assignments, "RUN is never assigned"
    for line in assignments:
        assert "%~dp0" in line or "%%p" in line or "%VENVPY%" in line, (
            f"RUN assigned a possibly relative path: {line.strip()}"
        )


def test_every_invocation_uses_run(lines: list[str]):
    for number, line in enumerate(lines, 1):
        if "-m mra" not in line or PROSE.match(line):
            continue
        assert '"%RUN%"' in line, f"line {number}: mra invoked without %RUN%"


def test_every_invocation_carries_the_current_project(lines: list[str]):
    """A menu item that ignores %PROJARG% silently acts on the wrong project —
    it imports into 默认 while the header says 肝纤维化."""
    for number, line in enumerate(lines, 1):
        if "-m mra" not in line or PROSE.match(line):
            continue
        if "-m mra project" in line:
            continue  # `project list` and `project new` span every project
        assert "%PROJARG%" in line, f"line {number}: missing %PROJARG%"


def test_deepseek_branch_sets_every_variable_the_backend_reads(raw: str):
    """A half-configured provider fails at the first call, not at setup."""
    start = raw.index("\r\n:key_deepseek\r\n")
    branch = raw[start : raw.index("\r\n:havekey\r\n", start)]
    for variable in ("MRA_PROVIDER", "MRA_BASE_URL", "MRA_MODEL", "MRA_API_KEY"):
        assert f'setx {variable} ' in branch, f"{variable} never persisted"
        assert f'set "{variable}=' in branch, (
            f"{variable} not set in-process — setx does not affect the current window, "
            "which is the whole reason this block exists"
        )
