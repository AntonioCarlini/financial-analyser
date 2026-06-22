from decimal import Decimal
from collections import defaultdict

from financial_statement_analyser.core.types import (
    AnalysisResult,
    ControlFile,
    Transaction,
)
from financial_statement_analyser.core.analysis import match_rule
from financial_statement_analyser.core.utils import print_error

def print_analysis_report(result, control, ownership_report=None):
    """
    Print the category summary.

    Args:
        result: AnalysisResult object.
        control: ControlFile object (for people list).
        ownership_report: False (no ownership), True (all owners), or str (specific owner).
    """
    print()
    print("============================================================")
    print("CATEGORY SUMMARY")
    print("============================================================")
    print()

    # Determine which owners to show
    owners = []
    line_len = 75   ## length if no owners ... adjusted otherwise

    if ownership_report:
        if ownership_report is True:
            # Show all owners defined in the control file
            owners = sorted(control.people.keys())
        elif isinstance(ownership_report, str):
            owners = [ownership_report]

    # Build the two-line header if owners exist
    if owners:
        # First line: owner names, centered above their column groups
        # Base indent: Category(30) + Count(8) + In(15) = 53
        indent = 79
        owner_line = " " * indent
        for owner in owners:
            # Each owner gets 3 columns: Count (8), In (15), Out (15) = 38 chars
            # Plus 8 spaces gap between groups
            owner_line += f"{owner:^45}   "
        print(owner_line)

        # Second line: column labels
        header = f"{'Category':30} {'Count':>8} {'In':>15} {'Out':>15}"
        for owner in owners:
            header += " " * 8
            header += f"{'Count':>8} {'In':>15} {'Out':>15}"
        print(header)

        line_len = 75 + (46 * len(owners))  # 68 base + 38 columns + 8 gap per owner
        print("-" * line_len)
    else:
        header = f"{'Category':30} {'Count':>8} {'In':>15} {'Out':>15}"
        print(header)
        print("-" * line_len)

    for category_id in sorted(result.summaries):
        summary = result.summaries[category_id]

        line = f"{category_id:30} {summary.transaction_count:>8} {summary.total_credit:>15,.2f} {summary.total_debit:>15,.2f}"

        if owners:
            for owner in owners:
                count = summary.owner_counts.get(owner, 0)
                credit = summary.owner_credits.get(owner, Decimal("0"))
                debit = summary.owner_debits.get(owner, Decimal("0"))
                line += " " * 8
                line += f"{count:>8} {credit:>15,.2f} {debit:>15,.2f}"

        print(line)

    # Print totals row
    total_count = sum(s.transaction_count for s in result.summaries.values())
    total_credit = sum(s.total_credit for s in result.summaries.values())
    total_debit = sum(s.total_debit for s in result.summaries.values())

    line = f"{'TOTAL':30} {total_count:>8} {total_credit:>15,.2f} {total_debit:>15,.2f}"
    if owners:
        for owner in owners:
            owner_count = sum(s.owner_counts.get(owner, 0) for s in result.summaries.values())
            owner_credit = sum(s.owner_credits.get(owner, Decimal("0")) for s in result.summaries.values())
            owner_debit = sum(s.owner_debits.get(owner, Decimal("0")) for s in result.summaries.values())
            line += " " * 8
            line += f"{owner_count:>8} {owner_credit:>15,.2f} {owner_debit:>15,.2f}"

    print("-" * line_len)
    print(line)

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
    """
    Print a summary table grouped by facet codes in the specified group.
    facet_definitions: dict from the YAML (e.g., facets: {IHT: {codes: {...}}})

    Args:
        result: AnalysisResult object.
        facet_group_name: The facet group to report (e.g., "IHT").
        facet_definitions: The facet definitions from the control file.
        control: ControlFile object (for people list).
        ownership_report: False (no ownership), True (all owners), or str (specific owner).
    """
    if not facet_group_name:
        return

    if facet_group_name not in facet_definitions:
        print(f"ERROR: Facet group '{facet_group_name}' not found in control file.")
        return

    group = facet_definitions[facet_group_name]
    codes_metadata = group["codes"]

    # Build a dictionary: facet_code -> totals
    facet_totals = {}
    for code, meta in codes_metadata.items():
        facet_totals[code] = {
            "count": 0,
            "total_credit": Decimal("0"),
            "total_debit": Decimal("0"),
            "owner_counts": {},
            "owner_credits": {},
            "owner_debits": {},
        }

    # Determine owners
    owners = []
    if ownership_report:
        if ownership_report is True:
            # Show all owners defined in the control file
            owners = sorted(control.people.keys())
        elif isinstance(ownership_report, str):
            owners = [ownership_report]

    # Re-process transactions with ownership (using category_transactions)
    for cat_id, entries in result.category_transactions.items():
        for tx, rule_id in entries:
            # Get ownership for this transaction
            ownership = control.default_ownership
            matched_rule = None
            for rule in control.rules:
                if match_rule(tx, rule):
                    matched_rule = rule
                    break
            if matched_rule and matched_rule.ownership:
                ownership = matched_rule.ownership

            for facet in result.facet_assignments.get(tx, []):
                if facet in facet_totals:
                    facet_totals[facet]["count"] += 1
                    facet_totals[facet]["total_credit"] += tx.credit
                    facet_totals[facet]["total_debit"] += tx.debit

                    for owner, percentage in ownership.items():
                        if percentage == 0:
                            continue
                        share = Decimal(percentage) / Decimal(100)
                        owner_credit = tx.credit * share
                        owner_debit = tx.debit * share

                        facet_totals[facet]["owner_counts"][owner] = (
                            facet_totals[facet]["owner_counts"].get(owner, 0) + 1
                        )
                        facet_totals[facet]["owner_credits"][owner] = (
                            facet_totals[facet]["owner_credits"].get(owner, Decimal("0")) + owner_credit
                        )
                        facet_totals[facet]["owner_debits"][owner] = (
                            facet_totals[facet]["owner_debits"].get(owner, Decimal("0")) + owner_debit
                        )

    # Print the summary
    print()
    print("============================================================")
    print(f"FACET SUMMARY: {facet_group_name}")
    print(f"Description: {group['description']}")
    print("============================================================")
    print()

    # Build the two-line header if owners exist
    if owners:
        # First line: owner names, centered above their column groups
        # Base indent: Facet(30) + Description(55) + Count(8) + In(15) = 108
        indent = 108
        owner_line = " " * indent
        for owner in owners:
            owner_line += f"{owner:^38}"
        print(owner_line)

        # Second line: column labels
        # Build the two-line header if owners exist
        if owners:
            # First line: owner names, centered above their column groups
            # Base indent: Facet(30) + Description(55) + Count(8) + In(15) = 108
            indent = 108
            owner_line = " " * indent
            for owner in owners:
                # Each owner gets 3 columns: Count (8), In (15), Out (15) = 38 chars
                # Plus 8 spaces gap between groups
                owner_line += f"{owner:^38}"
            print(owner_line)

            # Second line: column labels
            header = f"{'Facet':<30} {'Description':<55} {'Count':>8} {'In':>15} {'Out':>15}"
            for owner in owners:
                header += " " * 8
                header += f"{'Count':>8} {'In':>15} {'Out':>15}"
            print(header)

            line_len = 108 + (46 * len(owners))  # 108 base + 38 columns + 8 gap per owner
            print("-" * line_len)
        else:
            header = f"{'Facet':<30} {'Description':<55} {'Count':>8} {'In':>15} {'Out':>15}"
            print(header)
            print("-" * 110)
    else:
        header = f"{'Facet':<30} {'Description':<55} {'Count':>8} {'In':>15} {'Out':>15}"
        print(header)
        print("-" * 110)

    total_count = 0
    total_in = Decimal("0")
    total_out = Decimal("0")

    for facet_code in sorted(facet_totals):
        meta = codes_metadata.get(facet_code, {})
        if meta.get("suppress_in_report", False):
            continue

        totals = facet_totals[facet_code]
        if totals["count"] == 0 and totals["total_credit"] == 0 and totals["total_debit"] == 0:
            continue

        desc = meta.get("description", "")
        line = (
            f"{facet_code:<30} "
            f"{desc[:55]:<55} "
            f"{totals['count']:>8} "
            f"{totals['total_credit']:>15,.2f} "
            f"{totals['total_debit']:>15,.2f}"
        )

        if owners:
            for owner in owners:
                count = totals["owner_counts"].get(owner, 0)
                credit = totals["owner_credits"].get(owner, Decimal("0"))
                debit = totals["owner_debits"].get(owner, Decimal("0"))
                line += " " * 8
                line += f"{count:>8} {credit:>15,.2f} {debit:>15,.2f}"
        print(line)

        total_count += totals["count"]
        total_in += totals["total_credit"]
        total_out += totals["total_debit"]

    print("-" * line_len)
    line = (
        f"{'TOTAL':<30} "
        f"{'':<55} "
        f"{total_count:>8} "
        f"{total_in:>15,.2f} "
        f"{total_out:>15,.2f}"
    )
    if owners:
        for owner in owners:
            owner_count = sum(facet_totals[f]["owner_counts"].get(owner, 0) for f in facet_totals)
            owner_credit = sum(facet_totals[f]["owner_credits"].get(owner, Decimal("0")) for f in facet_totals)
            owner_debit = sum(facet_totals[f]["owner_debits"].get(owner, Decimal("0")) for f in facet_totals)
            line += " " * 8
            line += f"{owner_count:>8} {owner_credit:>15,.2f} {owner_debit:>15,.2f}"
    print(line)

    print()
    print(f"Net surplus (in - out): £{total_in - total_out:,.2f}")

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

def print_facet_summary(result, facet_group_name, facet_definitions, control, ownership_report=None):
    """
    Print a summary table grouped by facet codes in the specified group.
    facet_definitions: dict from the YAML (e.g., facets: {IHT: {codes: {...}}})

    Args:
        result: AnalysisResult object.
        facet_group_name: The facet group to report (e.g., "IHT").
        facet_definitions: The facet definitions from the control file.
        control: ControlFile object (for people list).
        ownership_report: False (no ownership), True (all owners), or str (specific owner).
    """
    if not facet_group_name:
        return

    if facet_group_name not in facet_definitions:
        print(f"ERROR: Facet group '{facet_group_name}' not found in control file.")
        return

    group = facet_definitions[facet_group_name]
    codes_metadata = group["codes"]

    # Build a dictionary: facet_code -> totals
    facet_totals = {}
    for code, meta in codes_metadata.items():
        facet_totals[code] = {
            "count": 0,
            "total_credit": Decimal("0"),
            "total_debit": Decimal("0"),
            "owner_counts": {},
            "owner_credits": {},
            "owner_debits": {},
        }

    # Determine owners
    owners = []
    if ownership_report:
        if ownership_report is True:
            # Show all owners defined in the control file
            owners = sorted(control.people.keys())
        elif isinstance(ownership_report, str):
            owners = [ownership_report]

    # Re-process transactions with ownership using stored tx_ownership
    for cat_id, entries in result.category_transactions.items():
        for tx, rule_id in entries:
            ownership = result.tx_ownership.get(tx, control.default_ownership)

            for facet in result.facet_assignments.get(tx, []):
                if facet in facet_totals:
                    facet_totals[facet]["count"] += 1
                    facet_totals[facet]["total_credit"] += tx.credit
                    facet_totals[facet]["total_debit"] += tx.debit

                    for owner, percentage in ownership.items():
                        if percentage == 0:
                            continue
                        share = Decimal(percentage) / Decimal(100)
                        owner_credit = tx.credit * share
                        owner_debit = tx.debit * share

                        facet_totals[facet]["owner_counts"][owner] = (
                            facet_totals[facet]["owner_counts"].get(owner, 0) + 1
                        )
                        facet_totals[facet]["owner_credits"][owner] = (
                            facet_totals[facet]["owner_credits"].get(owner, Decimal("0")) + owner_credit
                        )
                        facet_totals[facet]["owner_debits"][owner] = (
                            facet_totals[facet]["owner_debits"].get(owner, Decimal("0")) + owner_debit
                        )

    # Print the summary
    print()
    print("============================================================")
    print(f"FACET SUMMARY: {facet_group_name}")
    print(f"Description: {group['description']}")
    print("============================================================")
    print()

    # Build the two-line header if owners exist
    if owners:
        # First line: owner names, centered above their column groups
        # Base indent: Facet(30) + Description(55) + Count(8) + In(15) = 108
        indent = 135
        owner_line = " " * indent
        for owner in owners:
            # Each owner gets 3 columns: Count (8), In (15), Out (15) = 38 chars
            # Plus 8 spaces gap between groups
            owner_line += f"{owner:^45}   "
        print(owner_line)

        # Second line: column labels
        header = f"{'Facet':<30} {'Description':<55} {'Count':>8} {'In':>15} {'Out':>15}"
        for owner in owners:
            header += " " * 8
            header += f"{'Count':>8} {'In':>15} {'Out':>15}"
        print(header)

        line_len = 108 + (46 * len(owners))  # 108 base + 38 columns + 8 gap per owner
        print("-" * line_len)
    else:
        header = f"{'Facet':<30} {'Description':<55} {'Count':>8} {'In':>15} {'Out':>15}"
        print(header)
        print("-" * 110)

    total_count = 0
    total_in = Decimal("0")
    total_out = Decimal("0")

    for facet_code in sorted(facet_totals):
        meta = codes_metadata.get(facet_code, {})
        if meta.get("suppress_in_report", False):
            continue

        totals = facet_totals[facet_code]
        if totals["count"] == 0 and totals["total_credit"] == 0 and totals["total_debit"] == 0:
            continue

        desc = meta.get("description", "")
        line = (
            f"{facet_code:<30} "
            f"{desc[:55]:<55} "
            f"{totals['count']:>8} "
            f"{totals['total_credit']:>15,.2f} "
            f"{totals['total_debit']:>15,.2f}"
        )

        if owners:
            for owner in owners:
                count = totals["owner_counts"].get(owner, 0)
                credit = totals["owner_credits"].get(owner, Decimal("0"))
                debit = totals["owner_debits"].get(owner, Decimal("0"))
                line += " " * 8
                line += f"{count:>8} {credit:>15,.2f} {debit:>15,.2f}"

        print(line)

        total_count += totals["count"]
        total_in += totals["total_credit"]
        total_out += totals["total_debit"]

    print("-" * line_len)
    line = (
        f"{'TOTAL':<30} "
        f"{'':<55} "
        f"{total_count:>8} "
        f"{total_in:>15,.2f} "
        f"{total_out:>15,.2f}"
    )
    if owners:
        for owner in owners:
            owner_count = sum(facet_totals[f]["owner_counts"].get(owner, 0) for f in facet_totals)
            owner_credit = sum(facet_totals[f]["owner_credits"].get(owner, Decimal("0")) for f in facet_totals)
            owner_debit = sum(facet_totals[f]["owner_debits"].get(owner, Decimal("0")) for f in facet_totals)
            line += " " * 8
            line += f"{owner_count:>8} {owner_credit:>15,.2f} {owner_debit:>15,.2f}"
    print(line)

    print()
    print(f"Net surplus (in - out): £{total_in - total_out:,.2f}")

def print_report( monthly, total_in, total_out, opening_balance, closing_balance):

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
