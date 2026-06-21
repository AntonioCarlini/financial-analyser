from datetime import datetime
from decimal import Decimal

# Accumulated messages for final recap
_messages = []

def print_pass(message, verbose, stats):
    stats.pass_count += 1

    if verbose:
        print(f"PASS: {message}")

def print_warning(message, stats):
    stats.warning_count += 1
    print(f"WARNING: {message}")
    _messages.append(("WARNING", message))


def print_error(message, stats):
    stats.error_count += 1
    print(f"ERROR: {message}")
    _messages.append(("ERROR", message))

def print_accumulated_messages():
    """Print all accumulated warnings and errors at the end."""
    print("IN ACCUMULATED")
    if not _messages:
        return

    print()
    print("============================================================")
    print("SUMMARY OF WARNINGS AND ERRORS")
    print("============================================================")

    error_count = 0
    warning_count = 0

    for msg_type, msg in _messages:
        print(f"{msg_type}: {msg}")
        if msg_type == "ERROR":
            error_count += 1
        else:
            warning_count += 1

    print()
    print(f"Total errors: {error_count}")
    print(f"Total warnings: {warning_count}")

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

def parse_decimal(value):
    value = value.strip()
    if value == "":
        return Decimal("0")
    value = value.replace(",", "")
    return Decimal(value)

