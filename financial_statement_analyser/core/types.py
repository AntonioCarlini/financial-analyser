from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

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
    default_category: str
    default_ownership: dict[str, int]
    facet_definitions: dict = field(default_factory=dict)
    statement_handling: dict[str, str] = field(default_factory=dict)

@dataclass
class CategorySummary:
    category: str

    transaction_count: int = 0

    total_credit: Decimal = Decimal("0")
    total_debit: Decimal = Decimal("0")

    # Per-owner breakdowns
    owner_counts: dict[str, int] = field(default_factory=dict)
    owner_credits: dict[str, Decimal] = field(default_factory=dict)
    owner_debits: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    summaries: dict[str, CategorySummary]
    uncategorised: list[Transaction]
    warnings: list[str]
    category_transactions: dict[str, list[tuple[Transaction, str | None]]] = field(default_factory=dict)
    facet_assignments: dict[Transaction, list[str]] = field(default_factory=dict)
    tx_ownership: dict[Transaction, dict[str, int]] = field(default_factory=dict)

