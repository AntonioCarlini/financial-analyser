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

Command-line options:

  --analyse
        Run the full analysis (categorisation, summarisation, reporting).
        Without this flag, most other flags have no effect.
        Default: False

  --control-file CONTROL_FILE
        Path to the YAML control file containing:
          - people definitions
          - category definitions with default facets
          - facet definitions (IHT, etc.)
          - classification rules
        Required when using --statement (not required when using --data-file,
        as the control file is specified inside the data file).

  --data-file DATA_FILE
        YAML data file containing tax years and statement files.
        Replaces --statement for multi-year, multi-account analysis.
        Mutually exclusive with --statement.
        If provided, --control-file is read from the data file.

  --statement STATEMENT
        Single CSV bank statement file (Lloyds format only).
        Mutually exclusive with --data-file.
        Requires --control-file and --analyse.

  --tax-year TAX_YEAR
        Filter which tax years to process when using --data-file.
        Repeatable (e.g., --tax-year 2023-2024 --tax-year 2024-2025).
        If not specified, all tax years in the data file are processed.
        Has no effect with --statement.

  --display-category CATEGORY
        Debug: show every transaction assigned to the specified category.
        Repeatable (OR logic: show transactions in any specified category).
        Requires --analyse and --control-file.

  --display-description-contains TEXT
        Debug: show transactions whose description contains TEXT.
        Repeatable (OR logic).
        Requires --analyse and --control-file.

  --display-description-prefix TEXT
        Debug: show transactions whose description starts with TEXT.
        Repeatable (OR logic).
        Requires --analyse and --control-file.

  --display-description-suffix TEXT
        Debug: show transactions whose description ends with TEXT.
        Repeatable (OR logic).
        Requires --analyse and --control-file.

  --display-facet FACET
        Debug: show transactions assigned to the specified facet code.
        Repeatable (OR logic).
        Requires --analyse and --control-file.

  --facet-report FACET_GROUP
        Generate a summary report grouped by facets in the specified group
        (e.g., --facet-report IHT). This produces a table suitable for
        filling in IHT403 or similar forms.
        Requires --analyse and --control-file.

  --ownership-report [OWNER]
        Enable ownership reporting. When specified, category and facet
        summaries show breakdowns by owner.
        With no value (--ownership-report), show all owners defined in
        the control file.
        With a value (--ownership-report ARC), show only that owner.
        Requires --analyse and --control-file.
        Default: False (no ownership reporting).

  --print-report
        Print the monthly summary (month-by-month money in/out/net).
        In single-statement mode, also prints ledger reconciliation.
        In data-file mode, prints only the monthly summary (no reconciliation).
        Does not require --analyse.

  --relax-facet-checks / --no-relax-facet-checks
        Control facet validation behaviour.
        --relax-facet-checks (default): collect all validation errors
        before reporting, then exit with failure if any found.
        --no-relax-facet-checks: exit immediately on first error.
        Default: True (--relax-facet-checks)

  --verbose / --no-verbose
        Enable or disable verbose output (PASS messages, etc.).
        Default: False (--no-verbose)

  --help (-h)
        Show this help message and exit.

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

from financial_statement_analyser.core.types import (
    AnalysisResult,
    AnalysisResults,
    Category,
    CategorySummary,
    ControlFile,
    MatchCondition,
    Person,
    Rule,
    Transaction,
)

from financial_statement_analyser.core.utils import (
    print_pass,
    print_warning,
    print_error,
    print_accumulated_messages,
    parse_date,
    parse_tax_year,
    parse_decimal,
)

from financial_statement_analyser.core.analysis import (
    match_rule,
    resolve_ownership,
    analyse_transactions,
    merge_analysis_results,
)

from financial_statement_analyser.core.reports import (
    print_analysis_report,
    print_category_debug,
    print_description_debug,
    print_facet_debug,
    print_facet_summary,
    validate_compulsory_facets,
)

from financial_statement_analyser.core.control import (
    load_control_file,
    load_rules_file,
    get_rules_for_type,
)

from financial_statement_analyser.loaders import load_statement_by_type
from financial_statement_analyser.loaders.lloyds import validate_transaction_types
from financial_statement_analyser.core.data import load_data_file, list_data_file_info
from financial_statement_analyser.core.reports import print_report, print_monthly_summary

from financial_statement_analyser.core.verification import (
    verify_reverse_chronological_order,
    verify_tax_year,
    verify_balances,
    calculate_monthly_totals,
)

# ------------------------------------------------------------
# Condition checkers for "when" clauses
# ------------------------------------------------------------

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
        "--data-file",
        help="YAML data file containing tax years and statements (replaces --statement)"
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
        "--display-facet",
        action="append",
        default=[],
        help="Show all transactions assigned to this facet code (repeatable, OR logic)",
    )

    parser.add_argument(
        "--facet-report",
        help="Generate a summary report for a facet group",
    )

    parser.add_argument(
        "--print-report",
        default=False,
        help="Print report data"
    )

    parser.add_argument(
	    "--relax-facet-checks",
	    action=argparse.BooleanOptionalAction,
	    default=True,
	    help="Collect all facet validation errors (don't stop early). Default: True",
	)

    parser.add_argument(
        "--statement",
        help="CSV bank statement"
    )

    parser.add_argument(
        "--statement-type",
        default="bank-lloyds",
        help="Statement type for --statement (default: bank-lloyds)"
    )
    
    parser.add_argument(
        "--tax-year",
        action="append",
        default=[],
        help="Filter which tax years to process (repeatable, e.g., --tax-year 2024-2025)"
    )

    parser.add_argument(
        "--ownership-report",
        nargs='?',
        const=True,
        default=False,
        help="Report ownership splits. With no value, show all owners. With a value, show only that owner (e.g., --ownership-report ARC)"
    )

    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable verbose output"
    )

    return parser.parse_args()


def load_extra_information(filename, tax_year):
    """
    Load extra information (interest, dividends, etc.) for a specific tax year.
    """
    raise NotImplementedError(
        f"Fatal error: no support for loading extra information from '{filename}' "
        f"for tax year '{tax_year}'"
    )

def main():
    stats = AnalysisResults()
    args = parse_arguments()
    has_facet_errors = False

    # ------------------------------------------------------------------
    # 1. Determine control file path and load data file (if any)
    # ------------------------------------------------------------------
    control_file_path = None
    extra_info_path = None
    data_tax_years = []
    data_statement_paths = set()

    if args.data_file:
        if not os.path.isfile(args.data_file):
            print_error(f"data file not found: {args.data_file}", stats)
            return 1

        try:
            control_file_path, data_tax_years, extra_info_path = load_data_file(args.data_file)
        except Exception as e:
            print_error(f"Failed to load data file: {e}", stats)
            return 1

        # Build set of absolute statement paths from data file
        for ty in data_tax_years:
            for stmt in ty.get('statements', []):
                data_statement_paths.add(os.path.abspath(stmt['file']))

    # ------------------------------------------------------------------
    # 2. Determine control file (CLI overrides data file)
    # ------------------------------------------------------------------
    if args.control_file:
        control_file_path = args.control_file

    if not control_file_path:
        print_error("No control file specified (use --control-file or provide one in data file)", stats)
        return 1

    if not os.path.isfile(control_file_path):
        print_error(f"control file not found: {control_file_path}", stats)
        return 1
    if not os.access(control_file_path, os.R_OK):
        print_error(f"control file not readable: {control_file_path}", stats)
        return 1

    # ------------------------------------------------------------------
    # 3. Load control file (once)
    # ------------------------------------------------------------------
    control = load_control_file(control_file_path)

    # Validate --ownership-report owner exists
    if args.ownership_report and isinstance(args.ownership_report, str):
        if args.ownership_report not in control.people:
            print_error(f"Owner '{args.ownership_report}' not found in control file", stats)
            return 1

    # ------------------------------------------------------------------
    # 4. Build combined tax years list
    # ------------------------------------------------------------------
    tax_years = list(data_tax_years)  # copy

    if args.statement:
        stmt_file = os.path.abspath(args.statement)
        stmt_type = args.statement_type

        # Check if this statement is already in the data file
        if stmt_file in data_statement_paths:
            # It will be processed as part of the data file; do nothing
            print(f"Note: --statement file is already in data file; will be processed normally.")
        else:
            # Load the statement to determine its tax year
            try:
                # Load the statement
                temp_transactions = load_statement_by_type(stmt_type, stmt_file, args.verbose, stats, control)
                if not temp_transactions:
                    print_error(f"Statement file contains no transactions: {stmt_file}", stats)
                    return 1

                # Get tax year
                year_str = verify_tax_year(temp_transactions, args.verbose, stats)
                if not year_str:
                    print_error(f"Could not determine tax year for {stmt_file}", stats)
                    return 1

                # Add as a forced tax year (not subject to --tax-year filter)
                # Check if this year already exists in tax_years
                existing_year = None
                for ty in tax_years:
                    if ty['year'] == year_str:
                        existing_year = ty
                        break

                if existing_year:
                    # Add this statement to the existing year's statements
                    existing_year['statements'].append({
                        'type': stmt_type,
                        'file': stmt_file,
                    })
                else:
                    # Create a new tax year with forced=True
                    tax_years.append({
                        'year': year_str,
                        'statements': [{'type': stmt_type, 'file': stmt_file}],
                        'forced': True,   # marker to bypass --tax-year filter
                    })

            except NotImplementedError as e:
                print_error(str(e), stats)
                return 1
            except Exception as e:
                print_error(f"Failed to process --statement: {e}", stats)
                return 1

    # ------------------------------------------------------------------
    # 5. Apply --tax-year filter
    # ------------------------------------------------------------------
    if args.tax_year:
        filtered = []
        for ty in tax_years:
            if ty['year'] in args.tax_year or ty.get('forced', False):
                filtered.append(ty)
        tax_years = filtered
    else:
        # Remove any 'forced' marker (not needed after filtering)
        for ty in tax_years:
            ty.pop('forced', None)

    if not tax_years:
        print_error("No tax years to process (check --tax-year filter)", stats)
        return 1

    # ------------------------------------------------------------------
    # 6. Process all tax years
    # ------------------------------------------------------------------
    all_transactions_by_year = {}  # For --print-report

    for ty in tax_years:
        print()
        print(f"Processing tax year: {ty['year']}")
        print("-" * 50)

        cumulative_analysis = None
        year_transactions = []

        for stmt in ty.get('statements', []):
            stmt_type = stmt['type']
            stmt_file = stmt['file']

            try:
                transactions = load_statement_by_type(stmt_type, stmt_file, args.verbose, stats, control)
                if not transactions:
                    print_warning(f"No transactions loaded from {stmt_file}", stats)
                    continue
                year_transactions.extend(transactions)

                # Get rules for this statement type
                try:
                    rules = get_rules_for_type(stmt_type, control)
                except ValueError as e:
                    print_error(str(e), stats)
                    continue

                analysis = analyse_transactions(transactions, control, rules)
                cumulative_analysis = merge_analysis_results(cumulative_analysis, analysis)

            except NotImplementedError as e:
                print_error(str(e), stats)
                continue
            except Exception as e:
                print_error(f"Error processing {stmt_file}: {e}", stats)
                continue

        if cumulative_analysis is None:
            print_warning(f"No transactions processed for {ty['year']}. Skipping.", stats)
            continue

        # Load extra info (only if data file was provided and we have extra_info_path)
        if extra_info_path:
            try:
                extra_info = load_extra_information(extra_info_path, ty['year'])
                # TODO: merge extra_info into cumulative_analysis
            except NotImplementedError as e:
                print_error(str(e), stats)

        analysis = cumulative_analysis
        all_transactions_by_year[ty['year']] = year_transactions

        # Validate compulsory facets
        required_prefixes = ["IHT_"]
        facet_errors = validate_compulsory_facets(analysis, required_prefixes)
        if facet_errors:
            print()
            print("============================================================")
            print(f"COMPULSORY FACET VALIDATION ERRORS – {ty['year']}")
            print("============================================================")
            for err in facet_errors:
                print(f"ERROR: {err}")
            print()
            has_facet_errors = True

        # Print reports
        if args.facet_report:
            if not hasattr(control, 'facet_definitions'):
                print_error("Facet definitions not loaded in control file.", stats)
            else:
                print_facet_summary(analysis, args.facet_report, control.facet_definitions, control, args.ownership_report)

        if args.analyse:
            print_analysis_report(analysis, control, args.ownership_report)

        # Debug outputs
        if args.display_facet:
            print_facet_debug(analysis, args.display_facet)

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

        # Monthly summary (without reconciliation)
        if args.print_report and year_transactions:
            monthly, total_in, total_out = calculate_monthly_totals(year_transactions)
            print_monthly_summary(monthly, total_in, total_out)

        print()  # separator

    # ------------------------------------------------------------------
    # 7. Final summary
    # ------------------------------------------------------------------
    print()
    print("============================================================")
    print("SUMMARY")
    print("============================================================")
    print()
    if args.verbose:
        print(f"PASS checks : {stats.pass_count}")
    print(f"Warnings    : {stats.warning_count}")
    print(f"Errors      : {stats.error_count}")

    if stats.error_count > 0 or stats.warning_count > 0:
        print_accumulated_messages()

    if has_facet_errors or stats.error_count > 0:
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
