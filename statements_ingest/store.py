"""The consolidated store: four date-ordered CSV tables per prefix.

{prefix}_snapshots.csv      state at a moment, one row per account per statement
{prefix}_positions.csv      holdings belonging to a snapshot
{prefix}_period_totals.csv  one measure over a date range (long format)
{prefix}_transactions.csv   individual dated events

The store is always loaded, merged by key, sorted and rewritten in full, so late
arrivals land in order and re-running is idempotent. One account of one document
is applied all-or-nothing.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

SNAPSHOT_FIELDS = [
    "provider", "person", "account_type", "account_label", "account_ref", "source_type",
    "as_of", "currency", "portfolio_reported", "holdings_reported", "cash_reported",
    "source_file", "source_sha256", "parser_version",
]
POSITION_FIELDS = [
    "provider", "person", "account_ref", "source_type", "as_of",
    "instrument_name", "ticker", "isin", "quantity", "value", "currency",
    "price", "avg_price", "price_currency", "fx_rate",
]
TOTAL_FIELDS = [
    "provider", "person", "account_ref", "source_type", "period_start", "period_end",
    "measure", "amount", "currency", "source_file", "source_sha256", "parser_version",
]
TXN_FIELDS = [
    "provider", "person", "account_ref", "source_type", "timestamp", "kind", "raw_type",
    "ticker", "isin", "quantity", "price", "price_currency", "fx_rate", "amount", "currency",
    "fees", "fx_fee", "withholding_tax", "external_id", "seq",
    "source_file", "source_sha256", "parser_version",
]
DEC_FIELDS = {
    "portfolio_reported", "holdings_reported", "cash_reported", "quantity", "value",
    "price", "avg_price", "fx_rate", "amount", "fees", "fx_fee", "withholding_tax",
}
INT_FIELDS = {"seq"}
PROVENANCE = ("source_file", "source_sha256", "parser_version")
IGNORED_FOR_COMPARE = set(PROVENANCE) | {"account_label"}


class StoreError(Exception):
    pass


def _fmt(v) -> str:
    if v is None:
        return ""
    return format(v, "f") if isinstance(v, Decimal) else str(v)


def _parse(name: str, s: str):
    if name in DEC_FIELDS:
        return Decimal(s) if s != "" else None
    if name in INT_FIELDS:
        return int(s) if s != "" else 0
    return s


def _norm(v) -> str:
    if v is None:
        return ""
    return format(v.normalize(), "f") if isinstance(v, Decimal) else str(v)


def _content(row: dict) -> tuple:
    return tuple(sorted((k, _norm(v)) for k, v in row.items() if k not in IGNORED_FOR_COMPARE))


def _pos_content(rows) -> list:
    return sorted(tuple(_norm(r[f]) for f in POSITION_FIELDS) for r in rows)


def skey(r):
    return (r["provider"], r["person"], r["account_ref"], r["source_type"], r["as_of"])


def tkey(r):
    return (r["provider"], r["person"], r["account_ref"], r["source_type"],
            r["period_start"], r["period_end"], r["measure"])


def xkey(r):
    return (r["provider"], r["person"], r["account_ref"], r["source_type"], r["timestamp"],
            r["kind"], r["isin"] or r["ticker"], _norm(r["amount"]), r["external_id"], r["seq"])


class Store:
    def __init__(self, directory: Path, prefix: str):
        self.directory = Path(directory)
        self.prefix = prefix
        self.snapshots: dict = {}
        self.positions: dict = defaultdict(list)
        self.totals: dict = {}
        self.txns: dict = {}
        self.dirty = False
        self._load()

    def path(self, table: str) -> Path:
        return self.directory / f"{self.prefix}_{table}.csv"

    def _read(self, table: str, fields: list[str]) -> list[dict]:
        p = self.path(table)
        if not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if list(reader.fieldnames or []) != fields:
                raise StoreError(f"{p} has unexpected columns; expected {fields}")
            return [{k: _parse(k, v) for k, v in r.items()} for r in reader]

    def _load(self) -> None:
        for r in self._read("snapshots", SNAPSHOT_FIELDS):
            self.snapshots[skey(r)] = r
        for r in self._read("positions", POSITION_FIELDS):
            if skey(r) not in self.snapshots:
                raise StoreError(f"{self.path('positions')} has a position with no matching snapshot")
            self.positions[skey(r)].append(r)
        for r in self._read("period_totals", TOTAL_FIELDS):
            self.totals[tkey(r)] = r
        for r in self._read("transactions", TXN_FIELDS):
            self.txns[xkey(r)] = r

    def add_account(self, ctx: dict, acct, prov: dict, allow_update: bool):
        """Merge one parsed account. Returns (status, counts, differing tables)."""
        base = {k: ctx[k] for k in ("provider", "person", "account_ref", "source_type")}
        counts = {"snapshots": 0, "positions": 0, "period_totals": 0, "transactions": 0}
        writes, differing, changed = [], [], False   # writes: (dict, key, row)
        pos_write = None

        if acct.as_of is not None:
            srow = {**base, "account_type": ctx["account_type"], "account_label": ctx["account_label"],
                    "as_of": acct.as_of, "currency": acct.currency or "",
                    "portfolio_reported": acct.portfolio, "holdings_reported": acct.holdings,
                    "cash_reported": acct.cash, **prov}
            prows = [{**base, "as_of": acct.as_of, **asdict(p)} for p in acct.positions]
            k = skey(srow)
            old = self.snapshots.get(k)
            if old is None:
                writes.append((self.snapshots, k, srow)); pos_write = (k, prows)
            elif (_content(old) != _content(srow)
                  or _pos_content(self.positions.get(k, [])) != _pos_content(prows)):
                differing.append("snapshot/positions"); changed = True
                writes.append((self.snapshots, k, srow)); pos_write = (k, prows)
            if pos_write:
                counts["snapshots"], counts["positions"] = 1, len(prows)

        for t in acct.totals:
            row = {**base, "period_start": acct.period_start or "", "period_end": acct.period_end or "",
                   "measure": t.measure, "amount": t.amount, "currency": t.currency, **prov}
            k, old = tkey(row), self.totals.get(tkey(row))
            if old is None:
                writes.append((self.totals, k, row)); counts["period_totals"] += 1
            elif _content(old) != _content(row):
                differing.append("period_totals"); changed = True
                writes.append((self.totals, k, row)); counts["period_totals"] += 1

        seen = defaultdict(int)
        for t in acct.transactions:
            nat = (t.timestamp, t.kind, t.isin or t.ticker, _norm(t.amount), t.external_id)
            seen[nat] += 1
            row = {**base, **asdict(t), "seq": seen[nat], **prov}
            k, old = xkey(row), self.txns.get(xkey(row))
            if old is None:
                writes.append((self.txns, k, row)); counts["transactions"] += 1
            elif _content(old) != _content(row):
                differing.append("transactions"); changed = True
                writes.append((self.txns, k, row)); counts["transactions"] += 1

        if changed and not allow_update:
            return "CONFLICT", {}, sorted(set(differing))
        for d, k, row in writes:
            d[k] = row
        if pos_write:
            self.positions[pos_write[0]] = pos_write[1]
        if writes:
            self.dirty = True
        status = "UPDATED" if changed else ("ADDED" if writes else "UNCHANGED")
        return status, counts, sorted(set(differing))

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        snaps = sorted(self.snapshots.values(), key=lambda r: (
            r["as_of"], r["provider"], r["person"], r["account_ref"], r["source_type"]))
        self._write("snapshots", SNAPSHOT_FIELDS, snaps)

        def pos_rows():
            for s in snaps:
                yield from sorted(self.positions.get(skey(s), []),
                                  key=lambda r: (r["instrument_name"], r["isin"]))
        self._write("positions", POSITION_FIELDS, pos_rows())
        self._write("period_totals", TOTAL_FIELDS, sorted(self.totals.values(), key=lambda r: (
            r["period_end"], r["period_start"], r["provider"], r["person"], r["account_ref"], r["measure"])))
        self._write("transactions", TXN_FIELDS, sorted(self.txns.values(), key=lambda r: (
            r["timestamp"], r["provider"], r["person"], r["account_ref"], r["kind"], r["seq"])))
        self.dirty = False

    def _write(self, table: str, fields: list[str], rows) -> None:
        path = self.path(table)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(fields)
            for r in rows:
                w.writerow([_fmt(r[k]) for k in fields])
        os.replace(tmp, path)


def find_gaps(snapshot_rows, step_months: int = 1) -> dict:
    """Missing months between the first and last snapshot of each account."""
    months = defaultdict(set)
    for r in snapshot_rows:
        y, m = int(r["as_of"][:4]), int(r["as_of"][5:7])
        months[(r["provider"], r["person"], r["account_ref"])].add(y * 12 + m - 1)
    gaps = {}
    for key, have in months.items():
        missing = [m for m in range(min(have), max(have) + 1, step_months) if m not in have]
        if missing:
            gaps[key] = [f"{m // 12}-{m % 12 + 1:02d}" for m in missing]
    return gaps


def find_range_gaps(total_rows, tolerance=timedelta(minutes=1)) -> dict:
    """Stretches not covered by any date-range statement, per account."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    ranges = defaultdict(set)
    for r in total_rows:
        if r["period_start"] and r["period_end"]:
            ranges[(r["provider"], r["person"], r["account_ref"])].add((r["period_start"], r["period_end"]))
    gaps = {}
    for key, rs in ranges.items():
        covered_to, found = None, []
        for start, end in sorted(rs):
            s, e = datetime.strptime(start, fmt), datetime.strptime(end, fmt)
            if covered_to is not None and s - covered_to > tolerance:
                found.append(f"{covered_to:%Y-%m-%d} to {s:%Y-%m-%d}")
            covered_to = e if covered_to is None else max(covered_to, e)
        if found:
            gaps[key] = found
    return gaps
