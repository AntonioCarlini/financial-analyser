from datetime import datetime
from decimal import Decimal
from collections import defaultdict

from financial_statement_analyser.core.types import (
    AnalysisResult,
    CategorySummary,
    ControlFile,
    Rule,
    Transaction,
)
from financial_statement_analyser.core.utils import parse_date, parse_tax_year

CONDITION_CHECKERS = {}

def register_checker(cond_type):
    """Decorator to register a condition checker function."""
    def decorator(func):
        CONDITION_CHECKERS[cond_type] = func
        return func
    return decorator


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

def resolve_ownership(tx, matched_rule, control):
    """
    Determine the ownership split for a transaction.
    Returns a dict: {person_id: percentage}

    Special case: if a rule has ownership: {card_holder: 100},
    it resolves to 100% ownership by the transaction's card_holder.
    """
    if matched_rule and matched_rule.ownership:
        ownership = matched_rule.ownership
        # Check for special card_holder key
        if "card_holder" in ownership:
            if tx.card_holder is None:
                raise ValueError(f"Transaction on line {tx.line_number} has no card_holder, " f"but rule '{matched_rule.id}' uses card_holder ownership.")
            # Replace with actual person ID
            person_id = tx.card_holder
            percentage = ownership["card_holder"]
            if percentage != 100:
                # Warn but use the given percentage
                pass
            return {person_id: percentage}
        else:
            return ownership
    return control.default_ownership

def analyse_transactions(transactions, control, rules):
    summaries = {}
    tx_ownership = {}

    for category_id in control.categories:
        summaries[category_id] = (CategorySummary(category=category_id))

    uncategorised = []
    warnings = []

    category_transactions = {cat_id: [] for cat_id in control.categories}

    facet_assignments = {}

    for tx in transactions:
        matched_rule = None
        for rule in rules:
            if match_rule(tx, rule):
                matched_rule = rule
                break

        if matched_rule is None:
            category_id = (control.default_category)
            uncategorised.append(tx)
        else:
            category_id = (matched_rule.category)

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

            if (matched_rule.direction == "credit"):
                if tx.credit == 0:
                    warnings.append(
                        f"line {tx.line_number}: "
                        f"{tx.description} "
                        f"expected credit"
                    )

            if (matched_rule.direction == "debit"):
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

        # Track per-owner breakdowns
        if matched_rule:
            ownership = resolve_ownership(tx, matched_rule, control)
        else:
            ownership = control.default_ownership

        for owner, percentage in ownership.items():
            if percentage == 0:
                continue
            share = Decimal(percentage) / Decimal(100)
            owner_credit = tx.credit * share
            owner_debit = tx.debit * share

            summary.owner_counts[owner] = summary.owner_counts.get(owner, 0) + 1
            summary.owner_credits[owner] = summary.owner_credits.get(owner, Decimal("0")) + owner_credit
            summary.owner_debits[owner] = summary.owner_debits.get(owner, Decimal("0")) + owner_debit

        # Store ownership for this transaction
        tx_ownership[tx] = ownership

    return AnalysisResult(
        summaries=summaries,
        uncategorised=uncategorised,
        warnings=warnings,
        category_transactions=category_transactions,
        facet_assignments=facet_assignments,
        tx_ownership=tx_ownership,
    )

def merge_analysis_results(base, other):
    """Merge two AnalysisResult objects, combining summaries and lists."""
    if base is None:
        return other

    # Merge summaries
    for cat_id, other_summary in other.summaries.items():
        if cat_id in base.summaries:
            base_summary = base.summaries[cat_id]
            base_summary.transaction_count += other_summary.transaction_count
            base_summary.total_credit += other_summary.total_credit
            base_summary.total_debit += other_summary.total_debit
            # Merge owner dicts
            for owner, count in other_summary.owner_counts.items():
                base_summary.owner_counts[owner] = base_summary.owner_counts.get(owner, 0) + count
            for owner, credit in other_summary.owner_credits.items():
                base_summary.owner_credits[owner] = base_summary.owner_credits.get(owner, Decimal("0")) + credit
            for owner, debit in other_summary.owner_debits.items():
                base_summary.owner_debits[owner] = base_summary.owner_debits.get(owner, Decimal("0")) + debit
        else:
            base.summaries[cat_id] = other_summary

    # Merge uncategorised
    base.uncategorised.extend(other.uncategorised)

    # Merge warnings
    base.warnings.extend(other.warnings)

    # Merge category_transactions
    for cat_id, entries in other.category_transactions.items():
        if cat_id in base.category_transactions:
            base.category_transactions[cat_id].extend(entries)
        else:
            base.category_transactions[cat_id] = entries

    # Merge facet_assignments
    base.facet_assignments.update(other.facet_assignments)

    # Merge tx_ownership
    base.tx_ownership.update(other.tx_ownership)

    return base
