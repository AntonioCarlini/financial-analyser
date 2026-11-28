# Statement ingester (`statements_ingest`)

Reads statements from investment and pension providers (PDFs today) and turns them into
uniform, date-ordered CSV tables. The tables are meant for analysis with other tools:
the performance of an individual GIA, ISA or pension, checking that documents agree
with each other, and a consolidated view across providers and people.

Each provider has its own adapter. Adapters only translate one provider's documents into a
common model. Merging, ordering, de-duplication and checking are shared.

Back to the [main README](README.md).

## Supported sources

| Provider | Document | What it contains | Adapter |
|---|---|---|---|
| Freetrade | Monthly statement PDF | Month-end positions, cash and totals | `freetrade` |
| Trading 212 | Activity statement PDF, any date range | Closing positions and cash, overview totals for the range, trades, deposits, free shares, dividends | `trading212` |


## Requirements

- Python 3.12 (what it is developed on).
- `pip install pdfplumber`, needed only to read real PDFs. The test suite does not need it.

## Quick start

Run from the repository root. A `--dry-run` option is available.

```
python -m statements_ingest ~/statements/freetrade --provider freetrade \
    --store ~/finance-data --person AB --account-ref <your-account-reference> --dry-run

python -m statements_ingest ~/statements/trading212 --provider trading212 \
    --store ~/finance-data --person AB --dry-run
```

The statements directory is only ever read.
The store directory is created if it does not exist, but only when there is valid data to put in it.

| Option | Meaning |
|---|---|
| `statements_dir` | Directory containing the PDFs. |
| `--provider` | Which adapter to use (`freetrade`, `trading212`). |
| `--store` | Directory for the output CSVs. Keep it outside the repository. |
| `--person` | The owner's initials, for example `AB`. Stored in every row. |
| `--store-prefix` | File name prefix. Defaults to the person's initials. Use one prefix for several people to keep them in the same files. |
| `--account-ref` | Account reference to use when a statement does not print one, and to check against it when it does. For single-account runs only. |
| `--account-label` | Free-text label stored with new rows. |
| `--allow-update` | Replace stored data whose parsed content has changed. Without it, a change is reported as a conflict and nothing is altered. |
| `--dry-run` | Report what would happen and write nothing. |

The exit status is 0 on success, 2 if any statement was rejected or conflicted, and 1 for
a bad directory or an unreadable store.

## What you get

Statement content falls into three kinds, so there are four tables per prefix:

| File | Holds | Kind |
|---|---|---|
| `{prefix}_snapshots.csv` | Totals reported for an account at one moment | state |
| `{prefix}_positions.csv` | The holdings belonging to a snapshot | state |
| `{prefix}_period_totals.csv` | One measure over a date range, one row per measure (deposits, dividends, fees, and so on) | flows |
| `{prefix}_transactions.csv` | Individual dated events: trades, deposits, dividends | events |

All providers share the same files and are distinguished by the `provider` column.

### Columns

Every table starts with identifying columns: `provider`, `person`, `account_ref`,
`source_type` (the kind of document, for example `statement` or `activity_statement`).
Snapshot, period-total and transaction rows also end with provenance columns:
`source_file`, `source_sha256`, `parser_version`.

- **snapshots:** `account_type`, `account_label`, `as_of`, `currency`, `portfolio_reported`,
  `holdings_reported`, `cash_reported`.
- **positions:** `as_of`, `instrument_name`, `ticker`, `isin`, `quantity`, `value`, `currency`,
  `price`, `avg_price`, `price_currency`, `fx_rate`.
- **period_totals:** `period_start`, `period_end`, `measure`, `amount`, `currency`.
- **transactions:** `timestamp`, `kind`, `raw_type`, `ticker`, `isin`, `quantity`, `price`,
  `price_currency`, `fx_rate`, `amount`, `currency`, `fees`, `fx_fee`, `withholding_tax`,
  `external_id`, `seq`.

### Conventions

- **Account types:** `GIA`, `ISA` or `PENSION`. Provider terms are mapped onto these (a SIPP
  is `PENSION`). `account_label` is free text for your own use.
- **People:** `person` is initials. Several people can share one store; every key includes it.
- **Dates:** `as_of` is an ISO date (Freetrade: the month end) or a UTC timestamp (Trading 212:
  the end of the covered range). Timestamps are ISO 8601 UTC. Trading 212 times are treated
  as UTC.
- **Money:** `value` and `amount` are always in the account's currency, in pounds, never
  pence. Native prices keep their own currency code in `price_currency`, with `GBX` for pence.
  Amounts are signed from the account's point of view: money in is positive.
- **Exchange rates:** `fx_rate` is stored as the provider prints it. For Trading 212, trades
  and positions give instrument units per pound (pence: 100), and dividends give pounds per
  instrument unit (pence: 0.01).
- **Transaction kinds:** `deposit`, `withdrawal`, `free_shares`, `dividend`, `interest`, `fee`,
  `buy`, `sell`, `corporate_action`. `raw_type` keeps the provider's own wording.
- **Quantities:** signed for `buy`, `sell` and `corporate_action` (a sell is negative), so
  trades can be replayed. For a `dividend` it is the eligible holding, not a position change.
- **Reported, not derived:** totals are stored as the statement printed them. Consistency
  checks are code, not stored columns, so a failed check never loses the original numbers.
- **Overlapping ranges:** period totals from overlapping date ranges are all kept. Do not
  sum across ranges that overlap.
- **Different documents about the same thing** (for example a monthly statement and an
  annual statement) are stored side by side, never merged, so they can be compared.

## How a run behaves

The store is always loaded, merged by key, sorted by date and rewritten in full, never
appended to. A statement found late therefore lands in its proper place, and re-running
on the same files changes nothing. Each account in a document is applied all-or-nothing.

Every account ends with a status:

| Status | Meaning |
|---|---|
| `ADDED` | New data was stored. |
| `UNCHANGED` | Already stored with identical content. Provenance and label differences do not count. |
| `UPDATED` | Stored data was replaced (only with `--allow-update`). |
| `CONFLICT` | The same statement now parses differently. Nothing is changed unless `--allow-update` is given. |
| `REJECTED` | A hard check failed. Nothing is stored for that account. |
| `IGNORED` | The file name did not match what the provider expects. |

**Hard checks** reject an account. They cover rows that cannot be parsed, a filename that
disagrees with the statement's contents, a missing account reference, an unrecognised
transaction type, and (Freetrade) the arithmetic: portfolio equals holdings plus cash, and
the positions add up to total holdings. Messages show offending rows with letters replaced by
`X` and digits by `9`, so they can be pasted into a bug report without exposing figures.

**Soft checks** are printed as `check:` lines and do not stop the data being stored. For
Trading 212 they compare positions plus cash with the account value, each overview figure
with the sum of its transactions, and the dividend and exchange-rate arithmetic. Once a
soft check proves reliable on real data it can be promoted to a hard one.

Afterwards the run reports gaps: missing months for monthly providers, or stretches of
dates covered by no statement for date-range providers.

## Provider notes

**Freetrade.** Files are named `<TYPE>-Statement-YYYY-MM.pdf`, for example
`GIA-Statement-2026-10.pdf`. Statements give month-end values only: no prices, no cost
basis, no ISIN. Older statements print no account reference, so pass `--account-ref`; if a
statement does print one, it must match. Each statement is cross-checked against its
filename (type and month).

**Trading 212.** Files must start `Activity-Statement`; the date range is read from inside
the PDF. A PDF can hold more than one account, and each is stored separately. The wording
used in the transactions list (deposit methods, free shares, interest, and so on) is
mapped to a kind in `RAW_TYPE_KINDS` in `providers/trading212.py`. An unknown wording is
rejected by name rather than guessed, so add the new wording there with the right kind and
re-run. Stock splits are stored as pairs of `corporate_action` rows with no
cash.

## Privacy

Statements and the generated tables contain account references and balances. Keep both
outside the repository (see the [main README](README.md#privacy-what-must-never-be-committed)).
The files in `tests/ingest/fixtures/` are synthetic.

## Adding a provider

1. Create `statements_ingest/providers/<name>.py` with a class that has `name`, `version`,
   `coverage` (`"monthly"` or `"ranges"`), a `discoverer`, `read(path)` and a pure
   `parse_text(text)` returning a `ParsedDocument`. `parse_text` must not touch the
   filesystem, so it can be tested without PDFs.
2. Register the class in `providers/__init__.py`.
3. If the file naming differs from the existing schemes, add a discoverer to `discovery.py`.
4. Add synthetic fixtures under `tests/ingest/fixtures/<name>/`, including some `bad_` ones,
   and generate their expected output (see below).
5. Bump `version` whenever the parsing logic changes.

The shared contract tests and the cross-provider isolation tests then cover the new
adapter without further code. Every real-world parse failure should become a fixture
before it is fixed.

## Package layout

```
statements_ingest/
  __main__.py        command line, cross-checks, reporting
  models.py          the common data model and the hard consistency checks
  store.py           the four CSV tables: merge, order, de-duplicate, gaps
  discovery.py       finding files and reading hints from their names
  providers/
    __init__.py      registry
    freetrade.py
    trading212.py
```

## Tests

```
python -m unittest discover -s tests/ingest -t .
```

The tests use synthetic text in place of PDFs. See [tests/ingest/README.md](tests/ingest/README.md)
for the fixture conventions, how to regenerate expected output, and how to run the optional
regression test against your real statements.

## Known limitations

- Freetrade `as_of` is the end of the statement's month; this is assumed rather than confirmed from the statement.
- A Freetrade holding name that wraps onto two lines is not always detected. A missing  ticker raises a soft check, but one layout cannot be detected at all (see the `limitation_` fixture).
- The layout of a Trading 212 PDF containing several accounts is assumed, not yet seen.
- Trading 212 "settled cash" is ignored; only the cash figure is stored.
- Nothing yet verifies positions by replaying trades from one statement to the next, or reconciles documents against each other.
- Freetrade positions have no ISIN, so matching them to other providers' holdings will need  a hand-maintained instrument mapping.
- There is no accounts registry, so gap reports cannot know when an account opened or closed.
