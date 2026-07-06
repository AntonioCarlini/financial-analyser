#!/usr/bin/env python3
"""
find-specified-transactions.py

Find transactions exceeding (or falling within) a given monetary threshold
across one or more tax years, using the existing data.yaml and control.yaml.

Filters:
  --include-category   Only show transactions in these categories (OR logic)
  --include-facet      Only show transactions with these facets (OR logic)
  --exclude-category   Hide transactions in these categories (OR logic)
  --exclude-facet      Hide transactions with these facets (OR logic)

Precedence:
  1. If any --include-* flags are given, the universe starts empty and
     transactions are added if they match ANY included category OR facet.
  2. If no --include-* flags are given, the universe is everything.
  3. Exclusions are then applied as a final filter (regardless of includes).

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


def get_category_and_facets(tx, rules, control):
    """
    Determine the category and assigned facets for a transaction.
    This assumes all categories referenced by rules have already been validated.
    """
    matched_rule = None
    for rule in rules:
        if match_rule(tx, rule):
            matched_rule = rule
            break

    if matched_rule is None:
        category = control.default_category
    else:
        category = matched_rule.category

    # Resolve facets
    if matched_rule and matched_rule.facets is not None:
        facets = matched_rule.facets
    else:
        facets = control.categories[category].default_facets

    return category, facets


def validate_rules_for_type(stmt_type, rules, control):
    """
    Check that every category referenced by rules (and the default category)
    actually exists in control.categories.
    Returns a list of error strings (empty if valid).
    """
    errors = []
    valid_categories = set(control.categories.keys())

    # Check default category
    if control.default_category not in valid_categories:
        errors.append(
            f"Default category '{control.default_category}' is not defined in control.yaml"
        )

    # Check every rule's category
    for rule in rules:
        if rule.category not in valid_categories:
            errors.append(
                f"Rule '{rule.id}' (type: {stmt_type}) references undefined category '{rule.category}'"
            )

    return errors


def parse_comma_list(value):
    """Split a comma-separated string, strip whitespace, ignore empty items."""
    if not value:
        return set()
    return {item.strip() for item in value.split(',') if item.strip()}


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

    # Include/exclude flags
    parser.add_argument(
        "--include-category",
        help="Comma-separated list of category IDs to include (OR logic)."
    )
    parser.add_argument(
        "--include-facet",
        help="Comma-separated list of facet codes to include (OR logic)."
    )
    parser.add_argument(
        "--exclude-category",
        help="Comma-separated list of category IDs to exclude (OR logic)."
    )
    parser.add_argument(
        "--exclude-facet",
        help="Comma-separated list of facet codes to exclude (OR logic)."
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

    include_categories = parse_comma_list(args.include_category)
    include_facets = parse_comma_list(args.include_facet)
    exclude_categories = parse_comma_list(args.exclude_category)
    exclude_facets = parse_comma_list(args.exclude_facet)

    has_includes = bool(include_categories or include_facets)

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

    if args.tax_year:
        tax_years = [ty for ty in tax_years if ty['year'] in args.tax_year]

    if not tax_years:
        print_error("No tax years to process (check --tax-year filter)", stats)
        return 1

    if not os.path.isfile(control_path):
        print_error(f"Control file not found: {control_path}", stats)
        return 1

    try:
        control = load_control_file(control_path)
    except Exception as e:
        print_error(f"Failed to load control file: {e}", stats)
        return 1

    # ------------------------------------------------------------------
    # PRE-FLIGHT VALIDATION: collect all rule/category errors
    # ------------------------------------------------------------------
    all_validation_errors = []
    statement_types_seen = set()

    for ty in tax_years:
        for stmt in ty.get('statements', []):
            stmt_type = stmt['type']
            if stmt_type in statement_types_seen:
                continue
            statement_types_seen.add(stmt_type)

            try:
                rules = get_rules_for_type(stmt_type, control)
            except Exception as e:
                # This is a fatal error for this type
                all_validation_errors.append(f"Failed to load rules for '{stmt_type}': {e}")
                continue

            errors = validate_rules_for_type(stmt_type, rules, control)
            if errors:
                all_validation_errors.extend(errors)

    if all_validation_errors:
        print("ERROR: Category validation failed - one or more rules reference undefined categories.", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for err in all_validation_errors:
            print(f"  - {err}", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("Please fix the control.yaml and re-run.", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # PROCESS TRANSACTIONS (validation passed)
    # ------------------------------------------------------------------
    rows = []
    headers = ["TaxYear", "StatementFile", "Date", "Description", "Amount", "Balance", "Type", "Category", "Facets"]

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
                category, facets = get_category_and_facets(tx, rules, control)

                # Inclusion filter
                if has_includes:
                    in_cat = category in include_categories
                    in_facet = any(f in include_facets for f in facets)
                    if not (in_cat or in_facet):
                        continue

                # Exclusion filter
                if category in exclude_categories:
                    continue
                if any(f in exclude_facets for f in facets):
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

                facets_str = ",".join(facets) if facets else ""

                rows.append([
                    year_str,
                    os.path.basename(stmt_file),
                    tx.date.isoformat() if hasattr(tx.date, 'isoformat') else str(tx.date),
                    desc,
                    f"{signed_amount:.2f}",
                    f"{tx.balance:.2f}",
                    tx_type,
                    category,
                    facets_str
                ])

    # ---- Output ----
    if args.raw_tsv:
        print("\t".join(headers))
        for row in rows:
            print("\t".join(row))
    else:
        if not rows:
            print("No matching transactions found.")
            return 0

        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        if widths[3] > 60:
            widths[3] = 60
        if widths[8] > 30:
            widths[8] = 30

        fmt = "  ".join(f"{{:<{w}}}" for w in widths)

        print(fmt.format(*headers))
        print("-" * sum(widths) + "-" * (2 * (len(widths) - 1)))

        for row in rows:
            desc = row[3]
            if len(desc) > widths[3]:
                row[3] = desc[:widths[3] - 3] + "..."
            facets_str = row[8]
            if len(facets_str) > widths[8]:
                row[8] = facets_str[:widths[8] - 3] + "..."
            print(fmt.format(*row))

    if args.verbose:
        print(f"Total matching transactions: {len(rows)}", file=sys.stderr)

    if stats.error_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())