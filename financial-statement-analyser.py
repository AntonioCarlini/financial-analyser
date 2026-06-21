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

ALLOWED_DAYS_GAP_AT_START = 9
ALLOWED_DAYS_GAP_AT_END = 5


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
        ## TODO required=True,
        help="CSV bank statement"
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

def verify_reverse_chronological_order(transactions, verbose,  stats):
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

def print_monthly_summary(monthly, total_in, total_out):
    """Print just the monthly summary table (no reconciliation)."""
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

def main():
    stats = AnalysisResults()
    args = parse_arguments()
    has_facet_errors = False

    # ------------------------------------------------------------------
    # 1. Mutual exclusion: --data-file and --statement
    # ------------------------------------------------------------------
    if args.data_file and args.statement:
        print_error("--data-file and --statement are mutually exclusive.", stats)
        return 1

    # ------------------------------------------------------------------
    # 2. DATA-FILE MODE
    # ------------------------------------------------------------------
    if args.data_file:
        if not os.path.isfile(args.data_file):
            print_error(f"data file not found: {args.data_file}", stats)
            return 1

        try:
            control_file_path, tax_years, extra_info_path = load_data_file(args.data_file)
            if not os.path.isfile(control_file_path):
                print_error(f"control file not found: {control_file_path}", stats)
                return 1

            # Optional: list the data file contents in verbose mode
            if args.verbose:
                list_data_file_info(tax_years, args.tax_year)

            # Filter tax years if --tax-year was given
            if args.tax_year:
                tax_years = [ty for ty in tax_years if ty['year'] in args.tax_year]
                if not tax_years:
                    print_error("No tax years match the filter.", stats)
                    return 1

            # Load control file once (shared across all years)
            control = load_control_file(control_file_path)

            # Validate --ownership-report owner exists
            if args.ownership_report and isinstance(args.ownership_report, str):
                if args.ownership_report not in control.people:
                    print_error(f"Owner '{args.ownership_report}' not found in control file", stats)
                    return 1

            # Process each tax year
            for ty in tax_years:
                print()
                print(f"Processing tax year: {ty['year']}")
                print("-" * 50)

                # 2a. Process each statement individually with its own rules
                cumulative_analysis = None

                for stmt in ty.get('statements', []):
                    stmt_type = stmt['type']
                    stmt_file = stmt['file']

                    try:
                        # Load the statement
                        transactions = load_statement_by_type(stmt_type, stmt_file, args.verbose, stats)
                        if not transactions:
                            print_warning(f"No transactions loaded from {stmt_file}", stats)
                            continue

                        # Get rules for this statement type
                        try:
                            rules = get_rules_for_type(stmt_type, control)
                        except ValueError as e:
                            print_error(str(e), stats)
                            continue

                        # Analyse this statement
                        analysis = analyse_transactions(transactions, control, rules)

                        # Merge into cumulative result
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

                # 2b. Load extra info (placeholder – will raise NotImplementedError)
                if extra_info_path:
                    try:
                        extra_info = load_extra_information(extra_info_path, ty['year'])
                        # TODO: merge extra_info into cumulative_analysis
                    except NotImplementedError as e:
                        print_warning(str(e), stats)

                # Use cumulative_analysis from here on
                analysis = cumulative_analysis

                # 2d. Validate compulsory facets (IHT_)
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

                # 2e. Print reports (mirroring the single‑statement flow)
                if args.facet_report:
                    if not hasattr(control, 'facet_definitions'):
                        print_error("Facet definitions not loaded in control file.", stats)
                    else:
                        print_facet_summary(analysis, args.facet_report, control.facet_definitions, control, args.ownership_report)

                if args.analyse:
                    print_analysis_report(analysis, control, args.ownership_report)

                # 2f. Debug outputs
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

                # 2g. Monthly summary (without ledger reconciliation)
                if args.print_report:
                    monthly, total_in, total_out = calculate_monthly_totals(all_transactions)
                    print_monthly_summary(monthly, total_in, total_out)

                # Optional: print a separator between years
                print()

            # After processing all years, print a final summary
            print()
            print("============================================================")
            print("SUMMARY")
            print("============================================================")
            print()
            if args.verbose:
                print(f"PASS checks : {stats.pass_count}")
            print(f"Warnings    : {stats.warning_count}")
            print(f"Errors      : {stats.error_count}")

            if has_facet_errors:
                return 1
            return 0

        except Exception as exc:
            print_error(f"Failed to process data file: {exc}", stats)
            return 1

    # ------------------------------------------------------------------
    # 3. SINGLE‑STATEMENT MODE
    # ------------------------------------------------------------------
    if not args.statement:
        print_error("Either --statement or --data-file must be provided", stats)
        return 1

    if not os.path.isfile(args.statement):
        print_error(f"statement file not found: {args.statement}", stats)
        return 1

    if args.control_file:
        if not os.path.isfile(args.control_file):
            print_error(f"control file not found: {args.control_file}", stats)
            return 1
        if not os.access(args.control_file, os.R_OK):
            print_error(f"control file not readable: {args.control_file}", stats)
            return 1

    # Check that debug flags imply --analyse and --control-file
    if args.display_category:
        if not args.analyse:
            print_error("--display-category requires --analyse", stats)
            return 1
        if not args.control_file:
            print_error("--display-category requires --control-file", stats)
            return 1

    if (args.display_description_contains or
        args.display_description_prefix or
        args.display_description_suffix):
        if not args.analyse:
            print_error("Description debug flags require --analyse", stats)
            return 1
        if not args.control_file:
            print_error("Description debug flags require --control-file", stats)
            return 1

    # Check that --ownership-report implies --analyse and --control-file
    if args.ownership_report:
        if not args.analyse:
            print_error("--ownership-report requires --analyse", stats)
            return 1
        if not args.control_file and not args.data_file:
            print_error("--ownership-report requires --control-file", stats)
            return 1

    try:
        # Load the single statement
        transactions = load_statement_by_type("bank-lloyds", args.statement, args.verbose, stats)

        if not transactions:
            raise RuntimeError("statement contains no transactions")

        print_pass(f"{len(transactions)} transactions loaded", args.verbose, stats)

        validate_transaction_types(transactions, args.verbose, stats)
        verify_reverse_chronological_order(transactions, args.verbose, stats)
        verify_tax_year(transactions, args.verbose, stats)
        opening_balance, closing_balance = verify_balances(transactions, args.verbose, stats)
        monthly, total_in, total_out = calculate_monthly_totals(transactions)

        if args.analyse:
            if not args.control_file:
                raise RuntimeError("--analyse requires --control-file")

            control = load_control_file(args.control_file)

            # Validate --ownership-report owner exists
            if args.ownership_report and isinstance(args.ownership_report, str):
                if args.ownership_report not in control.people:
                    print_error(f"Owner '{args.ownership_report}' not found in control file", stats)
                    return 1

            # Get rules for the statement type (assume "bank-lloyds" for single statement)
            try:
                rules = get_rules_for_type("bank-lloyds", control)
            except ValueError as e:
                print_error(str(e), stats)
                return 1

            analysis = analyse_transactions(transactions, control, rules)

            if args.facet_report:
                if not hasattr(control, 'facet_definitions'):
                    print_error("Facet definitions not loaded in control file.", stats)
                else:
                    print_facet_summary(analysis, args.facet_report, control.facet_definitions, control, args.ownership_report)

            print_analysis_report(analysis, control, args.ownership_report)

        if args.print_report:
            print_report(monthly, total_in, total_out, opening_balance, closing_balance)

        if args.analyse:
            # Validate compulsory facets (IHT_)
            required_prefixes = ["IHT_"]
            facet_errors = validate_compulsory_facets(analysis, required_prefixes)
            if facet_errors:
                print()
                print("============================================================")
                print("COMPULSORY FACET VALIDATION ERRORS")
                print("============================================================")
                for err in facet_errors:
                    print(f"ERROR: {err}")
                print()
                has_facet_errors = True

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

        print()
        print("============================================================")
        print("SUMMARY")
        print("============================================================")
        print()
        if args.verbose:
            print(f"PASS checks : {stats.pass_count}")
        print(f"Warnings    : {stats.warning_count}")
        print(f"Errors      : {stats.error_count}")

        if has_facet_errors:
            return 1
        return 0

    except Exception as exc:
        print_error(str(exc), stats)
        return 1

if __name__ == "__main__":
    sys.exit(main())
