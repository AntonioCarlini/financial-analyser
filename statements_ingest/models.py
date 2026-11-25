"""Canonical data model shared by all provider adapters.

Adapters turn one provider's files into ParsedStatement objects. The ingest
step adds who/where information (person, label, provenance) to make Snapshots,
which are what the store holds.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ACCOUNT_TYPES = ("GIA", "ISA", "PENSION")

# Provider/filename terms -> canonical tax-wrapper vocabulary.
_ALIASES = {"GIA": "GIA", "ISA": "ISA", "SIPP": "PENSION", "PENSION": "PENSION"}

PER_ITEM_TOLERANCE = Decimal("0.01")


def normalise_account_type(raw: str | None) -> str | None:
    return _ALIASES.get(raw.strip().upper()) if raw else None


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


@dataclass
class Position:
    instrument_name: str
    ticker: str
    quantity: Decimal
    value: Decimal
    currency: str


@dataclass
class ParsedStatement:
    """What a provider adapter extracts from one statement. Fields may be None."""
    account_type: str | None = None   # canonical (GIA/ISA/PENSION) or None if unrecognised
    account_ref: str | None = None
    year: int | None = None
    month: int | None = None
    currency: str | None = None
    portfolio: Decimal | None = None
    holdings: Decimal | None = None
    cash: Decimal | None = None
    positions: list[Position] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    provider: str
    person: str
    account_type: str
    account_label: str
    account_ref: str
    period_start: date | None
    period_end: date
    currency: str
    portfolio_reported: Decimal | None
    holdings_reported: Decimal | None
    cash_reported: Decimal | None
    source_file: str
    source_sha256: str
    parser_version: str
    positions: list[Position] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.provider, self.person, self.account_ref, self.period_end)

    def content(self) -> dict:
        """The data that matters for deciding whether two versions differ.

        Provenance (source file, hash, parser version) and the free-text label are
        deliberately excluded.
        """
        return {
            "account_type": self.account_type,
            "period_start": self.period_start,
            "currency": self.currency,
            "portfolio_reported": self.portfolio_reported,
            "holdings_reported": self.holdings_reported,
            "cash_reported": self.cash_reported,
            "positions": tuple(sorted(
                (p.instrument_name, p.ticker, p.quantity, p.value, p.currency)
                for p in self.positions
            )),
        }


def validate(s: Snapshot) -> list[str]:
    """Internal-consistency checks. Returns a list of problems (empty = valid).

    Checks are skipped when the data they need is absent (e.g. a manually entered
    pension total with no positions or cash line).
    """
    problems = []
    if s.account_type not in ACCOUNT_TYPES:
        problems.append(f"unknown account type {s.account_type!r}")
    if s.period_start and s.period_start > s.period_end:
        problems.append("period_start is after period_end")

    names = [p.instrument_name for p in s.positions]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append(f"duplicate instrument names in one statement: {dupes}")
    for p in s.positions:
        if p.currency != s.currency:
            problems.append(f"position currency {p.currency} differs from statement currency {s.currency}")
            break

    if None not in (s.portfolio_reported, s.holdings_reported, s.cash_reported):
        diff = s.portfolio_reported - (s.holdings_reported + s.cash_reported)
        if abs(diff) > PER_ITEM_TOLERANCE:
            problems.append(f"portfolio differs from holdings + cash by {diff}")

    if s.holdings_reported is not None and s.positions:
        total = sum((p.value for p in s.positions), Decimal("0"))
        diff = total - s.holdings_reported
        if abs(diff) > PER_ITEM_TOLERANCE * (len(s.positions) + 1):
            problems.append(f"sum of positions differs from total holdings by {diff}")
    return problems
