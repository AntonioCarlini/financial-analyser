#!/usr/bin/env python3
"""
find-specified-transactions.py

Find transactions exceeding (or falling within) a given monetary threshold
across one or more tax years, using the existing data.yaml and control.yaml.

Reuses:
  - financial_statement_analyser.core.data.load_data_file
  - financial_statement_analyser.core.control.load_control_file
  - financial_statement_analyser.core.control.get_rules_for_type
  - financial_statement_analyser.core.analysis.match_rule
  - financial_statement_analyser.loaders.load_statement_by_type

Output is tab-separated to stdout.
"""

import argparse
import os
import sys
from decimal import Decimal

from financial_statement_analyser.core.data import load_data_file
from financial_statement_analyser.core.control import load_control_file, get_rules_for_type
from financial_statement_analyser.core.analysis import match_rule
from financial_statement_analyser.core.types import AnalysisResults
from financial_statement_analyser.core.utils import parse_decimal, print_error
from financial_statement_analyser.loaders import load_statement_by_type


def get_signed_amount(tx):
    """Return a signed Decimal: positive = credit (money in), negative = debit (money out)."""
    return tx.credit - tx.debit


def get_category_for_transaction(tx, rules, control):
    """
    Determine the category for a transaction, using the same logic as
    analyse_transactions(). Returns the category ID (string).
    """
    matched_rule = None
    for rule in rules:
        if match_rule(tx, rule):
            matched_rule = rule
            break

    if matched_rule is None:
        return control.default_category
    else:
        return matched_rule.category


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Find large transactions across bank/credit card statements."
    )
    parser.add_argument(
        "--data-file",
        required=True,
        help="Path to the data.yaml file (defines tax years and statement files)."
    )
    parser.add_argument(
        "--tax-year",
        action="append",
        default=[],
        help="Filter to specific tax year(s), e.g. --tax-year 2024-2025. Repeatable."
    )
    parser.add_argument(
        "--min-amount",
        required=True,
        type=str,
        help="Minimum absolute amount (e.g., 500 or 1000.00)."
    )
    parser.add_argument(
        "--max-amount",
        type=str,
        help="Maximum absolute amount (e.g., 10000). Optional."
    )
    parser.add_argument(
        "--type",
        choices=["credit", "debit"],
        help="Filter by transaction type. 'credit' means money in, 'debit' means money out."
    )
    parser.add_argument(
        "--exclude",
        help="Comma-separated list of category IDs to exclude (e.g., INCOME_SALARY,CAPITAL_EXPENSES,CREDIT_CARD). "
             "Spaces around commas are allowed if the whole list is quoted."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show progress messages (which files are being read)."
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    # Parse the exclude list (strip whitespace, handle empty)
    excluded_categories = set()
    if args.exclude:
        for cat in args.exclude.split(','):
            cat_stripped = cat.strip()
            if cat_stripped:
                excluded_categories.add(cat_stripped)

    stats = AnalysisResults()

    try:
        min_dec = parse_decimal(args.min_amount)
    except Exception as e:
        print_error(f"Invalid --min-amount '{args.min_amount}': {e}", stats)
        return 1

    max_dec = None
    if args.max_amount:
        try:
            max_dec = parse_decimal(args.max_amount)
            if max_dec < min_dec:
                print_error("--max-amount cannot be less than --min-amount", stats)
                return 1
        except Exception as e:
            print_error(f"Invalid --max-amount '{args.max_amount}': {e}", stats)
            return 1

    # 1. Load the data file
    if not os.path.isfile(args.data_file):
        print_error(f"Data file not found: {args.data_file}", stats)
        return 1

    try:
        control_path, tax_years, extra_info_path = load_data_file(args.data_file)
    except Exception as e:
        print_error(f"Failed to load data file: {e}", stats)
        return 1

    # 2. Filter tax years if requested
    if args.tax_year:
        tax_years = [ty for ty in tax_years if ty['year'] in args.tax_year]

    if not tax_years:
        print_error("No tax years to process (check --tax-year filter)", stats)
        return 1

    # 3. Load the control file
    if not os.path.isfile(control_path):
        print_error(f"Control file not found: {control_path}", stats)
        return 1

    try:
        control = load_control_file(control_path)
    except Exception as e:
        print_error(f"Failed to load control file: {e}", stats)
        return 1

    # 4. Print header (tab-separated)
    print("\t".join([
        "TaxYear",
        "StatementFile",
        "Date",
        "Description",
        "Amount",
        "Balance",
        "Type",
        "Category"
    ]))

    total_matches = 0

    for ty in tax_years:
        year_str = ty['year']
        for stmt in ty.get('statements', []):
            stmt_type = stmt['type']
            stmt_file = stmt['file']

            if args.verbose:
                print(f"Reading {stmt_file} ({stmt_type})...", file=sys.stderr)

            # Load the rules for this statement type
            try:
                rules = get_rules_for_type(stmt_type, control)
            except Exception as e:
                print_error(f"Failed to get rules for {stmt_type}: {e}", stats)
                continue

            try:
                transactions = load_statement_by_type(
                    stmt_type,
                    stmt_file,
                    args.verbose,
                    stats,
                    control
                )
            except Exception as e:
                print_error(f"Error loading {stmt_file}: {e}", stats)
                continue

            for tx in transactions:
                # Determine the category for this transaction
                category = get_category_for_transaction(tx, rules, control)

                # Apply exclude filter
                if category in excluded_categories:
                    continue

                signed_amount = get_signed_amount(tx)
                abs_amount = abs(signed_amount)

                # Apply amount filters
                if abs_amount < min_dec:
                    continue
                if max_dec is not None and abs_amount > max_dec:
                    continue

                # Apply credit/debit filter
                if args.type == "credit" and signed_amount <= 0:
                    continue
                if args.type == "debit" and signed_amount >= 0:
                    continue

                tx_type = "Credit" if signed_amount > 0 else "Debit" if signed_amount < 0 else "Zero"

                # Clean description
                desc = tx.description.replace("\t", " ").replace("\n", " ")

                # Print matching transaction
                print("\t".join([
                    year_str,
                    os.path.basename(stmt_file),
                    tx.date.isoformat() if hasattr(tx.date, 'isoformat') else str(tx.date),
                    desc,
                    f"{signed_amount:.2f}",
                    f"{tx.balance:.2f}",
                    tx_type,
                    category
                ]))

                total_matches += 1

    if args.verbose:
        print(f"Total matching transactions found (after exclusions): {total_matches}", file=sys.stderr)

    if stats.error_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
