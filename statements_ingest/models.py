"""Canonical data model shared by all provider adapters.

Adapters turn one provider document into a ParsedDocument (one or more
ParsedAccounts). The ingest step adds who/where information (person, label,
provenance) and hands each account to the store.

Three kinds of data, matching three kinds of statement content:
    state   (snapshot + positions)  as at one moment
    flows   (period totals)         one figure per measure over a date range
    events  (transactions)          individual dated items
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ACCOUNT_TYPES = ("GIA", "ISA", "PENSION")

# Provider/filename terms -> canonical tax-wrapper vocabulary.
_ALIASES = {"GIA": "GIA", "ISA": "ISA", "SIPP": "PENSION", "PENSION": "PENSION"}

TRANSACTION_KINDS = (
    "deposit", "withdrawal", "free_shares", "dividend", "interest", "fee",
    "buy", "sell", "corporate_action",
)

PER_ITEM_TOLERANCE = Decimal("0.01")


class ParseProblem(Exception):
    """Part of a document could not be understood."""


def masked(text: str) -> str:
    """Letters -> X, digits -> 9. Safe to put in messages you might paste elsewhere."""
    return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "X", text))


def normalise_account_type(raw: str | None) -> str | None:
    return _ALIASES.get(raw.strip().upper()) if raw else None


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


@dataclass
class Position:
    instrument_name: str
    ticker: str
    quantity: Decimal
    value: Decimal                  # in the account currency
    currency: str                   # currency of `value`
    isin: str = ""
    price: Decimal | None = None    # as the provider prints it, in price_currency
    avg_price: Decimal | None = None
    price_currency: str = ""        # ISO code; GBX for pence
    fx_rate: Decimal | None = None  # as printed by the provider


@dataclass
class PeriodTotal:
    measure: str                    # e.g. deposits, dividends_and_distributions
    amount: Decimal
    currency: str


@dataclass
class Transaction:
    timestamp: str                  # ISO 8601 UTC, e.g. 2026-01-02T10:15:30Z
    kind: str                       # one of TRANSACTION_KINDS
    raw_type: str                   # the provider's own wording
    ticker: str = ""
    isin: str = ""
    quantity: Decimal | None = None     # signed for buy/sell/corporate_action
    price: Decimal | None = None
    price_currency: str = ""
    fx_rate: Decimal | None = None
    amount: Decimal | None = None       # account currency; money in is positive
    currency: str = ""
    fees: Decimal | None = None         # exchange & government fees, account currency
    fx_fee: Decimal | None = None
    withholding_tax: Decimal | None = None
    external_id: str = ""
    seq: int = 0                        # assigned by the store


@dataclass
class ParsedAccount:
    account_type: str | None = None     # canonical (GIA/ISA/PENSION) or None if unrecognised
    account_ref: str | None = None
    as_of: str | None = None            # date or UTC timestamp; None = no snapshot
    currency: str | None = None
    portfolio: Decimal | None = None
    holdings: Decimal | None = None
    cash: Decimal | None = None
    period_start: str | None = None
    period_end: str | None = None
    positions: list[Position] = field(default_factory=list)
    totals: list[PeriodTotal] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # parse problems: account is rejected
    notes: list[str] = field(default_factory=list)     # soft check failures: reported, still stored


@dataclass
class ParsedDocument:
    source_type: str                    # statement, activity_statement, ...
    accounts: list[ParsedAccount] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_account(a: ParsedAccount) -> list[str]:
    """Generic consistency checks. Skipped where the data they need is absent."""
    problems = []
    if a.account_type not in ACCOUNT_TYPES:
        problems.append(f"unknown account type {a.account_type!r}")

    keys = [(p.instrument_name, p.isin) for p in a.positions]
    if len(set(keys)) != len(keys):
        problems.append("duplicate positions within one statement")
    if any(p.currency != a.currency for p in a.positions):
        problems.append("a position's currency differs from the statement currency")

    if None not in (a.portfolio, a.holdings, a.cash):
        diff = a.portfolio - (a.holdings + a.cash)
        if abs(diff) > PER_ITEM_TOLERANCE:
            problems.append(f"portfolio differs from holdings + cash by {diff}")
    if a.holdings is not None and a.positions:
        diff = sum((p.value for p in a.positions), Decimal("0")) - a.holdings
        if abs(diff) > PER_ITEM_TOLERANCE * (len(a.positions) + 1):
            problems.append(f"sum of positions differs from total holdings by {diff}")
    return problems
