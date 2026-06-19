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


# Must be frozen as it is now used as a key in the facet_assignments dictionary
@dataclass(frozen=True)
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
    default_facets: list[str] = field(default_factory=list)

@dataclass
class MatchCondition:
    type: str   # 'description', 'prefix', (later 'regex', etc.)
    value: str

@dataclass
class Rule:
    id: str
    priority: int
    conditions: list[MatchCondition]
    category: str
    ownership: dict[str, int]
    transaction_types: set[str] | None
    direction: str | None
    when: list[dict] | None = None
    facets: list[str] | None = None

@dataclass
class ControlFile:
    people: dict[str, Person]

    categories: dict[str, Category]

    rules: list[Rule]

    default_category: str

    default_ownership: dict[str, int]
    facet_definitions: dict = field(default_factory=dict)

@dataclass
class CategorySummary:
    category: str

    transaction_count: int = 0

    total_credit: Decimal = Decimal("0")
    total_debit: Decimal = Decimal("0")


@dataclass
class AnalysisResult:
    summaries: dict[str, CategorySummary]
    uncategorised: list[Transaction]
    warnings: list[str]
    category_transactions: dict[str, list[tuple[Transaction, str | None]]] = field(default_factory=dict)
    facet_assignments: dict[Transaction, list[str]] = field(default_factory=dict)

# ------------------------------------------------------------
# Condition checkers for "when" clauses
# ------------------------------------------------------------

CONDITION_CHECKERS = {}

def register_checker(cond_type):
    """Decorator to register a condition checker function."""
    def decorator(func):
        CONDITION_CHECKERS[cond_type] = func
        return func
    return decorator

def parse_date(date_str):
    """Parse a date string in DD-MM-YYYY or YYYY-MM-DD format."""
    date_str = date_str.strip()
    # Try YYYY-MM-DD first
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        pass
    # Try DD-MM-YYYY
    try:
        return datetime.strptime(date_str, "%d-%m-%Y")
    except ValueError:
        raise ValueError(f"Unrecognised date format: {date_str}")

def parse_tax_year(tax_year_str):
    """Return (start_date, end_date) for a UK tax year like '2023-2024'."""
    parts = tax_year_str.split('-')
    if len(parts) != 2:
        raise ValueError(f"Invalid tax year format: {tax_year_str}")
    start_year = int(parts[0])
    start = datetime(start_year, 4, 6)
    end = datetime(start_year + 1, 4, 5)
    return start, end

# Condition checkers
@register_checker("amount_range")
def check_amount_range(tx, value):
    """value is [min, max] inclusive."""
    min_val, max_val = value
    amount = tx.credit if tx.credit else tx.debit
    return min_val <= amount <= max_val

@register_checker("amount_exact")
def check_amount_exact(tx, value):
    """value is a single number."""
    amount = tx.credit if tx.credit else tx.debit
    return amount == value

@register_checker("line_numbers")
def check_line_numbers(tx, value):
    """value is a list of line numbers."""
    return tx.line_number in value

@register_checker("tax_year")
def check_tax_year(tx, value):
    """value is a string like '2023-2024'."""
    start, end = parse_tax_year(value)
    return start <= tx.date <= end

@register_checker("date_range")
def check_date_range(tx, value):
    """value is [start_date, end_date] as strings."""
    start_str, end_str = value
    start = parse_date(start_str)
    end = parse_date(end_str)
    return start <= tx.date <= end



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
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable verbose output"
    )

    return parser.parse_args()


def parse_decimal(value):
    value = value.strip()

    if value == "":
        return Decimal("0")

    value = value.replace(",", "")

    return Decimal(value)

def load_data_file(filename):
    """
    Load the YAML data file and resolve relative paths.
    Returns a tuple: (control_file_path, list_of_tax_years)
    where each tax_year is a dict: {'year': '...', 'statements': [...]}
    """
    base_dir = os.path.dirname(os.path.abspath(filename))

    with open(filename, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    control_file = raw.get('control_file')
    if control_file:
        # Resolve relative to data file's directory
        control_file = os.path.join(base_dir, control_file)
    else:
        raise ValueError("data file must contain 'control_file' key")

    extra_info_file = raw.get('extra_info_file')
    if extra_info_file:
        extra_info_file = os.path.join(base_dir, extra_info_file)
    
    tax_years = raw.get('tax_years', [])
    if not tax_years:
        raise ValueError("data file must contain at least one tax_year")

    # Resolve statement file paths
    for ty in tax_years:
        for stmt in ty.get('statements', []):
            stmt['file'] = os.path.join(base_dir, stmt['file'])

    return control_file, tax_years, extra_info_file

def load_extra_information(filename, tax_year):
    """
    Load extra information (interest, dividends, etc.) for a specific tax year.
    """
    raise NotImplementedError(
        f"Fatal error: no support for loading extra information from '{filename}' "
        f"for tax year '{tax_year}'"
    )

def list_data_file_info(tax_years, filter_years=None):
    """
    Print the tax years and statements that would be processed.
    If filter_years is a list, only show those years.
    """
    if filter_years:
        filtered = [ty for ty in tax_years if ty['year'] in filter_years]
    else:
        filtered = tax_years

    if not filtered:
        print("No tax years match the filter.")
        return

    print()
    print("============================================================")
    print("DATA FILE SUMMARY")
    print("============================================================")
    print()

    for ty in filtered:
        print(f"Tax Year: {ty['year']}")
        if 'description' in ty:
            print(f"  Description: {ty['description']}")
        print(f"  Statements:")
        for stmt in ty.get('statements', []):
            print(f"    - Type: {stmt['type']:15} File: {stmt['file']}")
        print()

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
		    default_facets=category_data.get("default_facets", []),
        )

    # Load facet definitions with full metadata
    facet_definitions = {}
    for group_name, group_data in raw.get("facets", {}).items():
        codes_dict = {}
        for item in group_data.get("codes", []):
            codes_dict[item["code"]] = {
                "description": item.get("description", ""),
                "suppress_in_report": item.get("suppress_in_report", False),
            }
        facet_definitions[group_name] = {
            "description": group_data.get("description", ""),
            "codes": codes_dict,
        }

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
                when=rule_data.get("when"),
                facets=rule_data.get("classify", {}).get("facets"),
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
        default_category=raw["defaults"]["category"],
        default_ownership=raw["defaults"]["ownership"],
        facet_definitions=facet_definitions,
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

    # Finally, check the "when" clause (if present)
    if rule.when is not None:
        # rule.when is a list of groups; OR across groups, AND within each group
        for group in rule.when:
            group_passes = True
            for cond_type, cond_value in group.items():
                checker = CONDITION_CHECKERS.get(cond_type)
                if checker is None:
                    # Unknown condition type – treat as failure to be safe
                    group_passes = False
                    break
                if not checker(tx, cond_value):
                    group_passes = False
                    break
            if group_passes:
                # This group matched – rule passes
                return True
        # No group matched – rule fails
        return False

    # No 'when' clause, or it matched
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

    facet_assignments = {}

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

		# Resolve facets
        if matched_rule and matched_rule.facets is not None:
            assigned_facets = matched_rule.facets
        else:
            assigned_facets = control.categories[category_id].default_facets

        facet_assignments[tx] = assigned_facets

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
        facet_assignments=facet_assignments,
    )

def print_analysis_report(result):

    print()
    print("============================================================")
    print("CATEGORY SUMMARY")
    print("============================================================")
    print()

    print(
        f"{'Category':30}"
        f"{'Count':>8}"
        f"{'In':>15}"
        f"{'Out':>15}"
    )

    print("-" * 60)

    for category_id in sorted(result.summaries):

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

def print_facet_debug(result, facets_to_display):
    """Print all transactions that belong to any of the given facets."""
    if not facets_to_display:
        return

    print()
    print("============================================================")
    print("DEBUG: TRANSACTIONS BY FACET")
    print("============================================================")
    print()

    # Build a master list of (tx, category, rule_id, facets)
    all_entries = []

    # 1. Categorised transactions
    for cat_id, entries in result.category_transactions.items():
        for tx, rule_id in entries:
            assigned_facets = result.facet_assignments.get(tx, [])
            all_entries.append((tx, cat_id, rule_id, assigned_facets))

    # 2. Uncategorised transactions
    for tx in result.uncategorised:
        assigned_facets = result.facet_assignments.get(tx, [])
        all_entries.append((tx, "UNCATEGORISED", None, assigned_facets))

    # Filter by facets
    matches = []
    for tx, category, rule_id, assigned_facets in all_entries:
        if any(f in assigned_facets for f in facets_to_display):
            matches.append((tx, category, rule_id, assigned_facets))

    if not matches:
        print("No transactions matched the requested facets.")
        return

    # Header
    print(f"{'Line':>5}  {'Date':<12}  {'Type':<4}  {'Amount':>10}  {'Category':<22}  {'Rule':<20}  {'Facets':<30}  Description")
    print("-" * 140)

    # Sort by line number
    matches.sort(key=lambda x: x[0].line_number)

    for tx, category, rule_id, assigned_facets in matches:
        amount = tx.credit if tx.credit else tx.debit
        rule_display = rule_id if rule_id else "N/A"
        cat_display = category[:22]
        facets_display = ", ".join(assigned_facets) if assigned_facets else "NONE"
        print(
            f"{tx.line_number:>5}  "
            f"{tx.date.strftime('%Y-%m-%d'):<12}  "
            f"{tx.transaction_type:<4}  "
            f"{amount:>10,.2f}  "
            f"{cat_display:<22}  "
            f"{rule_display:<20}  "
            f"{facets_display:<30}  "
            f"{tx.description}"
        )

    print()
    print(f"Total displayed: {len(matches)} transactions")

def validate_compulsory_facets(result, required_prefixes):
    """Check that every transaction has at least one facet from each required prefix."""
    errors = []
    for tx, assigned_facets in result.facet_assignments.items():
        for prefix in required_prefixes:
            if not any(f.startswith(prefix) for f in assigned_facets):
                errors.append(
                    f"Line {tx.line_number}: {tx.date.strftime('%Y-%m-%d')} "
                    f"{tx.transaction_type} {tx.description} "
                    f"has no {prefix} facet. Current: {assigned_facets or 'NONE'}"
                )
                break  # Only report once per transaction (first missing prefix)
    return errors

def print_facet_summary(result, facet_group_name, facet_definitions):
    """
    Print a summary table grouped by facet codes in the specified group.
    facet_definitions: dict from the YAML (e.g., facets: {IHT: {codes: {...}}})
    """
    if not facet_group_name:
        return

    if facet_group_name not in facet_definitions:
        print(f"ERROR: Facet group '{facet_group_name}' not found in control file.")
        return

    group = facet_definitions[facet_group_name]
    codes_metadata = group["codes"]  # dict: code -> {description, suppress_in_report}

    # Build a dictionary: facet_code -> totals
    facet_totals = {}
    for code, meta in codes_metadata.items():
        facet_totals[code] = {
            "count": 0,
            "total_credit": Decimal("0"),
            "total_debit": Decimal("0"),
        }

    # Process all transactions
    for tx, assigned_facets in result.facet_assignments.items():
        for facet in assigned_facets:
            if facet in facet_totals:
                facet_totals[facet]["count"] += 1
                facet_totals[facet]["total_credit"] += tx.credit
                facet_totals[facet]["total_debit"] += tx.debit

    # Print the summary
    print()
    print("============================================================")
    print(f"FACET SUMMARY: {facet_group_name}")
    print(f"Description: {group['description']}")
    print("============================================================")
    print()

    print(f"{'Facet':<30} {'Description':<55} {'Count':>8} {'In':>15} {'Out':>15}")
    print("-" * 110)

    total_count = 0
    total_in = Decimal("0")
    total_out = Decimal("0")

    for facet_code in sorted(facet_totals):
        meta = codes_metadata.get(facet_code, {})
        # Skip facets marked for suppression
        if meta.get("suppress_in_report", False):
            continue

        totals = facet_totals[facet_code]
        # Optionally skip zero transactions (optional but keeps output clean)
        if totals["count"] == 0 and totals["total_credit"] == 0 and totals["total_debit"] == 0:
            continue

        desc = meta.get("description", "")
        print(
            f"{facet_code:<30} "
            f"{desc[:55]:<55} "
            f"{totals['count']:>8} "
            f"{totals['total_credit']:>15,.2f} "
            f"{totals['total_debit']:>15,.2f}"
        )

        total_count += totals["count"]
        total_in += totals["total_credit"]
        total_out += totals["total_debit"]

    print("-" * 110)
    print(
        f"{'TOTAL':<30} "
        f"{'':<55} "
        f"{total_count:>8} "
        f"{total_in:>15,.2f} "
        f"{total_out:>15,.2f}"
    )

    print()
    print(f"Net surplus (in - out): £{total_in - total_out:,.2f}")

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

def load_statement_lloyds(filename, stats):
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

def load_statement_monzo(filename, stats):
    """Load a Monzo statement CSV."""
    raise NotImplementedError(f"Fatal error: no support for processing Monzo statement '{filename}'")

def load_statement_amex(filename, stats):
    """Load an American Express credit card statement CSV."""
    raise NotImplementedError(f"Fatal error: no support for processing Amex statement '{filename}'")

def load_statement_capital_one(filename, stats):
    """Load a Capital One credit card statement CSV."""
    raise NotImplementedError(f"Fatal error: no support for processing Capital One statement '{filename}'")

def load_statement_vanguard(filename, stats):
    """Load a Vanguard ISA statement CSV."""
    raise NotImplementedError(f"Fatal error: no support for processing Vanguard statement '{filename}'")

def load_statement_interest(filename, stats):
    """Load an interest certificate or summary."""
    raise NotImplementedError(f"Fatal error: no support for processing interest statement '{filename}'")

def load_statement_pension(filename, stats):
    """Load a pension statement CSV."""
    raise NotImplementedError(f"Fatal error: no support for processing pension statement '{filename}'")

def load_statement_by_type(statement_type, filename, stats):
    """
    Dispatch to the appropriate statement loader based on the type string.
    Returns a list of Transaction objects.
    """
    dispatcher = {
        "bank-lloyds": load_statement_lloyds,
        "debit-monzo": load_statement_monzo,
        "credit-card-amex": load_statement_amex,
        "credit-card-capital-one": load_statement_capital_one,
        "isa-vanguard": load_statement_vanguard,
        "interest": load_statement_interest,
        "pension": load_statement_pension,
        # Keep backward compatibility
        "bank": load_statement_lloyds,  # If someone uses the old 'bank' type
    }

    loader = dispatcher.get(statement_type)
    if loader is None:
        raise ValueError(f"Unknown statement type: '{statement_type}'")

    return loader(filename, stats)

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


def main():

    stats = AnalysisResults()

    args = parse_arguments()

    has_facet_errors = False

    # If --data-file is provided, use it and exit after listing
    if args.data_file:
        if not os.path.isfile(args.data_file):
            print_error(f"data file not found: {args.data_file}", stats)
            return 1

        try:
            control_file_path, tax_years, extra_info_path = load_data_file(args.data_file)
            if not os.path.isfile(control_file_path):
                print_error(f"control file not found: {control_file_path}", stats)
                return 1

            # List the data file active contents
            if args.verbose:
                list_data_file_info(tax_years, args.tax_year)

            # Filter tax years
            if args.tax_year:
                tax_years = [ty for ty in tax_years if ty['year'] in args.tax_year]
            
            # Load control file once
            control = load_control_file(control_file_path)
            # Process each tax year
            for ty in tax_years:
                print()
                print(f"Processing tax year: {ty['year']}")
                print("-" * 50)

                all_transactions = []
                for stmt in ty.get('statements', []):
                    stmt_type = stmt['type']
                    stmt_file = stmt['file']
                    transactions = load_statement_by_type(stmt_type, stmt_file, stats)
                    all_transactions.extend(transactions)

                # Load extra info for this year
                if extra_info_path:
                    extra_info = load_extra_information(extra_info_path, ty['year'])
                    # ... merge into analysis ...
                    # Run analysis on all_transactions
                analysis = analyse_transactions(all_transactions, control)
                print_analysis_report(analysis)

        except Exception as exc:
            print_error(f"Failed to load data file: {exc}", stats)
            return 1

    # If --statement is not given and no --data-file, error
    if not args.statement:
        print_error("Either --statement or --data-file must be provided", stats)
        return 1

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

        # Use the dispatcher with "bank" type for backward compatibility
        transactions = load_statement_by_type("bank", args.statement, stats)

        if not transactions:
            raise RuntimeError(
                "statement contains no transactions"
            )

        print_pass(f"{len(transactions)} transactions loaded", args.verbose, stats)

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

            control = load_control_file(args.control_file)

            analysis = (
                analyse_transactions(
                    transactions,
                    control,
                )
            )

            if args.facet_report:
                if not hasattr(control, 'facet_definitions'):
                    print_error("Facet definitions not loaded in control file.", stats)
                else:
                    print_facet_summary(analysis, args.facet_report, control.facet_definitions)

            print_analysis_report(analysis)

        if args.print_report:
            print_report(
                monthly,
                total_in,
                total_out,
                opening_balance,
                closing_balance,
            )

        if args.analyse:
            # Validate compulsory facets (hardcoded to IHT_ for now)
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
                
                # Always exit with failure if there are errors (even with --relax-facet-checks)
                # But don't return immediately - let the debug prints run first
                # So store a flag to return 1 at the end
                has_facet_errors = True
            else:
                has_facet_errors = False

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

        if has_facet_errors:
            return 1
        return 0

    except Exception as exc:

        print_error(str(exc), stats)
        return 1


if __name__ == "__main__":
    sys.exit(main())
