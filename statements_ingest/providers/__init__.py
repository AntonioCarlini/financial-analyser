"""Provider adapters. Register new ones here.

An adapter class needs:
    name: str
    version: str          # bump when parsing logic changes
    coverage: str         # "monthly" or "ranges", selects the gap report
    discoverer            # object with discover(directory) -> (files, ignored)
    read(path) -> ParsedDocument      # extract text from the file, then parse_text
    parse_text(text) -> ParsedDocument  # pure: needed by the test suite
"""
from .freetrade import FreetradeParser
from .trading212 import Trading212Parser

PROVIDERS = {cls.name: cls for cls in (FreetradeParser, Trading212Parser)}
