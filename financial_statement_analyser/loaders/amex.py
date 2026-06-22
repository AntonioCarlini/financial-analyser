from financial_statement_analyser.core.utils import print_error

import csv
from datetime import datetime
from decimal import Decimal

from financial_statement_analyser.core.types import Transaction
from financial_statement_analyser.core.utils import print_pass, print_warning, parse_decimal
from financial_statement_analyser.core.control import get_cardholder_mapping


def load_statement_amex(filename, verbose, stats, control, statement_type):
    """
    Load an American Express credit card statement CSV.

    Expected columns:
        Date,Description,Card Member,Account #,Amount

    Date format: DD/MM/YYYY
    Amount: positive for spend, negative for credits (e.g., payments, refunds)

    The Card Member field is mapped to a person ID via the cardholder_mapping
    in the control file's statement_handling for 'credit-card-amex'.
    """
    transactions = []
    unknown_card_members = set()

    # Get the cardholder mapping for this statement type
    mapping = get_cardholder_mapping(statement_type, control)

    with open(filename, newline="", encoding="utf-8") as csvfile:
        print_pass(f"Analysing Amex statement {filename}", verbose, stats)
        reader = csv.DictReader(csvfile)

        # Check required columns
        expected_columns = {"Date", "Description", "Card Member", "Account #", "Amount"}
        if not expected_columns.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"Amex CSV missing required columns. Expected: {expected_columns}, got: {reader.fieldnames}"
            )

        for line_number, row in enumerate(reader, start=2):
            try:
                # Date
                date_str = row["Date"].strip()
                date = datetime.strptime(date_str, "%d/%m/%Y")

                description = row["Description"].strip()

                card_member_raw = row["Card Member"].strip()
                # Map to person ID
                card_holder = mapping.get(card_member_raw)
                if card_holder is None:
                    unknown_card_members.add(card_member_raw)

                # Account # – ignore for now
                # Amount
                amount_str = row["Amount"].strip()
                amount = Decimal(amount_str)

                # Determine debit/credit
                if amount > 0:
                    debit = amount
                    credit = Decimal("0")
                else:
                    debit = Decimal("0")
                    credit = -amount

                # Balance is not provided – set to 0
                balance = Decimal("0")

                transactions.append(
                    Transaction(
                        line_number=line_number,
                        date=date,
                        transaction_type="AMEX",
                        description=description,
                        debit=debit,
                        credit=credit,
                        balance=balance,
                        sort_code="",
                        account_number=row["Account #"].strip(),
                        card_holder=card_holder,
                    )
                )

            except Exception as exc:
                raise RuntimeError(f"Line {line_number}: {exc}")

    # Report unknown card members
    for member in sorted(unknown_card_members):
        print_warning(f"Unknown Amex card member '{member}' on statement {filename}", stats)

    return transactions
