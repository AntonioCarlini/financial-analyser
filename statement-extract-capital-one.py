#!/usr/bin/env python3

"""
statement-extract-capital-one.py

Extract transaction data from Capital One credit card PDF statements and output a CSV suitable for further analysis.

Dependencies:
    - pdfplumber

Usage:
    python statement-extract-capital-one.py path/to/statement.pdf [...] > output.csv

Input:
    Capital One UK credit card PDF statements.

Output CSV columns:
    statement_date  - The statement closing date.
    posting_date    - The date the transaction was posted to the account.
    transaction_date - The date the transaction occurred (if available).
    description     - Merchant description and location.
    credit          - Amount credited (positive, if applicable).
    debit           - Amount debited (positive, if applicable).

Parsing behaviour:
    - Identifies transaction lines starting with "DD Mon" (e.g., "22 Apr").
    - Credits are currently identified by "Direct Debit Payment" in the description.
    - Amounts are extracted and assigned to debit or credit accordingly.

Licence: Apache License 2.0
"""

import argparse
import csv
import datetime as dt
import pdfplumber
import re
import sys
from pathlib import Path

MONTHS = {
    "Jan": 1, "January": 1,
    "Feb": 2, "February": 2,
    "Mar": 3, "March": 3,
    "Apr": 4, "April": 4,
    "May": 5,
    "Jun": 6, "June": 6,
    "Jul": 7, "July": 7,
    "Aug": 8, "August": 8,
    "Sep": 9, "September": 9,
    "Oct": 10, "October": 10,
    "Nov": 11, "November": 11,
    "Dec": 12, "December": 12,
}

STATEMENT_RE = re.compile(
    r"Statement date\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{2})"
)

TRANSACTION_RE = re.compile(
    r"""
    ^
    (?P<posting>\d{2}\s+[A-Za-z]{3})
    \s+
    (?P<body>.+)
    \s+
    (?P<amount>\d[\d,]*\.\d{2})
    $
    """,
    re.VERBOSE,
)

ON_DATE_RE = re.compile(
    r"""
    ^

    (?P<description>.*?)
    \s+on\s+
    (?P<trans>\d{2}\s+[A-Za-z]{3})
    $
    """,
    re.VERBOSE,
)


def warning(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def parse_statement_date(text):
    # Try the original pattern first (now accepts any month name)
    m = STATEMENT_RE.search(text)
    if m:
        day = int(m.group(1))
        month_str = m.group(2).capitalize()
        month = MONTHS.get(month_str)
        if month is None:
            raise ValueError(f"Unknown month: {month_str}")
        year = 2000 + int(m.group(3))
        return dt.date(year, month, day)

    # Fallback: "Available to spend as at DD/MM/YY"
    pattern2 = re.compile(r"Available to spend as at (\d{2})/(\d{2})/(\d{2})")
    m = pattern2.search(text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = 2000 + int(m.group(3))
        return dt.date(year, month, day)

    # Fallback: "as at DD/MM/YY"
    pattern3 = re.compile(r"as at (\d{2})/(\d{2})/(\d{2})")
    m = pattern3.search(text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = 2000 + int(m.group(3))
        return dt.date(year, month, day)

    raise ValueError("cannot locate statement date")

def parse_partial_date(text, statement_date):

    day_s, month_s = text.split()

    day = int(day_s)
    month = MONTHS[month_s]

    year = statement_date.year

    #
    # January statement may contain December transactions.
    #
    if month > statement_date.month:
        year -= 1

    return dt.date(year, month, day)

def is_transaction_line(line):
    return bool(re.match(r"^\d{2}\s+[A-Za-z]{3}\s+", line))


def is_credit(description):
    return "Direct Debit Payment" in description


def extract_transactions(pdf_file):

    lines = []

    with pdfplumber.open(pdf_file) as pdf:

        statement_date = None

        for page in pdf.pages:

            text = page.extract_text()

            if not text:
                continue

            if statement_date is None:
                statement_date = parse_statement_date(text)

            for line in text.splitlines():
                line = line.strip()

                if not is_transaction_line(line):
                    continue

                if "STATEMENT TOTALS" in line:
                    continue

                m = TRANSACTION_RE.match(line)

                if not m:
                    continue

                posting_text = m.group("posting")
                body = m.group("body")
                amount_text = m.group("amount")

                transaction_date = ""

                m2 = ON_DATE_RE.match(body)

                if m2:
                    description = m2.group("description")
                    transaction_date = parse_partial_date(
                        m2.group("trans"),
                        statement_date,
                    ).isoformat()
                else:
                    description = body

                posting_date = parse_partial_date(
                    posting_text,
                    statement_date,
                )

                amount = float(amount_text.replace(",", ""))

                credit = ""
                debit = ""

                if is_credit(description):
                    credit = f"{amount:.2f}"
                else:
                    debit = f"{amount:.2f}"

                lines.append(
                    {
                        "statement_date": statement_date,
                        "posting_date": posting_date.isoformat(),
                        "transaction_date": transaction_date,
                        "description": description,
                        "credit": credit,
                        "debit": debit,
                    }
                )

    if statement_date is None:
        raise ValueError(f"{pdf_file}: no statement date found")

    return statement_date, lines


def check_dates(statement_dates):

    previous = None

    for current in statement_dates:

        if previous:

            if current <= previous:
                warning(
                    f"statement order problem: "
                    f"{current} follows {previous}"
                )

            #
            # Capital One statements are approximately monthly.
            #
            months = (
                (current.year - previous.year) * 12
                + current.month
                - previous.month
            )

            if months > 1:
                warning(
                    f"possible missing statement between "
                    f"{previous} and {current}"
                )

        previous = current

    if len(set(statement_dates)) != len(statement_dates):
        warning("duplicate statement dates detected")


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "pdfs",
        nargs="+",
        help="Capital One statement PDFs",
    )

    args = parser.parse_args()

    writer = csv.writer(sys.stdout)

    writer.writerow(
        [
            "statement_date",
            "posting_date",
            "transaction_date",
            "description",
            "credit",
            "debit",
        ]
    )

    statement_dates = []

    for filename in args.pdfs:

        path = Path(filename)

        try:
            statement_date, rows = extract_transactions(path)

        except Exception as e:
            warning(f"{path}: {e}")
            continue

        statement_dates.append(statement_date)

        for row in rows:
            writer.writerow(
                [
                    row["statement_date"],
                    row["posting_date"],
                    row["transaction_date"],
                    row["description"],
                    row["credit"],
                    row["debit"],
                ]
            )

    check_dates(statement_dates)


if __name__ == "__main__":
    main()
