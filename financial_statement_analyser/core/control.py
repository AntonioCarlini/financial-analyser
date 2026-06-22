import os
import yaml

from financial_statement_analyser.core.types import (
    Category,
    ControlFile,
    MatchCondition,
    Person,
    Rule,
)

_rules_cache = {}

def load_control_file(filename):

    with open(filename, "r", encoding="utf-8") as infile:
        raw = yaml.safe_load(infile)

    people = {}

    for person_id, person_data in raw["people"].items():

        people[person_id] = Person(
            id=person_id,
            full_name=person_data["full_name"],
        )

    categories = {}

    for category_id, category_data in (
        raw["categories"].items()
    ):

        categories[category_id] = Category(
            id=category_id,
            description=category_data["description"],
		    default_facets=category_data.get("default_facets", []),
        )

    # Load facet definitions with full metadata
    facet_definitions = {}
    for group_name, group_data in raw.get("facets", {}).items():
        codes_dict = {}
        for item in group_data.get("codes", []):
            codes_dict[item["code"]] = {
                "description": item.get("description", ""),
                "suppress_in_report": item.get("suppress_in_report", False),
            }
        facet_definitions[group_name] = {
            "description": group_data.get("description", ""),
            "codes": codes_dict,
        }

    # Load statement handling mapping (rules file + optional cardholder mapping)
    statement_handling = {}
    for item in raw.get("statement_handling", []):
        stmt_type = item.get("type")
        rules_file = item.get("rules_file")
        if stmt_type and rules_file:
            base_dir = os.path.dirname(filename)
            rules_file_path = os.path.join(base_dir, rules_file)
            cardholder_mapping = item.get("cardholder_mapping", {})
            statement_handling[stmt_type] = {
                "rules_file": rules_file_path,
                "cardholder_mapping": cardholder_mapping,
            }

    rules = []
    for rule_data in raw.get("rules", []):
        match_data = rule_data["match"]
        conditions = []

        if isinstance(match_data, list):
            for cond in match_data:
                # each cond should be a dict with one key, e.g. {"prefix": "XAARJET"}
                for match_type, match_value in cond.items():
                    conditions.append(MatchCondition(type=match_type, value=match_value.upper()))
        elif isinstance(match_data, dict):
            # Backward compatible: {"description": "..."} or {"prefix": "..."}
            for match_type, match_value in match_data.items():
                conditions.append(MatchCondition(type=match_type, value=match_value.upper()))
        else:
            # If it's a plain string, treat as description (old style)
            conditions.append(MatchCondition(type="description", value=match_data.upper()))

        rules.append(
            Rule(
                id=rule_data["id"],
                priority=rule_data.get("priority", 0),
                conditions=conditions,
                category=rule_data["classify"]["category"],
                ownership=rule_data.get("ownership", {}),
                transaction_types=set(rule_data.get("expect", {}).get("transaction_types", [])) or None,
                direction=rule_data.get("expect", {}).get("direction"),
                when=rule_data.get("when"),
                facets=rule_data.get("classify", {}).get("facets"),
            )
        )

    rules.sort(
        key=lambda rule: rule.priority,
        reverse=True,
    )

    return ControlFile(
        people=people,
        categories=categories,
        default_category=raw["defaults"]["category"],
        default_ownership=raw["defaults"]["ownership"],
        facet_definitions=facet_definitions,
        statement_handling=statement_handling,
    )

def load_rules_file(filename):
    """Load a rules file (YAML with a 'rules' list) and return the list of Rule objects."""
    if filename in _rules_cache:
        return _rules_cache[filename]

    with open(filename, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    rules_data = raw.get("rules", [])
    if not isinstance(rules_data, list):
        raise ValueError(f"rules file {filename} must contain a 'rules' list")

    rules = []
    for rule_data in rules_data:
        match_data = rule_data["match"]
        conditions = []

        if isinstance(match_data, list):
            for cond in match_data:
                for match_type, match_value in cond.items():
                    conditions.append(MatchCondition(type=match_type, value=match_value.upper()))
        elif isinstance(match_data, dict):
            for match_type, match_value in match_data.items():
                conditions.append(MatchCondition(type=match_type, value=match_value.upper()))
        else:
            conditions.append(MatchCondition(type="description", value=match_data.upper()))

        rules.append(
            Rule(
                id=rule_data["id"],
                priority=rule_data.get("priority", 0),
                conditions=conditions,
                category=rule_data["classify"]["category"],
                ownership=rule_data.get("ownership", {}),
                transaction_types=set(rule_data.get("expect", {}).get("transaction_types", [])) or None,
                direction=rule_data.get("expect", {}).get("direction"),
                when=rule_data.get("when"),
                facets=rule_data.get("classify", {}).get("facets"),
            )
        )

    rules.sort(key=lambda rule: rule.priority, reverse=True)
    _rules_cache[filename] = rules
    return rules

def get_rules_for_type(statement_type, control):
    """Look up and load the rules for a given statement type."""
    if statement_type not in control.statement_handling:
        raise ValueError(f"No rules file defined for statement type '{statement_type}'")
    rules_file = control.statement_handling[statement_type]["rules_file"]
    return load_rules_file(rules_file)

def get_cardholder_mapping(statement_type, control):
    """Return the cardholder mapping dict for a statement type, or empty dict."""
    if statement_type in control.statement_handling:
        return control.statement_handling[statement_type].get("cardholder_mapping", {})
    return {}
