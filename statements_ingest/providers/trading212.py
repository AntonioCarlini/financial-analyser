"""Adapter for Trading 212 activity statement PDFs (arbitrary date ranges).

One PDF can hold several accounts. Each account contributes:
    snapshot + positions   closing state at the end of the range
    period totals          the 'Overview' figures for the range
    transactions           trades, deposits, free shares and dividends

Conventions worth knowing (inferred from the layout, checked numerically):
    * Prefix 'p' on an amount means pence (currency GBX). Values stored in
      `value`/`amount` columns are always converted to the account currency (GBP).
    * fx_rate is stored as printed. Trades and positions print instrument units
      per pound (pence: 100); dividends print pounds per instrument unit (pence: 0.01).
    * withholding_tax is in the dividend's instrument currency (price_currency).
    * Buy/Sell quantities are signed (sell negative) so trades can be replayed.
    * All timestamps are treated as UTC.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..discovery import ActivityStatementDiscoverer
from ..models import (ParsedAccount, ParsedDocument, ParseProblem, PeriodTotal,
                      Position, Transaction, masked)

# Provider wording -> canonical kind. Unknown wording is rejected by name so that
# a new transaction type forces a deliberate decision. Extend this as you meet them.
RAW_TYPE_KINDS = {
    "truelayer": "deposit",
    "free equity shares": "free_shares",
     "interest on cash": "interest",
}

ISIN = r"[A-Z]{2}[A-Z0-9]{9}\d"
TS = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"
NUM = r"\d[\d,]*(?:\.\d+)?"
ISIN_ANY = re.compile(rf"\b{ISIN}\b")
TS_START = re.compile(rf"^{TS}\b")

COVERING_RE = re.compile(
    r"covering from (\d\d\.\d\d\.\d{4} \d\d:\d\d) \(UTC\) to (\d\d\.\d\d\.\d{4} \d\d:\d\d) \(UTC\)")
FOOTER_RE = re.compile(r"^\d+/\d+$")
ACCOUNT_ID_RE = re.compile(r"^Account ID:\s*(\S+)$", re.I)
PRODUCT_RE = re.compile(r"^Trading 212 (?P<name>.+)$")
SECTION_RE = re.compile(
    r"^(?P<name>.+?) account\s+[-–—]\s+"
    r"(?P<section>executed trades|open positions summary|cash breakdown|transactions and dividends)$", re.I)
OVERVIEW_RE = re.compile(rf"^(?P<label>[A-Za-z][A-Za-z \-]*?)\s*(?P<amt>-?£{NUM})$")
CASH_RE = re.compile(r"^GBP (?P<kind>settled cash|cash)\s+(?P<amt>\S+)$", re.I)

TRADE_RE = re.compile(
    rf"^(?P<ts>{TS})\s+(?P<ticker>\S+)\s+(?P<isin>{ISIN})\s+(?P<order_id>\d+)\s+"
    rf"(?P<direction>Buy|Sell)\s+(?P<qty>{NUM})\s+(?P<price>\S+)\s+(?P<value>\S+)\s+"
    rf"(?P<order_type>Stock split|Stop limit|\S+)\s+(?P<venue>\S+)\s+(?P<session>.+?)\s+"
    rf"(?P<fx>\S+)\s+(?P<fx_fee>\S+)\s+(?P<govt_fees>\S+)\s+(?P<ret>\S+)\s+(?P<value_acc>\S+)$")
POS_RE = re.compile(
    rf"^(?P<ticker>\S+)\s+(?P<isin>{ISIN})\s+(?P<qty>{NUM})\s+(?P<avg>\S+)\s+(?P<price>\S+)\s+"
    rf"(?P<ret_i>\S+)\s+(?P<val_i>\S+)\s+(?P<fx>\S+)\s+(?P<ret_a>\S+)\s+(?P<val_a>\S+)$")
TXN_RE = re.compile(rf"^(?P<ts>{TS})\s+(?P<type>.+?)\s+(?P<amt>-?[£€$p]{NUM})$")
DIV_RE = re.compile(
    rf"^(?P<ticker>\S+)\s+(?P<isin>{ISIN})\s+(?P<country>.+?)\s+(?P<elig>{NUM})\s+"
    rf"(?P<date>\d\d\.\d\d\.\d{{4}})\s+(?P<time>\d\d:\d\d)\s+(?P<aps>\S+)\s+(?P<fx>\S+)\s+"
    rf"(?P<tot>\S+)\s+(?P<wht_rate>\d+(?:\.\d+)?%)\s+(?P<wht>\S+)\s+(?P<tot_acc>\S+)$")

_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD", "p": "GBX"}
_AMOUNT_RE = re.compile(rf"^(-?)([£€$p])({NUM})$")


# ---- token helpers -------------------------------------------------------

def number(tok: str):
    if tok == "-":
        return None
    try:
        return Decimal(tok.replace(",", ""))
    except InvalidOperation:
        raise ParseProblem(f"unrecognised number {masked(tok)!r}")


def money(tok: str):
    """'£1,234.50' -> (Decimal, 'GBP'); '-' -> None."""
    if tok == "-":
        return None
    m = _AMOUNT_RE.match(tok)
    if not m:
        raise ParseProblem(f"unrecognised amount {masked(tok)!r}")
    value = Decimal(m.group(3).replace(",", ""))
    return (-value if m.group(1) else value), _SYMBOLS[m.group(2)]


def gbp(tok: str):
    r = money(tok)
    if r is None:
        return None
    if r[1] != "GBP":
        raise ParseProblem(f"expected a pound amount, got {masked(tok)!r}")
    return r[0]


def gbp_pence_aware(tok: str):
    """Value in pounds, converting a pence amount (prefix p) to pounds."""
    r = money(tok)
    if r is None:
        return None
    return r[0] / 100 if r[1] == "GBX" else r[0]


def utc_range_point(s: str) -> str:
    return datetime.strptime(s, "%d.%m.%Y %H:%M").strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_utc(ts: str) -> str:
    return ts.replace(" ", "T") + "Z"


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def conv_ok(instr, fx, acc) -> bool:
    """Does acc equal instr converted by fx, in either direction?"""
    if instr is None or fx is None or acc is None or fx == 0:
        return False
    tol = max(Decimal("0.02"), abs(acc) * Decimal("0.002"))
    return abs(instr / fx - acc) <= tol or abs(instr * fx - acc) <= tol


def product_type(name: str):
    if "ISA" in name.upper():
        return "ISA"
    if name.lower().startswith("invest"):
        return "GIA"
    return None


# ---- row handlers --------------------------------------------------------

def parse_trade(m) -> Transaction:
    sign = -1 if m["direction"] == "Sell" else 1
    price, price_ccy = money(m["price"]) or (None, "")
    value_acc = gbp(m["value_acc"])
    if value_acc is None:
        raise ParseProblem("trade row has no account-currency value")
    is_split = m["order_type"].lower() == "stock split"
    return Transaction(
        timestamp=iso_utc(m["ts"]),
        kind="corporate_action" if is_split else ("sell" if sign < 0 else "buy"),
        raw_type=m["order_type"],
        ticker=m["ticker"], isin=m["isin"],
        quantity=sign * Decimal(m["qty"].replace(",", "")),
        price=price, price_currency=price_ccy,
        fx_rate=number(m["fx"]),
        amount=None if is_split else -sign * value_acc,   # a buy is cash out
        currency="GBP",
        fees=gbp(m["govt_fees"]), fx_fee=gbp(m["fx_fee"]),
        external_id=m["order_id"],
    )


def parse_position(m, acct: ParsedAccount) -> Position:
    price, price_ccy = money(m["price"]) or (None, "")
    avg = money(m["avg"])
    val_i, val_a, fx = money(m["val_i"]), gbp(m["val_a"]), number(m["fx"])
    if val_a is None:
        raise ParseProblem("position row has no account-currency value")
    if not conv_ok(val_i[0] if val_i else None, fx, val_a):
        acct.notes.append(f"position {m['ticker']}: instrument value, fx rate and account value disagree")
    return Position(
        instrument_name=m["ticker"], ticker=m["ticker"], isin=m["isin"],
        quantity=Decimal(m["qty"].replace(",", "")), value=val_a, currency="GBP",
        price=price, avg_price=avg[0] if avg else None, price_currency=price_ccy, fx_rate=fx,
    )


def parse_dividend(m, acct: ParsedAccount) -> Transaction:
    aps, aps_ccy = money(m["aps"]) or (None, "")
    tot = money(m["tot"])
    wht = money(m["wht"])
    tot_acc, fx = gbp(m["tot_acc"]), number(m["fx"])
    elig = Decimal(m["elig"].replace(",", ""))
    tot_v = tot[0] if tot else None
    if aps is not None and tot_v is not None:
        if abs(elig * aps - tot_v) > max(Decimal("0.01"), abs(tot_v) * Decimal("0.001")):
            acct.notes.append(f"dividend {m['ticker']}: eligible holdings x amount per share != total")
    net = tot_v - wht[0] if (tot_v is not None and wht) else tot_v
    if not (conv_ok(tot_v, fx, tot_acc) or conv_ok(net, fx, tot_acc)):
        acct.notes.append(f"dividend {m['ticker']}: instrument total, fx rate and account total disagree")
    when = datetime.strptime(f"{m['date']} {m['time']}", "%d.%m.%Y %H:%M")
    return Transaction(
        timestamp=when.strftime("%Y-%m-%dT%H:%M:%SZ"), kind="dividend", raw_type="Dividend",
        ticker=m["ticker"], isin=m["isin"], quantity=elig, price=aps, price_currency=aps_ccy,
        fx_rate=fx, amount=tot_acc, currency="GBP", withholding_tax=wht[0] if wht else None,
    )


def parse_flow_row(m) -> Transaction:
    raw = m["type"].strip()
    kind = RAW_TYPE_KINDS.get(raw.lower())
    if kind is None:
        raise ParseProblem(
            f"unrecognised transaction type {raw!r}: add it to RAW_TYPE_KINDS in providers/trading212.py")
    return Transaction(timestamp=iso_utc(m["ts"]), kind=kind, raw_type=raw,
                       amount=gbp(m["amt"]), currency="GBP")


def handle_line(acct: ParsedAccount, section: str, ln: str) -> None:
    if section == "overview":
        m = OVERVIEW_RE.match(ln)
        if m:
            label, value = m["label"].strip(), gbp(m["amt"])
            if label.lower() == "account value":
                acct.portfolio = value
            else:
                acct.totals.append(PeriodTotal(slug(label), value, "GBP"))
    elif section == "executed trades":
        m = TRADE_RE.match(ln)
        if m:
            acct.transactions.append(parse_trade(m))
        elif TS_START.match(ln) or ISIN_ANY.search(ln):
            raise ParseProblem(f"unparsed row in executed trades: {masked(ln)}")
    elif section == "open positions summary":
        m = POS_RE.match(ln)
        if m:
            acct.positions.append(parse_position(m, acct))
        elif ISIN_ANY.search(ln):
            raise ParseProblem(f"unparsed row in open positions: {masked(ln)}")
    elif section == "cash breakdown":
        m = CASH_RE.match(ln)
        if m and m["kind"].lower() == "cash":
            acct.cash = gbp(m["amt"])
    elif section == "transactions and dividends":
        m = TXN_RE.match(ln)
        if m:
            acct.transactions.append(parse_flow_row(m))
            return
        m = DIV_RE.match(ln)
        if m:
            acct.transactions.append(parse_dividend(m, acct))
        elif TS_START.match(ln) or ISIN_ANY.search(ln):
            raise ParseProblem(f"unparsed row in transactions/dividends: {masked(ln)}")


# ---- soft cross-checks (reported, never block storing) -------------------

def run_checks(a: ParsedAccount) -> None:
    n = len(a.positions)
    if a.portfolio is not None and a.cash is not None:
        diff = sum((p.value for p in a.positions), Decimal("0")) + a.cash - a.portfolio
        if abs(diff) > Decimal("0.01") * (n + 2):
            a.notes.append(f"positions + cash differ from account value by {diff}")

    def total(kind, attr="amount"):
        return sum((getattr(t, attr) for t in a.transactions
                    if (kind is None or t.kind == kind) and getattr(t, attr) is not None), Decimal("0"))

    overview = {t.measure: t.amount for t in a.totals}
    derived = {
        "deposits": total("deposit"),
        "bonus": total("free_shares"),
        "dividends_and_distributions": total("dividend"),
        "interest_on_cash": total("interest"),
        "withdrawals": -total("withdrawal"),
        "fx_fee": -total(None, "fx_fee"),
        "third_party_fees": -total(None, "fees"),
    }
    tol = Decimal("0.01") * (len(a.transactions) + 1)
    for measure, value in derived.items():
        if measure in overview:
            a_, b_ = overview[measure], value
            if measure == "withdrawals":
                a_, b_ = abs(a_), abs(b_)
            if abs(a_ - b_) > tol:
                a.notes.append(f"overview {measure} differs from the sum of transactions by {a_ - b_}")


class Trading212Parser:
    name = "trading212"
    version = "1"
    coverage = "ranges"
    discoverer = ActivityStatementDiscoverer()

    def read(self, path: Path) -> ParsedDocument:
        return self.parse_text(self.extract_text(path))

    @staticmethod
    def extract_text(path: Path) -> str:
        try:
            import pdfplumber
        except ImportError:
            sys.exit("pdfplumber is required: pip install pdfplumber")
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text(layout=True) or "" for page in pdf.pages)

    @staticmethod
    def parse_text(text: str) -> ParsedDocument:
        doc = ParsedDocument(source_type="activity_statement")
        start = end = None
        m = COVERING_RE.search(text)
        if m:
            start, end = utc_range_point(m.group(1)), utc_range_point(m.group(2))
        else:
            doc.warnings.append("covering date range not found")

        lines = [ln.strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln and not FOOTER_RE.match(ln)]

        by_name: dict[str, ParsedAccount] = {}
        current, section, i = None, None, 0
        while i < len(lines):
            ln = lines[i]
            if ln == "Glossary":
                break          # glossary and disclosures carry nothing we need
            pm = PRODUCT_RE.match(ln)
            am = ACCOUNT_ID_RE.match(lines[i + 1]) if pm and i + 1 < len(lines) else None
            if pm and am:      # 'Trading 212 <product>' followed by 'Account ID: ...'
                name = pm["name"].strip()
                current = ParsedAccount(
                    account_type=product_type(name), account_ref=am.group(1), as_of=end,
                    currency="GBP", period_start=start, period_end=end)
                if current.account_type is None:
                    current.warnings.append(f"unrecognised Trading 212 product {name!r}")
                by_name[name.casefold()] = current
                doc.accounts.append(current)
                section = "overview"
                i += 2
                continue
            sm = SECTION_RE.match(ln)
            if sm:
                current = by_name.get(sm["name"].strip().casefold())
                section = sm["section"].lower() if current else None
                if current is None:
                    doc.warnings.append(f"section for unknown account {sm['name']!r}")
                i += 1
                continue
            if current is not None and section:
                try:
                    handle_line(current, section, ln)
                except ParseProblem as e:
                    current.warnings.append(str(e))
            i += 1

        if not doc.accounts:
            doc.warnings.append("no account sections found")
        for a in doc.accounts:
            if a.portfolio is None:
                a.warnings.append("account value not found")
            if a.cash is None:
                a.notes.append("GBP cash line not found")
            run_checks(a)
        return doc
