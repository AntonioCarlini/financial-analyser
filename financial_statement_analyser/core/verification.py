from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from financial_statement_analyser.core.utils import print_pass, print_warning

ALLOWED_DAYS_GAP_AT_START = 9
ALLOWED_DAYS_GAP_AT_END = 5

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
    print_pass("statement is in reverse chronological order", verbose, stats )


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

    print_pass(f"tax year appears to be " f"{start_year}/{str(start_year + 1)[2:]}", verbose, stats)

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

    print_pass(f"{checked} balances verified", verbose, stats)

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
