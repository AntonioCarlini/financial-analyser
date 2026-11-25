"""Provider adapters. Register new ones here.

An adapter class needs:
    name: str
    version: str                      # bump when parsing logic changes
    statement_interval_months: int    # used for gap detection
    read(path) -> ParsedStatement
"""
from .freetrade import FreetradeParser

PROVIDERS = {FreetradeParser.name: FreetradeParser}
