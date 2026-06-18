#!/usr/bin/env python3

"""
bank-statement-analyser.py

Analyse a UK bank statement exported as CSV.

Current functionality:

    * Validate CSV structure.
    * Verify transactions are in reverse chronological order.
    * Verify all transactions belong to a single account.
    * Verify the statement covers exactly one UK tax year
      (6 April to 5 April inclusive).
    * Recalculate balances from oldest transaction to newest and
      verify that every balance in the statement is correct.
    * Produce monthly and annual summaries of:
          - money in
          - money out
          - net surplus/deficit

Future functionality:

    * Transaction categorisation.
    * Control-file driven classification rules.
    * Income source identification.
    * HMRC gifting-out-of-income reporting.
    * Multi-account analysis.

Assumptions:

    * Credit Amount increases the account balance.
    * Debit Amount decreases the account balance.
    * All transactions are currently treated equally.
      No attempt is made to distinguish income,
      transfers, gifts, investments, or savings movements.

Author: Antonio
Licence: Private use
"""

import argparse
import csv
import os
import sys
import yaml

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass, field

ALLOWED_DAYS_GAP_AT_START = 9
ALLOWED_DAYS_GAP_AT_END = 5

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


@dataclass
class AnalysisResults:
    pass_count: int = 0
    warning_count: int = 0
    error_count: int = 0


@dataclass
class Transaction:
    line_number: int

    date: datetime

    transaction_type: str
    description: str

    debit: Decimal
    credit: Decimal
    balance: Decimal

    sort_code: str
    account_number: str

@dataclass
class Person:
    id: str
    full_name: str


@dataclass
class Category:
    id: str
    description: str


@dataclass
class MatchCondition:
    type: str   # 'description', 'prefix', (later 'regex', etc.)
    value: str

@dataclass
class Rule:
    id: str
    priority: int
    conditions: list[MatchCondition]   # instead of description
    category: str
    ownership: dict[str, int]
    transaction_types: set[str] | None
    direction: str | None

@dataclass
class ControlFile:
    people: dict[str, Person]

    categories: dict[str, Category]

    rules: list[Rule]

    default_category: str

    default_ownership: dict[str, int]


@dataclass
class CategorySummary:
    category: str

    transaction_count: int = 0

    total_credit: Decimal = Decimal("0")
    total_debit: Decimal = Decimal("0")


@dataclass
@dataclass
class AnalysisResult:
    summaries: dict[str, CategorySummary]
    uncategorised: list[Transaction]
    warnings: list[str]
    category_transactions: dict[str, list[tuple[Transaction, str | None]]] = field(default_factory=dict)

def print_pass(message, verbose, stats):
    stats.pass_count += 1

    if verbose:
        print(f"PASS: {message}")


def print_warning(message, stats):
    stats.warning_count += 1
    print(f"WARNING: {message}")


def print_error(message, stats):
    stats.error_count += 1
    print(f"ERROR: {message}")


def parse_arguments():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--analyse",
        action="store_true",
        help="Analyse transactions using control file",
    )

    parser.add_argument(
        "--control-file",
        help="Future transaction classification rules"
    )

    parser.add_argument(
        "--display-category",
        action="append",
        default=[],
        help="Show all transactions assigned to this category (repeatable)",
    )

    parser.add_argument(
        "--display-description-contains",
        action="append",
        default=[],
        help="Show transactions whose description contains this text (repeatable, OR logic)",
    )
    parser.add_argument(
        "--display-description-prefix",
        action="append",
        default=[],
        help="Show transactions whose description starts with this text (repeatable, OR logic)",
    )
    parser.add_argument(
        "--display-description-suffix",
        action="append",
        default=[],
        help="Show transactions whose description ends with this text (repeatable, OR logic)",
    )

    parser.add_argument(
        "--print-report",
        default=False,
        help="Print report data"
    )

    parser.add_argument(
        "--statement",
        required=True,
        help="CSV bank statement"
    )

    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable verbose output"
    )

    return parser.parse_args()


def parse_decimal(value):
    value = value.strip()

    if value == "":
        return Decimal("0")

    value = value.replace(",", "")

    return Decimal(value)

def load_control_file(filename):

    with open(filename, "r", encoding="utf-8") as infile:
        raw = yaml.safe_load(infile)

    people = {}

    for person_id, person_data in raw["people"].items():

        people[person_id] = Person(
            id=person_id,
            full_name=person_data["full_name"],
        )

    categories = {}

    for category_id, category_data in (
        raw["categories"].items()
    ):

        categories[category_id] = Category(
            id=category_id,
            description=category_data["description"],
        )

    rules = []

    for rule_data in raw.get("rules", []):
        match_data = rule_data["match"]
        conditions = []

        if isinstance(match_data, list):
            for cond in match_data:
                # each cond should be a dict with one key, e.g. {"prefix": "XAARJET"}
                for match_type, match_value in cond.items():
                    conditions.append(MatchCondition(type=match_type, value=match_value.upper()))
        elif isinstance(match_data, dict):
            # Backward compatible: {"description": "..."} or {"prefix": "..."}
            for match_type, match_value in match_data.items():
                conditions.append(MatchCondition(type=match_type, value=match_value.upper()))
        else:
            # If it's a plain string, treat as description (old style)
            conditions.append(MatchCondition(type="description", value=match_data.upper()))

        rules.append(
            Rule(
                id=rule_data["id"],
                priority=rule_data.get("priority", 0),
                conditions=conditions,
                category=rule_data["classify"]["category"],
                ownership=rule_data.get("ownership", {}),
                transaction_types=set(rule_data.get("expect", {}).get("transaction_types", [])) or None,
                direction=rule_data.get("expect", {}).get("direction"),
            )
        )

    rules.sort(
        key=lambda rule: rule.priority,
        reverse=True,
    )

    return ControlFile(
        people=people,
        categories=categories,
        rules=rules,
        default_category=raw["defaults"][
            "category"
        ],
        default_ownership=raw["defaults"][
            "ownership"
        ],
    )

def match_rule(tx, rule):
    # First check description/prefix conditions (OR logic)
    desc_match = False
    for cond in rule.conditions:
        if cond.type == "description":
            if tx.description.upper() == cond.value:
                desc_match = True
                break
        elif cond.type == "prefix":
            if tx.description.upper().startswith(cond.value):
                desc_match = True
                break
    if not desc_match:
        return False

    # Now check transaction_types and direction (AND with description)
    if rule.transaction_types is not None:
        if tx.transaction_type not in rule.transaction_types:
            return False
    if rule.direction is not None:
        if rule.direction == "credit" and tx.credit == 0:
            return False
        if rule.direction == "debit" and tx.debit == 0:
            return False
    return True

def analyse_transactions(
    transactions,
    control,
):

    summaries = {}

    for category_id in control.categories:

        summaries[category_id] = (
            CategorySummary(
                category=category_id
            )
        )

    uncategorised = []

    warnings = []

    category_transactions = {cat_id: [] for cat_id in control.categories}

    for tx in transactions:

        matched_rule = None

        for rule in control.rules:

            if match_rule(
                tx,
                rule,
            ):
                matched_rule = rule
                break

        if matched_rule is None:

            category_id = (
                control.default_category
            )

            uncategorised.append(tx)

        else:

            category_id = (
                matched_rule.category
            )

            if (
                matched_rule.transaction_types
                is not None
                and
                tx.transaction_type
                not in matched_rule.transaction_types
            ):
                warnings.append(
                    f"line {tx.line_number}: "
                    f"{tx.description} "
                    f"unexpected type "
                    f"{tx.transaction_type}"
                )

            if (
                matched_rule.direction
                ==
                "credit"
            ):
                if tx.credit == 0:
                    warnings.append(
                        f"line {tx.line_number}: "
                        f"{tx.description} "
                        f"expected credit"
                    )

            if (
                matched_rule.direction
                ==
                "debit"
            ):
                if tx.debit == 0:
                    warnings.append(
                        f"line {tx.line_number}: "
                        f"{tx.description} "
                        f"expected debit"
                    )

        category_transactions[category_id].append(
            (tx, matched_rule.id if matched_rule else None)
        )
        
        summary = summaries[
            category_id
        ]

        summary.transaction_count += 1
        summary.total_credit += tx.credit
        summary.total_debit += tx.debit

    return AnalysisResult(
        summaries=summaries,
        uncategorised=uncategorised,
        warnings=warnings,
        category_transactions=category_transactions,
    )

def print_analysis_report(
    result,
):

    print()
    print(
        "============================================================"
    )
    print(
        "CATEGORY SUMMARY"
    )
    print(
        "============================================================"
    )
    print()

    print(
        f"{'Category':30}"
        f"{'Count':>8}"
        f"{'In':>15}"
        f"{'Out':>15}"
    )

    print("-" * 60)

    for category_id in sorted(
        result.summaries
    ):

        summary = (
            result.summaries[
                category_id
            ]
        )

        print(
            f"{category_id:30}"
            f"{summary.transaction_count:>8}"
            f"{summary.total_credit:>15,.2f}"
            f"{summary.total_debit:>15,.2f}"
        )

    print()

    print(
        f"Uncategorised transactions: "
        f"{len(result.uncategorised)}"
    )

    print()

    for tx in result.uncategorised:

        amount = (
            tx.credit
            if tx.credit
            else tx.debit
        )

        print(
            f"{tx.date.strftime('%Y-%m-%d')} "
            f"{tx.transaction_type:4} "
            f"{amount:10,.2f} "
            f"{tx.description}"
        )

    if result.warnings:

        print()
        print("WARNINGS")
        print()

        for warning in result.warnings:
            print(warning)

def print_category_debug(result, categories_to_display):
    """Print all transactions that belong to any of the given categories."""
    if not categories_to_display:
        return

    print()
    print("============================================================")
    print("DEBUG: TRANSACTIONS BY CATEGORY")
    print("============================================================")
    print()

    # Print header
    print(f"{'Line':>5}  {'Date':<12}  {'Type':<4}  {'Amount':>10}  {'Rule':<15}  Description")
    print("-" * 90)

    # Gather all transactions from the requested categories
    # and sort by date (preserving original order).
    # Since transactions are already in reverse chronological, keep that.
    printed = 0
    for cat in categories_to_display:
        if cat not in result.category_transactions:
            print(f"Category '{cat}' not found.")
            continue
        entries = result.category_transactions[cat]
        if not entries:
            print(f"Category '{cat}' has no transactions.")
        else:
            print(f"\n=== Category: {cat} ({len(entries)} transactions) ===")
            for tx, rule_id in entries:
                amount = tx.credit if tx.credit else tx.debit
                rule_display = rule_id if rule_id else "UNCAT"
                print(
                    f"{tx.line_number:>5}  "
                    f"{tx.date.strftime('%Y-%m-%d'):<12}  "
                    f"{tx.transaction_type:<4}  "
                    f"{amount:>10,.2f}  "
                    f"{rule_display:<15}  "
                    f"{tx.description}"
                )
                printed += 1

    print()
    print(f"Total displayed: {printed} transactions")

def print_description_debug(result, contains_list, prefix_list, suffix_list):
    """Print all transactions that match any of the description filters."""
    if not (contains_list or prefix_list or suffix_list):
        return

    # Build a master list of all transactions with their category and rule ID
    all_entries = []

    # 1. Categorised transactions
    for cat_id, entries in result.category_transactions.items():
        for tx, rule_id in entries:
            all_entries.append((tx, cat_id, rule_id))

    # 2. Uncategorised transactions
    for tx in result.uncategorised:
        all_entries.append((tx, "UNCATEGORISED", None))

    # Apply filters
    matches = []
    for tx, category, rule_id in all_entries:
        desc = tx.description.upper()
        matched = False

        # Check contains
        for pattern in contains_list:
            if pattern.upper() in desc:
                matched = True
                break

        # Check prefix
        if not matched:
            for pattern in prefix_list:
                if desc.startswith(pattern.upper()):
                    matched = True
                    break

        # Check suffix
        if not matched:
            for pattern in suffix_list:
                if desc.endswith(pattern.upper()):
                    matched = True
                    break

        if matched:
            matches.append((tx, category, rule_id))

    if not matches:
        print("\nNo transactions matched the description filters.")
        return

    # Print results
    print()
    print("============================================================")
    print("DEBUG: TRANSACTIONS BY DESCRIPTION FILTER")
    print("============================================================")
    print()

    # Header
    print(f"{'Line':>5}  {'Date':<12}  {'Type':<4}  {'Amount':>10}  {'Category':<22}  {'Rule':<20}  Description")
    print("-" * 110)

    # Sort by line number (or date – your choice)
    matches.sort(key=lambda x: x[0].line_number)

    for tx, category, rule_id in matches:
        amount = tx.credit if tx.credit else tx.debit
        rule_display = rule_id if rule_id else "N/A"
        cat_display = category[:22]  # truncate to fit column
        print(
            f"{tx.line_number:>5}  "
            f"{tx.date.strftime('%Y-%m-%d'):<12}  "
            f"{tx.transaction_type:<4}  "
            f"{amount:>10,.2f}  "
            f"{cat_display:<22}  "
            f"{rule_display:<20}  "
            f"{tx.description}"
        )

    print()
    print(f"Total displayed: {len(matches)} transactions")

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

def validate_transaction_types(
    transactions,
    verbose,
    stats,
):
    seen_types = set()

    for tx in transactions:

        tx_type = tx.transaction_type

        seen_types.add(tx_type)

        if tx_type not in TRANSACTION_RULES:
            print_warning(
                f"unknown transaction type "
                f"'{tx_type}' "
                f"on line {tx.line_number}",
                stats,
            )
            continue

        rules = TRANSACTION_RULES[tx_type]

        if rules.get("credit_only"):

            if tx.debit != 0:
                print_warning(
                    f"line {tx.line_number}: "
                    f"{tx_type} expected money in "
                    f"but debit amount is "
                    f"£{tx.debit:,.2f}",
                    stats,
                )

        if rules.get("debit_only"):

            if tx.credit != 0:
                print_warning(
                    f"line {tx.line_number}: "
                    f"{tx_type} expected money out "
                    f"but credit amount is "
                    f"£{tx.credit:,.2f}",
                    stats,
                )

    print_pass(
        f"{len(seen_types)} transaction types analysed",
        verbose,
        stats,
    )

def load_statement(filename, stats):
    transactions = []

    expected_sort_code = None
    expected_account_number = None

    unknown_transaction_types = set()

    with open(filename, newline="", encoding="utf-8") as csvfile:
        print_pass(f"Analysing statment {filename}", True, stats)
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


def verify_reverse_chronological_order(
    transactions,
    verbose,
    stats,
):
    previous = None

    for tx in transactions:

        if previous is not None:

            if tx.date > previous:
                raise RuntimeError(
                    "Statement is not in reverse "
                    "chronological order"
                )

        previous = tx.date

    print_pass(
        "statement is in reverse chronological order",
        verbose,
        stats,
    )


def verify_tax_year(transactions, verbose, stats):

    newest = transactions[0].date
    oldest = transactions[-1].date

    start_year = oldest.year

    if oldest.month < 4:
        start_year -= 1

    if oldest.month == 4 and oldest.day < 6:
        start_year -= 1

    expected_start = datetime(start_year, 4, 6)
    expected_end = datetime(start_year + 1, 4, 5)

    start_gap = (oldest.date() - expected_start.date()).days

    if start_gap > ALLOWED_DAYS_GAP_AT_START:
        print_warning(
            f"statement starts on "
            f"{oldest.strftime('%d-%b-%Y')} "
            f"({start_gap} days after expected start "
            f"{expected_start.strftime('%d-%b-%Y')})",
            stats,
        )

    end_gap = (expected_end.date() - newest.date()).days

    if end_gap > ALLOWED_DAYS_GAP_AT_END:
        print_warning(
            f"statement ends on "
            f"{newest.strftime('%d-%b-%Y')} "
            f"({end_gap} days before expected end "
            f"{expected_end.strftime('%d-%b-%Y')})",
            stats,
        )

    print_pass(
        f"tax year appears to be "
        f"{start_year}/{str(start_year + 1)[2:]}",
        verbose,
        stats,
    )

    return start_year


def verify_balances(transactions, verbose, stats):

    chronological = list(reversed(transactions))

    first_tx = chronological[0]

    opening_balance = (
        first_tx.balance
        - first_tx.credit
        + first_tx.debit
    )

    running_balance = opening_balance

    checked = 0

    for tx in chronological:

        calculated_balance = (
            running_balance
            + tx.credit
            - tx.debit
        )

        if calculated_balance != tx.balance:
            raise RuntimeError(
                f"Balance mismatch on line "
                f"{tx.line_number}: "
                f"expected {tx.balance} "
                f"calculated {calculated_balance}"
            )

        running_balance = tx.balance
        checked += 1

    closing_balance = chronological[-1].balance

    print_pass(
        f"{checked} balances verified",
        verbose,
        stats,
    )

    return opening_balance, closing_balance


def calculate_monthly_totals(transactions):

    monthly = defaultdict(
        lambda: {
            "money_in": Decimal("0"),
            "money_out": Decimal("0"),
        }
    )

    total_in = Decimal("0")
    total_out = Decimal("0")

    for tx in transactions:

        month_key = tx.date.strftime("%Y-%m")

        monthly[month_key]["money_in"] += tx.credit
        monthly[month_key]["money_out"] += tx.debit

        total_in += tx.credit
        total_out += tx.debit

    return monthly, total_in, total_out


def print_report(
    monthly,
    total_in,
    total_out,
    opening_balance,
    closing_balance,
):

    MONTH_WIDTH = 10
    AMOUNT_WIDTH = 12
    COLUMN_GAP = " " * 5

    print()
    print("============================================================")
    print("MONTHLY SUMMARY")
    print("============================================================")
    print()

    print(
        f"{'Month':<{MONTH_WIDTH}}"
        f"{COLUMN_GAP}"
        f"{'Money In':>{AMOUNT_WIDTH}}"
        f"{COLUMN_GAP}"
        f"{'Money Out':>{AMOUNT_WIDTH}}"
        f"{COLUMN_GAP}"
        f"{'Net':>{AMOUNT_WIDTH}}"
    )

    print("-" * 60)

    for month in sorted(monthly):

        money_in = monthly[month]["money_in"]
        money_out = monthly[month]["money_out"]
        net = money_in - money_out

        print(
            f"{month:<{MONTH_WIDTH}}"
            f"{COLUMN_GAP}"
            f"£{money_in:>{AMOUNT_WIDTH-1},.2f}"
            f"{COLUMN_GAP}"
            f"£{money_out:>{AMOUNT_WIDTH-1},.2f}"
            f"{COLUMN_GAP}"
            f"£{net:>{AMOUNT_WIDTH-1},.2f}"
        )

    print("-" * 60)

    net_total = total_in - total_out

    print(
        f"{'TOTAL':<{MONTH_WIDTH}}"
        f"{COLUMN_GAP}"
        f"£{total_in:>{AMOUNT_WIDTH-1},.2f}"
        f"{COLUMN_GAP}"
        f"£{total_out:>{AMOUNT_WIDTH-1},.2f}"
        f"{COLUMN_GAP}"
        f"£{net_total:>{AMOUNT_WIDTH-1},.2f}"
    )

    print()
    print("============================================================")
    print("LEDGER RECONCILIATION")
    print("============================================================")
    print()

    print(f"Opening balance : £{opening_balance:,.2f}")
    print(f"Closing balance : £{closing_balance:,.2f}")
    print(f"Money in        : £{total_in:,.2f}")
    print(f"Money out       : £{total_out:,.2f}")
    print(f"Net movement    : £{net_total:,.2f}")

    expected_change = closing_balance - opening_balance

    if expected_change == net_total:
        print("Reconciliation  : PASS")
    else:
        print("Reconciliation  : FAIL")


def main():

    stats = AnalysisResults()

    args = parse_arguments()

    if not os.path.isfile(args.statement):
        print_error(
            f"statement file not found: {args.statement}",
            stats,
        )
        return 1

    if args.control_file:
        if not os.path.isfile(args.control_file):
            print_error(
                f"control file not found: "
                f"{args.control_file}",
                stats,
            )
            return 1

        if not os.access(args.control_file, os.R_OK):
            print_error(
                f"control file not readable: "
                f"{args.control_file}",
                stats,
            )
            return 1

    # Check that --display-category implies --analyse and --control-file
    if args.display_category:
        if not args.analyse:
            print_error("--display-category requires --analyse", stats)
            return 1
        if not args.control_file:
            print_error("--display-category requires --control-file", stats)
            return 1

    # Check that description-debug flags imply --analyse and --control-file
    if (args.display_description_contains or
        args.display_description_prefix or
        args.display_description_suffix):
        if not args.analyse:
            print_error("Description debug flags require --analyse", stats)
            return 1
        if not args.control_file:
            print_error("Description debug flags require --control-file", stats)
            return 1

    try:

        transactions = load_statement(
            args.statement,
            stats,
        )

        if not transactions:
            raise RuntimeError(
                "statement contains no transactions"
            )

        print_pass(
            f"{len(transactions)} transactions loaded",
            args.verbose,
            stats,
        )

        validate_transaction_types(
            transactions,
            args.verbose,
            stats,
        )

        verify_reverse_chronological_order(
            transactions,
            args.verbose,
            stats,
        )

        verify_tax_year(
            transactions,
            args.verbose,
            stats,
        )

        opening_balance, closing_balance = (
            verify_balances(
                transactions,
                args.verbose,
                stats,
            )
        )

        monthly, total_in, total_out = (
            calculate_monthly_totals(
                transactions
            )
        )

        if args.analyse:
            if not args.control_file:
                raise RuntimeError(
                    "--analyse requires "
                    "--control-file"
                )

            control = load_control_file(
                args.control_file
            )

            analysis = (
                analyse_transactions(
                    transactions,
                    control,
                )
            )

            print_analysis_report(
                analysis
            )

        if args.print_report:
            print_report(
                monthly,
                total_in,
                total_out,
                opening_balance,
                closing_balance,
            )

        if args.display_category:
            print_category_debug(analysis, args.display_category)

        if (args.display_description_contains or
            args.display_description_prefix or
            args.display_description_suffix):
            print_description_debug(
                analysis,
                args.display_description_contains,
                args.display_description_prefix,
                args.display_description_suffix,
            )
        
        print()
        print("============================================================")
        print("SUMMARY")
        print("============================================================")
        print()

        if args.verbose:
            print(
                f"PASS checks : "
                f"{stats.pass_count}"
            )

        print(
            f"Warnings    : "
            f"{stats.warning_count}"
        )

        print(
            f"Errors      : "
            f"{stats.error_count}"
        )

        return 0

    except Exception as exc:

        print_error(str(exc), stats)
        return 1


if __name__ == "__main__":
    sys.exit(main())
