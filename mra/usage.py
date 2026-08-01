"""Token and cost accounting.

Researchers run this on their own money, so every command that calls the model
reports what it cost. Cache reads and writes are priced separately from plain
input — a prompt-cached run can cost a fraction of what the raw token count
suggests, and hiding that makes the numbers look worse than they are.

Prices are a snapshot (see PRICE_TABLE_DATE) and are not fetched at runtime.
Override any of them in mra.config.json under `prices`, or check the current
published rates if the numbers here look stale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

PRICE_TABLE_DATE = "2026-06"

# US dollars per million tokens: (input, output).
# Cache reads bill at 0.1x input, cache writes at 1.25x input (5-minute TTL).
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass
class Usage:
    """Token counts for one or more calls."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    @property
    def total_input(self) -> int:
        """Every token the model read, cached or not."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def cost(self, model: str) -> float | None:
        """Cost in USD, or None when the model is not in the price table."""
        price = _lookup_price(model)
        if price is None:
            return None
        in_rate, out_rate = price
        return (
            self.input_tokens * in_rate
            + self.cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
            + self.cache_write_tokens * in_rate * CACHE_WRITE_MULTIPLIER
            + self.output_tokens * out_rate
        ) / 1_000_000

    def is_empty(self) -> bool:
        return self.calls == 0


def _lookup_price(model: str) -> tuple[float, float] | None:
    if model in PRICES:
        return PRICES[model]
    # Providers prefix model ids (Bedrock's "anthropic.claude-opus-5"), and the
    # API can return a dated snapshot id. Fall back to the longest known id the
    # returned name contains.
    matches = [key for key in PRICES if key in model]
    if matches:
        return PRICES[max(matches, key=len)]
    return None


@dataclass
class Ledger:
    """Accumulates usage for the current command and across the workspace."""

    path: Path | None = None
    model: str = ""
    prices: dict[str, tuple[float, float]] = field(default_factory=dict)
    session: Usage = field(default_factory=Usage)
    lifetime: Usage = field(default_factory=Usage)
    history: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, model: str, prices: dict | None = None) -> "Ledger":
        ledger = cls(path=path, model=model, prices=prices or {})
        # A user-supplied override wins over the built-in snapshot.
        for name, pair in (prices or {}).items():
            PRICES[name] = (float(pair[0]), float(pair[1]))

        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ledger.lifetime = Usage(**data.get("lifetime", {}))
                ledger.history = data.get("history", [])
            except (json.JSONDecodeError, TypeError):
                # A corrupt ledger must never block real work.
                pass
        return ledger

    def record(self, usage: Usage) -> None:
        self.session.add(usage)
        self.lifetime.add(usage)

    def save(self, command: str = "") -> None:
        if self.path is None or self.session.is_empty():
            return
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "command": command,
            "model": self.model,
            **asdict(self.session),
            "cost_usd": self.session.cost(self.model),
        }
        self.history.append(entry)
        # Keep the file small; the running totals are what matter long-term.
        self.history = self.history[-200:]

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"lifetime": asdict(self.lifetime), "history": self.history},
                indent=2,
            ),
            encoding="utf-8",
        )

    # --------------------------------------------------------------- display

    def line(self) -> str:
        """One-line summary printed after a command."""
        if self.session.is_empty():
            return ""

        u = self.session
        parts = [
            f"{u.calls} call{'s' if u.calls != 1 else ''}",
            f"in {_k(u.total_input)}",
        ]
        if u.cache_read_tokens:
            saved = _cache_saving(u, self.model)
            hit = f"cache hit {_k(u.cache_read_tokens)}"
            if saved:
                hit += f", saved ~${saved:.2f}"
            parts.append(hit)
        parts.append(f"out {_k(u.output_tokens)}")

        cost = u.cost(self.model)
        if cost is None:
            parts.append(f"cost unknown (no price on file for {self.model})")
        else:
            parts.append(f"${cost:.3f}")
            lifetime = self.lifetime.cost(self.model)
            if lifetime is not None and self.lifetime.calls > u.calls:
                parts.append(f"total ${lifetime:.2f}")

        return "usage: " + " · ".join(parts)

    def report(self) -> str:
        """Full accounting for `mra usage`."""
        lines = [
            f"Model            {self.model}",
            f"Price table      {PRICE_TABLE_DATE} snapshot"
            + ("" if _lookup_price(self.model) else "  ⚠ this model is not in it"),
            "",
            "Lifetime:",
            f"  calls          {self.lifetime.calls}",
            f"  input          {self.lifetime.total_input:,} "
            f"({self.lifetime.cache_read_tokens:,} served from cache)",
            f"  output         {self.lifetime.output_tokens:,}",
        ]
        total = self.lifetime.cost(self.model)
        lines.append(f"  cost           {'unknown' if total is None else f'${total:.2f}'}")

        saved = _cache_saving(self.lifetime, self.model)
        if saved:
            lines.append(f"  cache saved    ~${saved:.2f} versus uncached input")

        if self.history:
            lines += ["", "Recent commands:"]
            for entry in self.history[-12:]:
                cost = entry.get("cost_usd")
                shown = "     ?" if cost is None else f"${cost:7.3f}"
                lines.append(
                    f"  {entry['at'][:16].replace('T', ' ')}  {shown}  "
                    f"{entry.get('command', '?'):<12} "
                    f"{entry.get('calls', 0)} calls"
                )
        else:
            lines += ["", "No model calls recorded yet."]

        return "\n".join(lines)


def _cache_saving(usage: Usage, model: str) -> float | None:
    """What the cached tokens would have cost at full input price."""
    price = _lookup_price(model)
    if price is None or not usage.cache_read_tokens:
        return None
    return usage.cache_read_tokens * price[0] * (1 - CACHE_READ_MULTIPLIER) / 1_000_000


def _k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
