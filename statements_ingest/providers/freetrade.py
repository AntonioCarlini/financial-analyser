"""Adapter for Freetrade monthly statement PDFs."""
from __future__ import annotations

import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..models import ParsedStatement, Position, normalise_account_type

TITLE_RE = re.compile(r"^\s*([A-Za-z]+)\s+(\d{4})\s+(\S+)\s+statement\s*$", re.I | re.M)
ACCOUNT_RE = re.compile(r"Account reference:\s*(\S+)", re.I)
ROW_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?P<qty>-?\d[\d,]*(?:\.\d+)?)\s+£\s*(?P<value>-?[\d,]+\.\d{2})\s*$"
)
TICKER_RE = re.compile(r"\(([^()]+)\)\s*$")


def _money(text: str, label: str):
    m = re.search(rf"^\s*{label}\s+£\s*(-?[\d,]+\.\d{{2}})\s*$", text, re.I | re.M)
    return Decimal(m.group(1).replace(",", "")) if m else None


class FreetradeParser:
    name = "freetrade"
    version = "1"
    statement_interval_months = 1

    def read(self, path: Path) -> ParsedStatement:
        return self.parse_text(self.extract_text(path))

    @staticmethod
    def extract_text(path: Path) -> str:
        try:
            import pdfplumber
        except ImportError:
            sys.exit("pdfplumber is required: pip install pdfplumber")
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text(layout=True) or "" for page in pdf.pages)

    @staticmethod
    def parse_text(text: str) -> ParsedStatement:
        r = ParsedStatement()

        m = TITLE_RE.search(text)
        if m:
            month_name, year, type_token = m.groups()
            r.account_type = normalise_account_type(type_token)
            if r.account_type is None:
                r.warnings.append(f"unrecognised account type {type_token!r} in title")
            try:
                r.month = datetime.strptime(month_name, "%B").month
                r.year = int(year)
            except ValueError:
                r.warnings.append(f"could not parse month {month_name!r}")
        else:
            r.warnings.append("statement title line not found")

        m = ACCOUNT_RE.search(text)
        if m:
            r.account_ref = m.group(1)
        # Older statements have no account reference; the caller supplies a fallback.

        r.portfolio = _money(text, "Portfolio")
        r.holdings = _money(text, "Total holdings")
        r.cash = _money(text, "Total cash")
        for label, val in (("portfolio", r.portfolio), ("total holdings", r.holdings), ("total cash", r.cash)):
            if val is None:
                r.warnings.append(f"{label} not found")
        if any(v is not None for v in (r.portfolio, r.holdings, r.cash)):
            r.currency = "GBP"   # amounts are matched on the pound sign

        # Scan every line after the 'Positions' heading. Header and footer lines never
        # end in '<quantity> £<value>' so they don't match; scanning to the end also
        # copes with a table that continues onto a second page.
        lines = text.splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.strip().lower() == "positions"), None)
        if start is not None:
            for ln in lines[start + 1:]:
                row = ROW_RE.match(ln)
                if row:
                    name = row.group("name").strip()
                    t = TICKER_RE.search(name)
                    r.positions.append(Position(
                        instrument_name=name,
                        ticker=t.group(1) if t else "",
                        quantity=Decimal(row.group("qty").replace(",", "")),
                        value=Decimal(row.group("value").replace(",", "")),
                        currency="GBP",
                    ))
        if r.holdings and r.holdings > 0 and not r.positions:
            r.warnings.append("holdings are non-zero but no position rows were parsed")
        return r
