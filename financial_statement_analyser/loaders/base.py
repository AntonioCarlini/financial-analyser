from financial_statement_analyser.loaders.lloyds import load_statement_lloyds
from financial_statement_analyser.loaders.monzo import load_statement_monzo
from financial_statement_analyser.loaders.amex import load_statement_amex
from financial_statement_analyser.loaders.capital_one import load_statement_capital_one
from financial_statement_analyser.loaders.vanguard import load_statement_vanguard
from financial_statement_analyser.loaders.interest import load_statement_interest
from financial_statement_analyser.loaders.pension import load_statement_pension

def load_statement_by_type(statement_type, filename, verbose, stats):
    """
    Dispatch to the appropriate statement loader based on the type string.
    Returns a list of Transaction objects.
    """
    dispatcher = {
        "bank-lloyds": load_statement_lloyds,
        "debit-monzo": load_statement_monzo,
        "credit-card-amex": load_statement_amex,
        "credit-card-capital-one": load_statement_capital_one,
        "isa-vanguard": load_statement_vanguard,
        "interest": load_statement_interest,
        "pension": load_statement_pension,
    }

    loader = dispatcher.get(statement_type)
    if loader is None:
        raise ValueError(f"Unknown statement type: '{statement_type}'")

    return loader(filename, verbose, stats)
