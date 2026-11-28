# Financial statement analyser

Analyses money flows: bank accounts and credit cards. It reads CSV statements, classifies
every transaction using rules you write in YAML, and reports on them by category, by month
and by "facet" (a tag such as an inheritance-tax code), optionally split between the owners
of shared finances.

Back to the [main README](README.md).

## What it is for

- Categorising the transactions on financial statements.
- Monthly summaries of money in, money out and net.
- Splitting transactions between owners, for shared finances.
- Facet reports, for example a table to help fill in the IHT403 gifts-out-of-income form.

All work is organised by UK tax year (6 April to 5 April).

## Scripts

| Script | Purpose |
|---|---|
| `financial-statement-analyser.py` | The main analysis: loads statements, classifies them, prints reports. |
| `financial-statement-analyser-control-file-checker.py` | Strict validator for `control.yaml`. See [known gaps](#known-gaps): it is currently out of step with the main script. |
| `find-specified-transactions.py` | Lists transactions above or below an amount, optionally filtered by category or facet. |

The code is in the `financial_statement_analyser/` package (`core/` for analysis and reports,
`loaders/` for statement formats).

## Supported statement types

Each statement in the data file has a `type`:

| Type | Input | Status |
|---|---|---|
| `bank-lloyds` | Lloyds bank CSV: `Transaction Date` (DD/MM/YYYY), `Transaction Type`, `Transaction Description`, `Sort Code`, `Account Number`, `Debit Amount`, `Credit Amount`, `Balance` | Working |
| `credit-card-amex` | American Express CSV: `Date` (DD/MM/YYYY), `Description`, `Card Member`, `Account #`, `Amount` (positive = spend, negative = credit) | Working |
| `credit-card-capital-one` | Capital One CSV in either of two layouts, detected from the exact header row: a CSV derived from PDF statements (`statement_date`, `posting_date`, `transaction_date`, `description`, `credit`, `debit`) or the official website export (`postedDate`, `debitCreditCode`, ...) | Working |
| `debit-monzo`, `isa-vanguard`, `interest`, `pension` | | Not implemented: reported as an error if used |

Whatever the source, every row becomes one common transaction: date, transaction type,
description, debit, credit, balance, account details and (for cards) the card holder.
Amex and Capital One provide no running balance, so balances are recorded as zero.

## How it works

1. A **data file** lists the tax years and, for each, the statement files to read.
2. A **control file** defines the people, the categories, the facets and the default
   ownership, and points to a **rules file** for each statement type.
3. A **loader** turns each CSV into transactions.
4. For each transaction the **first matching rule** decides its category. Rules are tried
   in descending `priority` order, with ties kept in file order. If nothing matches, the
   transaction gets the default category and default ownership and is listed as uncategorised.
5. The transaction's **facets** come from the rule, or from its category's `default_facets`.
6. Its **ownership split** comes from the rule, or from the default.
7. Reports are printed, one tax year at a time.

## Running it

```
# Everything in a data file, with the category summary
python financial-statement-analyser.py --data-file data.yaml --analyse

# One tax year, with the per-owner breakdown
python financial-statement-analyser.py --data-file data.yaml --tax-year 2024-2025 \
    --analyse --ownership-report

# A facet report (for example the IHT group), for one owner
python financial-statement-analyser.py --data-file data.yaml --analyse \
    --facet-report IHT --ownership-report AB

# A single statement, without a data file (bank-lloyds unless --statement-type is given)
python financial-statement-analyser.py --statement lloyds.csv --control-file control.yaml --analyse
```

Main options:

| Option | Meaning |
|---|---|
| `--data-file FILE` | The data file. It names the control file, so `--control-file` is not needed. |
| `--statement FILE` | One statement instead of a data file. Needs `--control-file`, and `--statement-type` if it is not `bank-lloyds`. |
| `--tax-year YEAR` | Only process this tax year, such as `2024-2025`. Repeatable. |
| `--analyse` | Run the classification and print the category summary. |
| `--ownership-report [OWNER]` | Break the summaries down by owner. With a value, show only that owner. |
| `--facet-report GROUP` | Summarise by the facet codes of one group, such as `IHT`. |
| `--print-report` | Print the monthly summary. See [known gaps](#known-gaps): it currently needs a dummy value. |
| `--display-category`, `--display-facet` | Debug: list every transaction in a category or with a facet. Repeatable. |
| `--display-description-contains`, `-prefix`, `-suffix` | Debug: list transactions by description. Repeatable. |
| `--verbose` | Show the PASS messages too. |

Run it with `--help` for the full list. The exit status is 1 if there were errors or any
transaction failed the compulsory-facet check.

### Finding specific transactions

```
python find-specified-transactions.py --data-file data.yaml --min-amount 500
python find-specified-transactions.py --data-file data.yaml --min-amount 100 --max-amount 1000 \
    --type debit --include-category gifts --raw-tsv
```

It reuses the data and control files, classifies transactions the same way, and lists those
whose amount falls within the range, in date order. `--include-category` and `--include-facet`
narrow the universe (OR logic), and `--exclude-category` and `--exclude-facet` remove
matches. Output is aligned columns, or tab-separated with `--raw-tsv` for piping.

## The data file

```yaml
control_file: control.yaml          # required; relative to this file
extra_info_file: extra.yaml         # optional; not yet supported (see known gaps)
tax_years:
  - year: "2024-2025"
    description: Optional text
    statements:
      - type: bank-lloyds
        file: lloyds-2024-2025.csv  # relative to this file
```

The tax-year labels are taken on trust; statements are not checked against them in
data-file mode.

## The control file

```yaml
defaults:
  category: uncategorised
  ownership: {AB: 50, CD: 50}       # used when no rule matches

people:
  AB: {full_name: Alex Example}
  CD: {full_name: Chris Example}

categories:
  uncategorised: {description: Not yet classified, default_facets: [IHT_NONE]}
  salary: {description: Salary}
  gifts:
    description: Gifts out of income
    default_facets: [IHT_NORMAL_EXPENDITURE]

facets:                             # optional; groups of codes for --facet-report
  IHT:
    description: Inheritance tax
    codes:
      - {code: IHT_NORMAL_EXPENDITURE, description: Normal expenditure out of income}
      - {code: IHT_NONE, description: Not relevant}      # suppress_in_report: true is also allowed

statement_handling:                 # one entry per statement type used
  - type: bank-lloyds
    rules_file: rules-lloyds.yaml   # relative to the control file
  - type: credit-card-amex
    rules_file: rules-amex.yaml
    cardholder_mapping:             # card member name on the statement -> person id
      A EXAMPLE: AB
      C EXAMPLE: CD
```

**Compulsory facets.** Every transaction must end up with at least one facet starting
`IHT_`, either from its rule or from its category's `default_facets`. Any transaction
without one is listed as a compulsory-facet error and the run fails. In practice every
category, including the default one, needs an `IHT_` facet in its `default_facets`.

**Ownership.** Percentages per person. A rule can set `ownership: {card_holder: 100}`
to give a card transaction wholly to the person its card member maps to.

## Rules files

One per statement type, referenced from `statement_handling`:

```yaml
rules:
  - id: salary
    priority: 10                    # higher is tried first
    match: {prefix: ACME LTD}
    expect: {direction: credit}     # also: transaction_types: [FPI, BGC]
    classify: {category: salary, facets: [IHT_NONE]}
    ownership: {AB: 100}

  - id: supermarket
    match:                          # a list means "any of these"
      - prefix: TESCO
      - prefix: SAINSBURYS
    classify: {category: groceries, facets: [IHT_NONE]}

  - id: birthday-gift
    match: {description: BIRTHDAY GIFT}
    classify: {category: gifts}
    when:                           # optional extra conditions
      - amount_range: [10, 100]
```

- `match` compares with the transaction description, ignoring case. `description` means
  the whole description equals the value, and `prefix` means it starts with it.
- `expect.transaction_types` and `expect.direction` (`credit` or `debit`) must also hold
  for the rule to apply.
- `when` is a list of groups. A rule applies if any group matches (OR), and every condition
  inside a group must hold (AND). The conditions are `amount_range`, `amount_exact`,
  `line_numbers`, `tax_year` and `date_range`.
- `classify.facets` overrides the category's default facets; leave it out to inherit them.

## Reports

- **Category summary** (`--analyse`): per category, transaction count, money in and money out,
  with a per-owner breakdown if requested. Uncategorised transactions are listed after it.
- **Facet summary** (`--facet-report GROUP`): the same by facet code, ending with the net
  surplus.
- **Monthly summary** (`--print-report`): money in, money out and net for each month.
- **Debug listings** (`--display-*`): transactions selected by category, facet or description.

## Privacy

Statements, control files and rules files contain names and spending detail. Keep them in a
directory outside this repository and point `--data-file` at them. See the
[main README](README.md#privacy-what-must-never-be-committed).

## Known gaps

These come from reading and running the code, and are listed so they are not a surprise:

- **Unfinished loaders.** `debit-monzo`, `isa-vanguard`, `interest` and `pension` raise a
  "no support" error. The `extra_info_file` option in the data file is accepted, but
  loading it raises the same error.
- **The control-file checker is out of date.** It requires a top-level `rules` key, which
  the main script ignores, and it rejects `statement_handling`, which the main script needs.
  A control file that works with the analyser therefore fails the checker.
- **`validation.compulsory_facet_prefixes`** is accepted by the checker but not read by the
  main script, which always requires `IHT_`.
- **`--print-report` is meant to be a flag** but is currently defined to take a value, so it
  must be written `--print-report yes`.
- **The header of `financial-statement-analyser.py` over-promises.** It describes verifying
  statement order, recalculating balances and reconciling a ledger. Those functions exist in
  `core/verification.py`, but the main script no longer calls them (apart from the tax-year
  check in `--statement` mode).
