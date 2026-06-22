import csv
from datetime import datetime
from decimal import Decimal

from financial_statement_analyser.core.types import Transaction
from financial_statement_analyser.core.utils import print_pass, print_warning


# Exact header lists for format detection
FORMAT_A_HEADERS = [
    "statement_date",
    "posting_date",
    "transaction_date",
    "description",
    "credit",
    "debit",
]

FORMAT_B_HEADERS = [
    "date",
    "postedDate",
    "amount",
    "description",
    "recurringPayment",
    "originalCurrencyAmount",
    "conversionRate",
    "type",
    "currency",
    "debitCreditCode",
    "merchant.name",
    "merchant.town",
    "merchant.postCode",
    "merchant.country",
]


def parse_decimal(value):
    """Convert a string to Decimal, handling empty strings and commas."""
    if value is None or value.strip() == "":
        return Decimal("0")
    return Decimal(value.replace(",", ""))


def parse_date_pdf_derived(date_str):
    """Parse YYYY-MM-DD from PDF-derived CSV."""
    return datetime.strptime(date_str.strip(), "%Y-%m-%d")


def parse_date_full_csv(date_str):
    """Parse ISO datetime from official Capital One CSV."""
    # Remove 'Z' and parse as UTC
    return datetime.fromisoformat(date_str.strip().replace("Z", "+00:00"))


def create_transaction(
    line_number,
    date,
    description,
    debit,
    credit,
    account_number="",
):
    """Create a Transaction object with common defaults."""
    return Transaction(
        line_number=line_number,
        date=date,
        transaction_type="CAPITAL_ONE",
        description=description,
        debit=debit,
        credit=credit,
        balance=Decimal("0"),
        sort_code="",
        account_number=account_number,
        card_holder=None,
    )


def load_statement_capital_one_format_a(filename, verbose, stats, control, statement_type):
    """
    Load a PDF-derived Capital One CSV (Format A).

    Columns:
        statement_date, posting_date, transaction_date, description, credit, debit
    """
    transactions = []

    with open(filename, newline="", encoding="utf-8") as csvfile:
        print_pass(f"Analysing Capital One (PDF-derived) statement {filename}", verbose, stats)
        reader = csv.DictReader(csvfile)

        # Verify headers match exactly (we already checked this in the dispatcher)
        for line_number, row in enumerate(reader, start=2):
            try:
                # Date: posting_date is the key field
                date_str = row.get("posting_date", "").strip()
                if not date_str:
                    raise ValueError("Missing posting_date")
                date = parse_date_pdf_derived(date_str)

                # Description
                description = row.get("description", "").strip()

                # Debit / Credit
                debit = parse_decimal(row.get("debit", ""))
                credit = parse_decimal(row.get("credit", ""))

                # Account number not available
                account_number = ""

                transactions.append(
                    create_transaction(
                        line_number=line_number,
                        date=date,
                        description=description,
                        debit=debit,
                        credit=credit,
                        account_number=account_number,
                    )
                )

            except Exception as exc:
                raise RuntimeError(f"Line {line_number}: {exc}")

    transactions.reverse()
    return transactions


def load_statement_capital_one_format_b(filename, verbose, stats, control, statement_type):
    """
    Load an official Capital One CSV from the website (Format B).

    Columns:
        date, postedDate, amount, description, ..., debitCreditCode, merchant.name, ...
    """
    transactions = []

    with open(filename, newline="", encoding="utf-8") as csvfile:
        print_pass(f"Analysing Capital One (official) statement {filename}", verbose, stats)
        reader = csv.DictReader(csvfile)

        for line_number, row in enumerate(reader, start=2):
            try:
                # Date: postedDate is the key field
                date_str = row.get("postedDate", "").strip()
                if not date_str:
                    raise ValueError("Missing postedDate")
                date = parse_date_full_csv(date_str)

                # Description: use the description column (or merchant.name if description is empty)
                description = row.get("description", "").strip()
                if not description:
                    description = row.get("merchant.name", "").strip()

                # Amount (always positive)
                amount = parse_decimal(row.get("amount", ""))

                # Determine debit/credit from debitCreditCode
                debit_credit_code = row.get("debitCreditCode", "").strip()
                if debit_credit_code == "Debit":
                    debit = amount
                    credit = Decimal("0")
                elif debit_credit_code == "Credit":
                    debit = Decimal("0")
                    credit = amount
                else:
                    raise ValueError(f"Unknown debitCreditCode: '{debit_credit_code}'")

                # Account number not available
                account_number = ""

                transactions.append(
                    create_transaction(
                        line_number=line_number,
                        date=date,
                        description=description,
                        debit=debit,
                        credit=credit,
                        account_number=account_number,
                    )
                )

            except Exception as exc:
                raise RuntimeError(f"Line {line_number}: {exc}")

    return transactions


def load_statement_capital_one(filename, verbose, stats, control, statement_type):
    """
    Load a Capital One statement CSV.

    Detects whether the file is Format A (PDF-derived) or Format B (official website).
    If neither, raises an error.
    """
    with open(filename, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        headers = [h.strip() for h in reader.fieldnames]

    # Format detection: exact header match
    if headers == FORMAT_A_HEADERS:
        return load_statement_capital_one_format_a(filename, verbose, stats, control, statement_type)
    elif headers == FORMAT_B_HEADERS:
        return load_statement_capital_one_format_b(filename, verbose, stats, control, statement_type)
    else:
        raise ValueError(
            f"Unknown Capital One CSV format.\n"
            f"Expected Format A headers: {FORMAT_A_HEADERS}\n"
            f"or Format B headers: {FORMAT_B_HEADERS}\n"
            f"Got: {headers}"
        )
