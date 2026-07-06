#!/usr/bin/env python3
"""
find-specified-transactions.py

Find transactions exceeding (or falling within) a given monetary threshold
across one or more tax years, using the existing data.yaml and control.yaml.

Output is human-readable aligned columns by default. Use --raw-tsv for piping.
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
    """Return signed Decimal: positive = credit (money in), negative = debit (money out)."""
    return tx.credit - tx.debit


def get_category_for_transaction(tx, rules, control):
    """Determine category using the same logic as analyse_transactions()."""
    matched_rule = None
    for rule in rules:
        if match_rule(tx, rule):
            matched_rule = rule
            break
    if matched_rule is None:
        return control.default_category
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
        help="Comma-separated list of category IDs to exclude."
    )
    parser.add_argument(
        "--raw-tsv",
        action="store_true",
        help="Output raw tab-separated values (for piping to files/Excel)."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show progress messages."
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

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

    # Load data file
    if not os.path.isfile(args.data_file):
        print_error(f"Data file not found: {args.data_file}", stats)
        return 1

    try:
        control_path, tax_years, extra_info_path = load_data_file(args.data_file)
    except Exception as e:
        print_error(f"Failed to load data file: {e}", stats)
        return 1

    # Filter tax years
    if args.tax_year:
        tax_years = [ty for ty in tax_years if ty['year'] in args.tax_year]

    if not tax_years:
        print_error("No tax years to process (check --tax-year filter)", stats)
        return 1

    # Load control file
    if not os.path.isfile(control_path):
        print_error(f"Control file not found: {control_path}", stats)
        return 1

    try:
        control = load_control_file(control_path)
    except Exception as e:
        print_error(f"Failed to load control file: {e}", stats)
        return 1

    # Collect rows here for pretty formatting
    rows = []
    headers = ["TaxYear", "StatementFile", "Date", "Description", "Amount", "Balance", "Type", "Category"]

    for ty in tax_years:
        year_str = ty['year']
        for stmt in ty.get('statements', []):
            stmt_type = stmt['type']
            stmt_file = stmt['file']

            if args.verbose:
                print(f"Reading {stmt_file} ({stmt_type})...", file=sys.stderr)

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
                category = get_category_for_transaction(tx, rules, control)

                if category in excluded_categories:
                    continue

                signed_amount = get_signed_amount(tx)
                abs_amount = abs(signed_amount)

                if abs_amount < min_dec:
                    continue
                if max_dec is not None and abs_amount > max_dec:
                    continue

                if args.type == "credit" and signed_amount <= 0:
                    continue
                if args.type == "debit" and signed_amount >= 0:
                    continue

                tx_type = "Credit" if signed_amount > 0 else "Debit" if signed_amount < 0 else "Zero"
                desc = tx.description.replace("\t", " ").replace("\n", " ")

                rows.append([
                    year_str,
                    os.path.basename(stmt_file),
                    tx.date.isoformat() if hasattr(tx.date, 'isoformat') else str(tx.date),
                    desc,
                    f"{signed_amount:.2f}",
                    f"{tx.balance:.2f}",
                    tx_type,
                    category
                ])

    # ---- Output ----
    if args.raw_tsv:
        # Raw tab-separated (for piping)
        print("\t".join(headers))
        for row in rows:
            print("\t".join(row))
    else:
        # Human-readable aligned columns
        if not rows:
            print("No matching transactions found.")
            return 0

        # Calculate column widths
        col_count = len(headers)
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        # Cap description at 60 characters to avoid crazy wrapping
        if widths[3] > 60:
            widths[3] = 60

        # Build format strings
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)

        # Print header
        print(fmt.format(*headers))
        print("-" * sum(widths) + "-" * (2 * (len(widths) - 1)))

        # Print rows (truncate description if needed)
        for row in rows:
            desc = row[3]
            if len(desc) > widths[3]:
                row[3] = desc[:widths[3] - 3] + "..."
            print(fmt.format(*row))

    if args.verbose:
        print(f"Total matching transactions: {len(rows)}", file=sys.stderr)

    if stats.error_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())