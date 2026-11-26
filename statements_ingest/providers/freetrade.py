"""Adapter for Freetrade monthly statement PDFs."""
from __future__ import annotations

import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..discovery import MonthlyFilenameDiscoverer
from ..models import (ParsedAccount, ParsedDocument, Position, month_end,
                      normalise_account_type)

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
    version = "2"
    coverage = "monthly"
    discoverer = MonthlyFilenameDiscoverer()

    def read(self, path: Path) -> ParsedDocument:
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
    def parse_text(text: str) -> ParsedDocument:
        a = ParsedAccount()

        m = TITLE_RE.search(text)
        if m:
            month_name, year, type_token = m.groups()
            a.account_type = normalise_account_type(type_token)
            if a.account_type is None:
                a.warnings.append(f"unrecognised account type {type_token!r} in title")
            try:
                month = datetime.strptime(month_name, "%B").month
                a.as_of = month_end(int(year), month).isoformat()
            except ValueError:
                a.warnings.append(f"could not parse month {month_name!r}")
        else:
            a.warnings.append("statement title line not found")

        # Older statements have no account reference; the caller supplies a fallback.
        m = ACCOUNT_RE.search(text)
        a.account_ref = m.group(1) if m else None

        a.portfolio = _money(text, "Portfolio")
        a.holdings = _money(text, "Total holdings")
        a.cash = _money(text, "Total cash")
        for label, val in (("portfolio", a.portfolio), ("total holdings", a.holdings), ("total cash", a.cash)):
            if val is None:
                a.warnings.append(f"{label} not found")
        if any(v is not None for v in (a.portfolio, a.holdings, a.cash)):
            a.currency = "GBP"   # amounts are matched on the pound sign

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
                    a.positions.append(Position(
                        instrument_name=name,
                        ticker=t.group(1) if t else "",
                        quantity=Decimal(row.group("qty").replace(",", "")),
                        value=Decimal(row.group("value").replace(",", "")),
                        currency="GBP",
                    ))
        if any(not pos.ticker for pos in a.positions):
            a.notes.append("a holding name has no bracketed ticker; it may have wrapped onto two lines")
        if a.holdings and a.holdings > 0 and not a.positions:
            a.warnings.append("holdings are non-zero but no position rows were parsed")
        return ParsedDocument(source_type="statement", accounts=[a])
