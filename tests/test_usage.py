"""Cost accounting tests.

The arithmetic matters: cache reads bill at a tenth of input and cache writes at
1.25x, so a naive input+output calculation overstates a cached run substantially.
"""

import json

import pytest

from mra.usage import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    Ledger,
    Usage,
    _lookup_price,
)


class TestCost:
    def test_plain_input_and_output(self):
        # Opus 5: $5/M input, $25/M output.
        usage = Usage(calls=1, input_tokens=1_000_000, output_tokens=1_000_000)
        assert usage.cost("claude-opus-5") == pytest.approx(30.0)

    def test_cache_reads_bill_at_a_tenth(self):
        usage = Usage(calls=1, cache_read_tokens=1_000_000)
        assert usage.cost("claude-opus-5") == pytest.approx(5.0 * CACHE_READ_MULTIPLIER)

    def test_cache_writes_bill_at_a_premium(self):
        usage = Usage(calls=1, cache_write_tokens=1_000_000)
        assert usage.cost("claude-opus-5") == pytest.approx(5.0 * CACHE_WRITE_MULTIPLIER)

    def test_cached_run_is_far_cheaper_than_the_naive_estimate(self):
        """The whole point of tracking cache separately."""
        cached = Usage(calls=1, input_tokens=1000, cache_read_tokens=99_000, output_tokens=1000)
        naive = Usage(calls=1, input_tokens=100_000, output_tokens=1000)
        assert cached.cost("claude-opus-5") < naive.cost("claude-opus-5") / 3

    def test_sonnet_is_cheaper_than_opus(self):
        usage = Usage(calls=1, input_tokens=100_000, output_tokens=10_000)
        assert usage.cost("claude-sonnet-5") < usage.cost("claude-opus-5")

    def test_unknown_model_returns_none_rather_than_guessing(self):
        assert Usage(calls=1, input_tokens=1000).cost("some-future-model") is None

    def test_zero_usage_costs_zero(self):
        assert Usage().cost("claude-opus-5") == 0.0


class TestPriceLookup:
    def test_exact_match(self):
        assert _lookup_price("claude-opus-5") == (5.00, 25.00)

    def test_bedrock_prefix_resolves(self):
        assert _lookup_price("anthropic.claude-opus-5") == (5.00, 25.00)

    def test_dated_snapshot_resolves(self):
        assert _lookup_price("claude-haiku-4-5-20251001") == (1.00, 5.00)

    def test_longest_match_wins(self):
        # "claude-opus-4-8" must not resolve via a shorter accidental substring.
        assert _lookup_price("anthropic.claude-opus-4-8") == (5.00, 25.00)

    def test_unknown_returns_none(self):
        assert _lookup_price("gpt-4") is None


class TestUsageAccumulation:
    def test_add_sums_every_field(self):
        total = Usage()
        total.add(Usage(calls=1, input_tokens=10, output_tokens=20,
                        cache_read_tokens=30, cache_write_tokens=40))
        total.add(Usage(calls=1, input_tokens=1, output_tokens=2,
                        cache_read_tokens=3, cache_write_tokens=4))
        assert (total.calls, total.input_tokens, total.output_tokens) == (2, 11, 22)
        assert (total.cache_read_tokens, total.cache_write_tokens) == (33, 44)

    def test_total_input_counts_cache(self):
        usage = Usage(input_tokens=100, cache_read_tokens=900, cache_write_tokens=50)
        assert usage.total_input == 1050


class TestLedger:
    def test_records_and_persists(self, tmp_path):
        path = tmp_path / "usage.json"
        ledger = Ledger.load(path, "claude-opus-5")
        ledger.record(Usage(calls=1, input_tokens=1000, output_tokens=500))
        ledger.save("digest")

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["lifetime"]["calls"] == 1
        assert data["history"][0]["command"] == "digest"
        assert data["history"][0]["cost_usd"] == pytest.approx(1000 * 5e-6 + 500 * 25e-6)

    def test_lifetime_accumulates_across_runs(self, tmp_path):
        path = tmp_path / "usage.json"
        for _ in range(3):
            ledger = Ledger.load(path, "claude-opus-5")
            ledger.record(Usage(calls=1, input_tokens=1000, output_tokens=100))
            ledger.save("chat")

        final = Ledger.load(path, "claude-opus-5")
        assert final.lifetime.calls == 3
        assert final.lifetime.input_tokens == 3000

    def test_session_is_separate_from_lifetime(self, tmp_path):
        path = tmp_path / "usage.json"
        Ledger.load(path, "claude-opus-5").record(Usage(calls=1, input_tokens=1000))
        first = Ledger.load(path, "claude-opus-5")
        first.record(Usage(calls=1, input_tokens=500, output_tokens=100))
        first.save("chat")

        second = Ledger.load(path, "claude-opus-5")
        assert second.session.is_empty()
        assert second.lifetime.calls == 1  # only the saved one persisted

    def test_saving_nothing_writes_nothing(self, tmp_path):
        path = tmp_path / "usage.json"
        Ledger.load(path, "claude-opus-5").save("status")
        assert not path.exists(), "a command with no model calls must not create a ledger"

    def test_corrupt_ledger_does_not_block_work(self, tmp_path):
        path = tmp_path / "usage.json"
        path.write_text("{ this is not json", encoding="utf-8")
        ledger = Ledger.load(path, "claude-opus-5")
        assert ledger.lifetime.calls == 0  # starts fresh rather than raising

    def test_history_is_capped(self, tmp_path):
        path = tmp_path / "usage.json"
        for _ in range(250):
            ledger = Ledger.load(path, "claude-opus-5")
            ledger.record(Usage(calls=1, input_tokens=10))
            ledger.save("chat")

        final = Ledger.load(path, "claude-opus-5")
        assert len(final.history) == 200
        # Totals survive even though old entries are trimmed.
        assert final.lifetime.calls == 250

    def test_config_price_override_applies(self, tmp_path):
        ledger = Ledger.load(tmp_path / "u.json", "my-gateway-model",
                             prices={"my-gateway-model": [1.0, 2.0]})
        ledger.record(Usage(calls=1, input_tokens=1_000_000, output_tokens=1_000_000))
        assert ledger.session.cost("my-gateway-model") == pytest.approx(3.0)


class TestDisplay:
    def test_line_reports_calls_cost_and_cache(self, tmp_path):
        ledger = Ledger.load(tmp_path / "u.json", "claude-opus-5")
        ledger.record(Usage(calls=2, input_tokens=5000, output_tokens=2000,
                            cache_read_tokens=40_000))
        line = ledger.line()
        assert "2 calls" in line
        assert "cache hit 40.0k" in line
        assert "saved ~$" in line
        assert "$" in line

    def test_line_is_empty_when_nothing_happened(self, tmp_path):
        assert Ledger.load(tmp_path / "u.json", "claude-opus-5").line() == ""

    def test_unknown_model_says_so_rather_than_showing_zero(self, tmp_path):
        ledger = Ledger.load(tmp_path / "u.json", "mystery-model")
        ledger.record(Usage(calls=1, input_tokens=1000))
        assert "cost unknown" in ledger.line()

    def test_report_flags_a_model_missing_from_the_price_table(self, tmp_path):
        ledger = Ledger.load(tmp_path / "u.json", "mystery-model")
        assert "not in it" in ledger.report()

    def test_report_on_empty_ledger(self, tmp_path):
        assert "No model calls recorded yet" in Ledger.load(
            tmp_path / "u.json", "claude-opus-5"
        ).report()
