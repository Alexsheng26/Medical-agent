"""What the conversation remembers once it outgrows the window.

The behaviour being replaced was a silent truncation: `chat_history` returned
the last forty messages and everything older simply stopped existing. Turn 21
could no longer see turn 3, so the dialogue would re-propose a mechanism it had
already ruled out — and the researcher had no way to know why, because nothing
said anything had been dropped.
"""

from __future__ import annotations

import pytest

from mra import dialogue
from mra.config import Config
from mra.store import Store


class Reply:
    def __init__(self, text: str):
        self.text = text


class StubLLM:
    """Records what it was asked, so the fold can be checked without a key."""

    def __init__(self, reply: str = "摘要正文", fail: bool = False):
        # Not named `text`: that is the method name, and the collision made the
        # stub call a string.
        self.reply = reply
        self.fail = fail
        self.systems: list[list[str]] = []

    def text_call_count(self) -> int:
        return len(self.systems)

    def text(self, system, messages, **kwargs):
        if self.fail:
            raise RuntimeError("provider is down")
        self.systems.append(system)
        return Reply(self.reply)

    def parse(self, *args, **kwargs):
        raise RuntimeError("retrieval helper not exercised here")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "k.db") as opened:
        yield opened


@pytest.fixture
def cfg(tmp_path):
    return Config(workspace=tmp_path)


def fill(store: Store, count: int) -> None:
    for index in range(count):
        store.append_chat("user" if index % 2 == 0 else "assistant", f"消息 {index}")


class TestFolding:
    def test_a_short_conversation_is_left_alone(self, cfg, store):
        fill(store, 12)
        llm = StubLLM()
        assert dialogue.fold_older_turns(cfg, store, llm) == 0
        assert llm.text_call_count() == 0, "must not spend a call with nothing to fold"

    def test_nothing_folds_until_enough_has_fallen_out(self, cfg, store):
        """Folding one message at a time would cost a call per turn forever."""
        fill(store, dialogue.CHAT_WINDOW + dialogue.FOLD_WHEN - 1)
        assert dialogue.fold_older_turns(cfg, store, StubLLM()) == 0

    def test_what_falls_outside_the_window_is_folded(self, cfg, store):
        fill(store, 60)
        assert dialogue.fold_older_turns(cfg, store, StubLLM()) == 20
        assert store.chat_summary() is not None

    def test_the_window_itself_is_never_folded(self, cfg, store):
        """The most recent messages stay verbatim; only older ones compress."""
        fill(store, 60)
        dialogue.fold_older_turns(cfg, store, StubLLM())
        covered, _ = store.chat_summary()
        assert covered == 60 - dialogue.CHAT_WINDOW

    def test_a_second_fold_does_not_redo_the_first(self, cfg, store):
        fill(store, 60)
        dialogue.fold_older_turns(cfg, store, StubLLM())
        llm = StubLLM()
        assert dialogue.fold_older_turns(cfg, store, llm) == 0
        assert llm.text_call_count() == 0

    def test_folding_resumes_once_more_accumulates(self, cfg, store):
        fill(store, 60)
        dialogue.fold_older_turns(cfg, store, StubLLM())
        fill(store, 12)
        assert dialogue.fold_older_turns(cfg, store, StubLLM()) == 12

    def test_the_previous_summary_is_handed_back_for_folding_in(self, cfg, store):
        """Otherwise each fold replaces the last and the oldest reasoning is
        lost anyway, just more slowly."""
        fill(store, 60)
        dialogue.fold_older_turns(cfg, store, StubLLM("第一版摘要"))
        fill(store, 12)
        llm = StubLLM("第二版摘要")
        dialogue.fold_older_turns(cfg, store, llm)
        assert "第一版摘要" in llm.systems[0][0]

    def test_a_failed_summary_does_not_lose_the_turn(self, cfg, store):
        """A helper that cannot run must degrade, never break the conversation."""
        fill(store, 60)
        assert dialogue.fold_older_turns(cfg, store, StubLLM(fail=True)) == 0
        assert store.chat_summary() is None

    def test_a_failed_summary_leaves_the_messages_to_be_folded_next_time(self, cfg, store):
        fill(store, 60)
        dialogue.fold_older_turns(cfg, store, StubLLM(fail=True))
        assert dialogue.fold_older_turns(cfg, store, StubLLM()) == 20


class TestSummaryReachesTheModel:
    def test_the_summary_is_given_to_the_next_turn(self, cfg, store):
        store.save_chat_summary(5, "排除了 Kupffer 来源，理由是谱系追踪 [PMID:30778899]")
        llm = StubLLM("回答")
        dialogue.respond(cfg, store, llm, "那 TREM2 呢？")
        assert any("Kupffer" in block for block in llm.systems[0])

    def test_it_is_labelled_as_this_conversation_not_as_literature(self, cfg, store):
        """Handed over unlabelled, a summary reads as retrieved evidence and its
        conclusions get cited as if they came from a paper."""
        store.save_chat_summary(5, "排除了 Kupffer 来源")
        llm = StubLLM("回答")
        dialogue.respond(cfg, store, llm, "继续")
        block = next(b for b in llm.systems[0] if "排除了 Kupffer" in b)
        assert "不是文献" in block

    def test_no_summary_block_when_there_is_none(self, cfg, store):
        # Matched on the block's own marker, not the word 摘要 — the dialogue
        # prompt uses that word itself, so it was matching the wrong thing.
        llm = StubLLM("回答")
        dialogue.respond(cfg, store, llm, "第一个问题")
        assert not any("不是文献" in block for block in llm.systems[0])


class TestClearing:
    def test_clearing_the_chat_clears_the_summary(self, store):
        """A summary surviving a reset would haunt the next conversation with
        conclusions from a discussion the researcher deliberately discarded."""
        fill(store, 4)
        store.save_chat_summary(2, "旧结论")
        store.clear_chat()
        assert store.chat_summary() is None
        assert store.count_chat() == 0


class TestStoreWindowing:
    def test_chat_before_returns_what_falls_outside(self, store):
        fill(store, 10)
        assert len(store.chat_before(4)) == 6

    def test_chat_before_skips_what_is_already_covered(self, store):
        fill(store, 10)
        outside = store.chat_before(4)
        assert len(store.chat_before(4, after_id=outside[2]["id"])) == 3

    def test_chat_before_is_empty_when_everything_fits(self, store):
        fill(store, 3)
        assert store.chat_before(40) == []


class TestNoticeIsNotModelOutput:
    """The fold notice must be distinguishable from what the model said.

    stderr is merged into stdout for the browser, so an unmarked notice appears
    inside the assistant's reply — the same failure `draft` avoids by keeping
    its warnings out of the manuscript file.
    """

    def test_the_notice_carries_the_system_marker(self):
        from mra import cli

        assert cli.NOTICE.strip(), "a notice with no marker cannot be routed"

    def test_the_page_routes_marked_lines_out_of_the_reply(self):
        from mra import webui

        page = webui.read_index().decode("utf-8")
        assert "⟨系统⟩" in page, "the page has no rule for tool notices"
