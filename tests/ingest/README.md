# Tests for `statements_ingest`

Everything in this directory tests the statement ingester (PDF statements -> consolidated CSVs)
and nothing else. Tests for other parts of the repo belong in their own sibling directory
(`tests/<area>/`), with their own fixtures.

Run:

    python -m unittest discover -s tests/ingest -t .     # just the ingester
    python -m unittest discover -s tests -t .             # everything

(`-t .` matters: it keeps `tests/` from shadowing real packages on the import path.)

## Layers

| File | Tests | Depends on |
|---|---|---|
| `test_parsers.py` | each provider's `parse_text`: golden output + rules every provider must obey | fixtures only |
| `test_store.py` | merge / ordering / idempotence / provider isolation of the CSV store | model classes |
| `test_cli.py` | `python -m statements_ingest` end to end, with text standing in for PDFs | fixtures |
| `test_private_corpus.py` | your real PDFs against previously blessed output (skipped unless configured) | your PDFs |

## Fixtures

`fixtures/<provider>/NAME.txt` is synthetic text laid out like the extracted PDF text, with
`NAME.expected.json` beside it. `<provider>` must be a key in `statements_ingest.providers.PROVIDERS`.
Never put real statement text here.

- `bad_*`        must be rejected
- `notes_*`      must be accepted but raise soft check notes
- `limitation_*` documents a known gap (parses "cleanly" but not correctly)
- anything else  must be completely clean

Rule: every real-world parse failure becomes a fixture *before* the fix.

After an intentional parser change:

    INGEST_UPDATE_GOLDEN=1 python -m unittest tests.ingest.test_parsers

then review the `.expected.json` diff in git.

## Real statements (optional)

    export INGEST_CORPUS=~/statements-corpus     # contains freetrade/*.pdf, trading212/*.pdf (symlinks fine)
    export INGEST_GOLDEN=~/statements-golden     # OUTSIDE the repo: holds your real figures
    INGEST_UPDATE_GOLDEN=1 python -m unittest tests.ingest.test_private_corpus   # bless, then read the output
    python -m unittest tests.ingest.test_private_corpus                          # thereafter
