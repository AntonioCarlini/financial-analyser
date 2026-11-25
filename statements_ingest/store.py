"""The consolidated store: two date-ordered CSV files in a directory.

snapshots.csv  one row per statement
positions.csv  one row per holding per statement

The store is always loaded, merged by key, sorted and rewritten in full (never
appended to), so late-arriving statements land in the right place and re-running
is idempotent.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Position, Snapshot

SNAPSHOT_FIELDS = [
    "provider", "person", "account_type", "account_label", "account_ref",
    "period_start", "period_end", "currency",
    "portfolio_reported", "holdings_reported", "cash_reported",
    "source_file", "source_sha256", "parser_version",
]
POSITION_FIELDS = [
    "provider", "person", "account_ref", "period_end",
    "instrument_name", "ticker", "quantity", "value", "currency",
]


class StoreError(Exception):
    pass


def _fmt(v) -> str:
    if v is None:
        return ""
    return v.isoformat() if isinstance(v, date) else str(v)


def _dec(s: str):
    return Decimal(s) if s != "" else None


def _date(s: str):
    return date.fromisoformat(s) if s else None


def _check_header(found, expected, path):
    if list(found or []) != expected:
        raise StoreError(f"{path} has unexpected columns; expected {expected}")


class Store:
    SNAPSHOTS = "snapshots.csv"
    POSITIONS = "positions.csv"

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.snapshots: dict[tuple, Snapshot] = {}
        self.dirty = False
        self._load()

    @property
    def _snap_path(self) -> Path:
        return self.directory / self.SNAPSHOTS

    @property
    def _pos_path(self) -> Path:
        return self.directory / self.POSITIONS

    def _load(self) -> None:
        if not self._snap_path.exists():
            if self._pos_path.exists():
                raise StoreError(f"{self._pos_path} exists but {self._snap_path} does not")
            return
        with self._snap_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            _check_header(reader.fieldnames, SNAPSHOT_FIELDS, self._snap_path)
            for r in reader:
                s = Snapshot(
                    provider=r["provider"], person=r["person"],
                    account_type=r["account_type"], account_label=r["account_label"],
                    account_ref=r["account_ref"],
                    period_start=_date(r["period_start"]), period_end=date.fromisoformat(r["period_end"]),
                    currency=r["currency"],
                    portfolio_reported=_dec(r["portfolio_reported"]),
                    holdings_reported=_dec(r["holdings_reported"]),
                    cash_reported=_dec(r["cash_reported"]),
                    source_file=r["source_file"], source_sha256=r["source_sha256"],
                    parser_version=r["parser_version"],
                )
                self.snapshots[s.key] = s
        if self._pos_path.exists():
            with self._pos_path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                _check_header(reader.fieldnames, POSITION_FIELDS, self._pos_path)
                for r in reader:
                    key = (r["provider"], r["person"], r["account_ref"], date.fromisoformat(r["period_end"]))
                    if key not in self.snapshots:
                        raise StoreError(f"{self._pos_path} has a position with no matching snapshot")
                    self.snapshots[key].positions.append(Position(
                        instrument_name=r["instrument_name"], ticker=r["ticker"],
                        quantity=Decimal(r["quantity"]), value=Decimal(r["value"]),
                        currency=r["currency"],
                    ))

    def upsert(self, snap: Snapshot, allow_update: bool) -> tuple[str, list[str]]:
        """Merge one snapshot. Returns (status, names of differing fields)."""
        existing = self.snapshots.get(snap.key)
        if existing is None:
            self.snapshots[snap.key] = snap
            self.dirty = True
            return "ADDED", []
        old, new = existing.content(), snap.content()
        if old == new:
            return "UNCHANGED", []
        differing = [k for k in new if old[k] != new[k]]
        if not allow_update:
            return "CONFLICT", differing
        self.snapshots[snap.key] = snap
        self.dirty = True
        return "UPDATED", differing

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        snaps = sorted(self.snapshots.values(),
                       key=lambda s: (s.period_end, s.provider, s.person, s.account_ref))
        self._write(self._snap_path, SNAPSHOT_FIELDS,
                    ({f: _fmt(getattr(s, f)) for f in SNAPSHOT_FIELDS} for s in snaps))

        def position_rows():
            for s in snaps:
                for p in sorted(s.positions, key=lambda p: p.instrument_name):
                    yield {
                        "provider": s.provider, "person": s.person,
                        "account_ref": s.account_ref, "period_end": _fmt(s.period_end),
                        "instrument_name": p.instrument_name, "ticker": p.ticker,
                        "quantity": _fmt(p.quantity), "value": _fmt(p.value),
                        "currency": p.currency,
                    }
        self._write(self._pos_path, POSITION_FIELDS, position_rows())
        self.dirty = False

    @staticmethod
    def _write(path: Path, fields: list[str], rows) -> None:
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)


def find_gaps(snapshots, step_months: int = 1) -> dict[tuple, list[str]]:
    """Missing periods between the first and last snapshot of each account."""
    months = defaultdict(set)
    for s in snapshots:
        months[(s.provider, s.person, s.account_ref)].add(s.period_end.year * 12 + s.period_end.month - 1)
    gaps = {}
    for key, have in months.items():
        missing = [m for m in range(min(have), max(have) + 1, step_months) if m not in have]
        if missing:
            gaps[key] = [f"{m // 12}-{m % 12 + 1:02d}" for m in missing]
    return gaps
