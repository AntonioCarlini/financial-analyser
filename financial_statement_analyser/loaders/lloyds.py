
import csv
from datetime import datetime
from decimal import Decimal

from financial_statement_analyser.core.types import Transaction
from financial_statement_analyser.core.utils import (
    print_pass,
    print_warning,
    parse_decimal,
)


# BGC  - Bank Giro Credit: electronic deposit
# CHQ  - Payment to someone else by cheque
# COR  - Correction by bank
# CPT  - Cashpoint withdrawl
# CSH  - Cash payment into account
# DD   - Direct debit payment (out)
# DEB  -
# DEP  - Deposit of cheque
# FPI  - Fast Payment in
# FPO  - Fast Payment out
# SO   - Standing order
# TFR  - ???

KNOWN_TRANSACTION_TYPES = {
    "BGC",
    "CHQ",
    "COR",
    "CPT",
    "CSH",
    "DD",
    "DEB",
    "DEP",
    "FPI",
    "FPO",
    "SO",
    "TFR",
}

TRANSACTION_RULES = {
    "BGC": {"credit_only": True},
    "CHQ": {"debit_only": True},
    "COR": {},
    "CPT": {"debit_only": True},
    "CSH": {"credit_only": True},
    "DD":  {"debit_only": True},
    "DEB": {"debit_only": True},
    "DEP": {"credit_only": True},
    "FPI": {"credit_only": True},
    "FPO": {"debit_only": True},
    "SO":  {"debit_only": True},
    "TFR": {},
}

def load_statement_lloyds(filename, verbose, stats):
    transactions = []

    expected_sort_code = None
    expected_account_number = None

    unknown_transaction_types = set()

    with open(filename, newline="", encoding="utf-8") as csvfile:
        print_pass(f"Analysing statment {filename}", verbose, stats)
        reader = csv.DictReader(csvfile)

        for line_number, row in enumerate(reader, start=2):

            try:
                date = datetime.strptime(
                    row["Transaction Date"].strip(),
                    "%d/%m/%Y"
                )

                transaction_type = row["Transaction Type"].strip()

                description = row["Transaction Description"].strip()

                sort_code = row["Sort Code"].strip()
                account_number = row["Account Number"].strip()

                debit = parse_decimal(
                    row["Debit Amount"]
                )

                credit = parse_decimal(
                    row["Credit Amount"]
                )

                balance = parse_decimal(
                    row["Balance"]
                )

            except Exception as exc:
                raise RuntimeError(
                    f"Line {line_number}: {exc}"
                )

            if expected_sort_code is None:
                expected_sort_code = sort_code
                expected_account_number = account_number

            if sort_code != expected_sort_code:
                print_warning(
                    f"line {line_number}: unexpected sort code "
                    f"'{sort_code}'",
                    stats,
                )

            if account_number != expected_account_number:
                print_warning(
                    f"line {line_number}: unexpected account number "
                    f"'{account_number}'",
                    stats,
                )

            if transaction_type not in KNOWN_TRANSACTION_TYPES:
                unknown_transaction_types.add(transaction_type)

            transactions.append(
                Transaction(
                    line_number=line_number,
                    date=date,
                    transaction_type=transaction_type,
                    description=description,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    sort_code=sort_code,
                    account_number=account_number,
                )
            )

    for tx_type in sorted(unknown_transaction_types):
        print_warning(
            f"unknown transaction type '{tx_type}'",
            stats,
        )

    return transactions
