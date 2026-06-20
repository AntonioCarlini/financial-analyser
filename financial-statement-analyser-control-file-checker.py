#!/usr/bin/env python3
"""
financial-statement-analyser-control-file-checker.py

Strict validator for control.yaml used by financial-statement-analyser.py.

Checks for:
- Required top-level keys: defaults, people, categories, rules
- Optional top-level keys: version, facets, validation
- No unknown top-level keys
- Allowed key names and types everywhere
- direction is inside expect (not at rule level)
- match, classify, expect, when blocks are correctly structured
- Duplicate rule IDs
- Valid direction values
- Categories and facets referenced in rules exist
- Ownership sums to exactly 100
- No hardcoded transaction type validation (left to main script)
"""

import argparse
import sys
import yaml
from decimal import Decimal
from pathlib import Path


# ----------------------------------------------------------------------
# Constants: allowed values and key sets
# ----------------------------------------------------------------------

# Valid direction values
VALID_DIRECTIONS = {"credit", "debit"}

# Valid condition types for "when" clauses
VALID_WHEN_CONDITIONS = {"amount_range", "amount_exact", "line_numbers", "tax_year", "date_range"}

# Allowed top-level keys
ALLOWED_TOP_LEVEL_KEYS = {
    "version",
    "defaults",
    "people",
    "facets",
    "validation",
    "categories",
    "rules",
}

# Keys allowed inside a rule
ALLOWED_RULE_KEYS = {
    "id",
    "priority",
    "match",
    "expect",
    "classify",
    "ownership",
    "when",
}

# Keys allowed inside expect
ALLOWED_EXPECT_KEYS = {
    "transaction_types",
    "direction",
}

# Keys allowed inside classify
ALLOWED_CLASSIFY_KEYS = {
    "category",
    "facets",
}

# Keys allowed inside match conditions
ALLOWED_MATCH_TYPES = {
    "description",
    "prefix",
}


# ----------------------------------------------------------------------
# Helper classes for validation results
# ----------------------------------------------------------------------

class ValidationError:
    """Represents a validation error with optional line number."""

    def __init__(self, message, line=None, column=None):
        self.message = message
        self.line = line
        self.column = column

    def __str__(self):
        if self.line is not None:
            return f"Line {self.line}: {self.message}"
        return self.message


class ValidationWarning:
    """Represents a validation warning with optional line number."""

    def __init__(self, message, line=None, column=None):
        self.message = message
        self.line = line
        self.column = column

    def __str__(self):
        if self.line is not None:
            return f"Line {self.line}: {self.message}"
        return self.message


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------

def get_rule_identifier(rule_data, idx):
    """
    Get a human-readable identifier for a rule.

    Args:
        rule_data (dict): The rule data.
        idx (int): The rule index (1-based).

    Returns:
        str: Either "ID: 'rule_id'" or "#idx" if no ID is present.
    """
    if "id" in rule_data and isinstance(rule_data["id"], str) and rule_data["id"].strip():
        return f"ID: '{rule_data['id']}'"
    return f"#{idx}"


# ----------------------------------------------------------------------
# Main validation function
# ----------------------------------------------------------------------

def validate_control_file(filename):
    """
    Validate the control.yaml file.

    Args:
        filename (str): Path to the YAML file.

    Returns:
        tuple: (list of ValidationError, list of ValidationWarning)
    """
    errors = []
    warnings = []

    # Attempt to load the YAML file
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(ValidationError(f"YAML syntax error: {e}"))
        return errors, warnings
    except FileNotFoundError:
        errors.append(ValidationError(f"File not found: {filename}"))
        return errors, warnings

    if raw is None:
        errors.append(ValidationError("File is empty."))
        return errors, warnings

    # ------------------------------------------------------------------
    # 1. Check top-level keys
    # ------------------------------------------------------------------
    top_keys = set(raw.keys())

    # Required keys
    required_keys = {"defaults", "people", "categories", "rules"}
    for key in required_keys:
        if key not in raw:
            errors.append(ValidationError(f"Missing required top-level key: '{key}'"))

    # Check for unknown keys
    unknown_keys = top_keys - ALLOWED_TOP_LEVEL_KEYS
    for key in unknown_keys:
        errors.append(ValidationError(f"Unknown top-level key: '{key}'"))

    # Collect people, category, and facet IDs for cross-referencing
    people_ids = set()
    category_ids = set()
    facet_definition_codes = set()   # codes defined in facets section
    facet_reference_codes = set()    # codes referenced in categories/rules

    # ------------------------------------------------------------------
    # 2. Validate 'defaults'
    # ------------------------------------------------------------------
    if "defaults" in raw:
        defaults = raw["defaults"]
        if not isinstance(defaults, dict):
            errors.append(ValidationError("'defaults' must be a dictionary."))
        else:
            if "category" not in defaults:
                errors.append(ValidationError("'defaults.category' is required."))
            elif not isinstance(defaults["category"], str) or not defaults["category"].strip():
                errors.append(ValidationError("'defaults.category' must be a non-empty string."))

            if "ownership" not in defaults:
                errors.append(ValidationError("'defaults.ownership' is required."))
            elif not isinstance(defaults["ownership"], dict):
                errors.append(ValidationError("'defaults.ownership' must be a dictionary."))
            else:
                # Ownership values will be validated after people are loaded
                pass

            # Check for unexpected keys in defaults
            allowed_defaults_keys = {"category", "ownership"}
            unknown_defaults = set(defaults.keys()) - allowed_defaults_keys
            for key in unknown_defaults:
                errors.append(ValidationError(f"Unknown key in 'defaults': '{key}'"))

    # ------------------------------------------------------------------
    # 3. Validate 'people'
    # ------------------------------------------------------------------
    if "people" in raw:
        people = raw["people"]
        if not isinstance(people, dict):
            errors.append(ValidationError("'people' must be a dictionary."))
        else:
            for person_id, person_data in people.items():
                if not isinstance(person_id, str) or not person_id.strip():
                    errors.append(ValidationError(f"Person ID must be a non-empty string (got: {person_id})"))
                    continue

                people_ids.add(person_id)

                if not isinstance(person_data, dict):
                    errors.append(ValidationError(f"Person '{person_id}' must be a dictionary."))
                    continue

                if "full_name" not in person_data:
                    errors.append(ValidationError(f"Person '{person_id}' missing 'full_name'"))
                elif not isinstance(person_data["full_name"], str) or not person_data["full_name"].strip():
                    errors.append(ValidationError(f"Person '{person_id}' has empty 'full_name'"))

                # Check for unexpected keys
                allowed_person_keys = {"full_name"}
                unknown_person_keys = set(person_data.keys()) - allowed_person_keys
                for key in unknown_person_keys:
                    errors.append(ValidationError(f"Person '{person_id}' has unknown key: '{key}'"))

    # ------------------------------------------------------------------
    # 4. Validate 'categories'
    # ------------------------------------------------------------------
    if "categories" in raw:
        categories = raw["categories"]
        if not isinstance(categories, dict):
            errors.append(ValidationError("'categories' must be a dictionary."))
        else:
            for cat_id, cat_data in categories.items():
                if not isinstance(cat_id, str) or not cat_id.strip():
                    errors.append(ValidationError(f"Category ID must be a non-empty string (got: {cat_id})"))
                    continue

                category_ids.add(cat_id)

                if not isinstance(cat_data, dict):
                    errors.append(ValidationError(f"Category '{cat_id}' must be a dictionary."))
                    continue

                if "description" not in cat_data:
                    errors.append(ValidationError(f"Category '{cat_id}' missing 'description'"))
                elif not isinstance(cat_data["description"], str) or not cat_data["description"].strip():
                    errors.append(ValidationError(f"Category '{cat_id}' has empty 'description'"))

                # Check default_facets if present
                if "default_facets" in cat_data:
                    if not isinstance(cat_data["default_facets"], list):
                        errors.append(ValidationError(
                            f"Category '{cat_id}'.default_facets must be a list (got {type(cat_data['default_facets']).__name__})"
                        ))
                    else:
                        for facet in cat_data["default_facets"]:
                            if not isinstance(facet, str) or not facet.strip():
                                errors.append(ValidationError(
                                    f"Category '{cat_id}'.default_facets contains empty or non-string value: {facet}"
                                ))
                            else:
                                facet_reference_codes.add(facet)

                # Check for unexpected keys in category
                allowed_category_keys = {"description", "default_facets"}
                unknown_category_keys = set(cat_data.keys()) - allowed_category_keys
                for key in unknown_category_keys:
                    errors.append(ValidationError(f"Category '{cat_id}' has unknown key: '{key}'"))

    # ------------------------------------------------------------------
    # 5. Validate 'facets' (if present)
    # ------------------------------------------------------------------
    if "facets" in raw:
        facets = raw["facets"]
        if not isinstance(facets, dict):
            errors.append(ValidationError("'facets' must be a dictionary."))
        else:
            for group_name, group_data in facets.items():
                if not isinstance(group_name, str) or not group_name.strip():
                    errors.append(ValidationError(f"Facet group name must be a non-empty string (got: {group_name})"))
                    continue

                if not isinstance(group_data, dict):
                    errors.append(ValidationError(f"Facet group '{group_name}' must be a dictionary."))
                    continue

                if "description" not in group_data:
                    warnings.append(ValidationWarning(
                        f"Facet group '{group_name}' missing 'description' (optional but recommended)"
                    ))

                if "codes" not in group_data:
                    errors.append(ValidationError(f"Facet group '{group_name}' missing 'codes'"))
                elif not isinstance(group_data["codes"], list):
                    errors.append(ValidationError(
                        f"Facet group '{group_name}'.codes must be a list (got {type(group_data['codes']).__name__})"
                    ))
                else:
                    for code_item in group_data["codes"]:
                        if not isinstance(code_item, dict):
                            errors.append(ValidationError(
                                f"Facet item in group '{group_name}' must be a dictionary"
                            ))
                            continue

                        if "code" not in code_item:
                            errors.append(ValidationError(
                                f"Facet item in group '{group_name}' missing 'code'"
                            ))
                        else:
                            code = code_item["code"]
                            if not isinstance(code, str) or not code.strip():
                                errors.append(ValidationError(
                                    f"Facet code in group '{group_name}' must be a non-empty string"
                                ))
                            else:
                                if code in facet_definition_codes:
                                    warnings.append(ValidationWarning(
                                        f"Duplicate facet code: '{code}' (group '{group_name}')"
                                    ))
                                facet_definition_codes.add(code)

                        if "description" not in code_item:
                            warnings.append(ValidationWarning(
                                f"Facet '{code_item.get('code', 'unknown')}' missing 'description' (optional but recommended)"
                            ))

                        if "suppress_in_report" in code_item:
                            if not isinstance(code_item["suppress_in_report"], bool):
                                errors.append(ValidationError(
                                    f"Facet '{code_item.get('code', 'unknown')}'.suppress_in_report must be a boolean"
                                ))

                        # Check for unexpected keys in facet item
                        allowed_facet_item_keys = {"code", "description", "suppress_in_report"}
                        unknown_facet_item_keys = set(code_item.keys()) - allowed_facet_item_keys
                        for key in unknown_facet_item_keys:
                            errors.append(ValidationError(
                                f"Facet '{code_item.get('code', 'unknown')}' has unknown key: '{key}'"
                            ))

                # Check for unexpected keys in facet group
                allowed_group_keys = {"description", "codes"}
                unknown_group_keys = set(group_data.keys()) - allowed_group_keys
                for key in unknown_group_keys:
                    errors.append(ValidationError(f"Facet group '{group_name}' has unknown key: '{key}'"))

    # ------------------------------------------------------------------
    # 6. Validate 'validation' (if present)
    # ------------------------------------------------------------------
    if "validation" in raw:
        validation = raw["validation"]
        if not isinstance(validation, dict):
            errors.append(ValidationError("'validation' must be a dictionary."))
        else:
            if "compulsory_facet_prefixes" in validation:
                prefixes = validation["compulsory_facet_prefixes"]
                if not isinstance(prefixes, list):
                    errors.append(ValidationError(
                        "'validation.compulsory_facet_prefixes' must be a list"
                    ))
                else:
                    for prefix in prefixes:
                        if not isinstance(prefix, str) or not prefix.strip():
                            errors.append(ValidationError(
                                f"'validation.compulsory_facet_prefixes' contains invalid value: {prefix}"
                            ))

            # Check for unexpected keys in validation
            allowed_validation_keys = {"compulsory_facet_prefixes"}
            unknown_validation_keys = set(validation.keys()) - allowed_validation_keys
            for key in unknown_validation_keys:
                errors.append(ValidationError(f"'validation' has unknown key: '{key}'"))

    # ------------------------------------------------------------------
    # 7. Validate 'rules'
    # ------------------------------------------------------------------
    if "rules" in raw:
        rules = raw["rules"]
        if not isinstance(rules, list):
            errors.append(ValidationError("'rules' must be a list."))
        else:
            rule_ids = set()
            for idx, rule_data in enumerate(rules, start=1):
                if not isinstance(rule_data, dict):
                    errors.append(ValidationError(f"Rule #{idx} must be a dictionary."))
                    continue

                # Get a human-readable identifier for this rule
                rid = get_rule_identifier(rule_data, idx)

                # 7a. Check rule-level keys
                unknown_rule_keys = set(rule_data.keys()) - ALLOWED_RULE_KEYS
                for key in unknown_rule_keys:
                    errors.append(ValidationError(f"{rid} has unknown key: '{key}'"))

                # 7b. Validate 'id'
                if "id" not in rule_data:
                    errors.append(ValidationError(f"{rid} missing 'id'"))
                else:
                    rule_id = rule_data["id"]
                    if not isinstance(rule_id, str) or not rule_id.strip():
                        errors.append(ValidationError(f"{rid} has invalid 'id': must be non-empty string"))
                    else:
                        if rule_id in rule_ids:
                            errors.append(ValidationError(f"Duplicate rule ID: '{rule_id}' (also in {rid})"))
                        rule_ids.add(rule_id)

                # 7c. Validate 'priority'
                if "priority" not in rule_data:
                    errors.append(ValidationError(f"{rid} missing 'priority'"))
                else:
                    priority = rule_data["priority"]
                    if not isinstance(priority, int):
                        errors.append(ValidationError(f"{rid} 'priority' must be an integer (got {type(priority).__name__})"))

                # 7d. Validate 'match'
                if "match" not in rule_data:
                    errors.append(ValidationError(f"{rid} missing 'match'"))
                else:
                    validate_match(rule_data["match"], rid, errors)

                # 7e. Validate 'expect' (optional)
                if "expect" in rule_data:
                    expect = rule_data["expect"]
                    if not isinstance(expect, dict):
                        errors.append(ValidationError(f"{rid} 'expect' must be a dictionary"))
                    else:
                        # Check for unexpected keys in expect
                        unknown_expect_keys = set(expect.keys()) - ALLOWED_EXPECT_KEYS
                        for key in unknown_expect_keys:
                            errors.append(ValidationError(f"{rid} 'expect' has unknown key: '{key}'"))

                        # Validate transaction_types
                        if "transaction_types" in expect:
                            tx_types = expect["transaction_types"]
                            if not isinstance(tx_types, list):
                                errors.append(ValidationError(
                                    f"{rid} 'expect.transaction_types' must be a list"
                                ))
                            else:
                                for tx_type in tx_types:
                                    if not isinstance(tx_type, str) or not tx_type.strip():
                                        errors.append(ValidationError(
                                            f"{rid} 'expect.transaction_types' contains invalid value: {tx_type}"
                                        ))

                        # Validate direction
                        if "direction" in expect:
                            direction = expect["direction"]
                            if not isinstance(direction, str):
                                errors.append(ValidationError(
                                    f"{rid} 'expect.direction' must be a string"
                                ))
                            elif direction not in VALID_DIRECTIONS:
                                errors.append(ValidationError(
                                    f"{rid} 'expect.direction' must be 'credit' or 'debit' (got '{direction}')"
                                ))

                # 7f. Validate 'classify'
                if "classify" not in rule_data:
                    errors.append(ValidationError(f"{rid} missing 'classify'"))
                else:
                    classify = rule_data["classify"]
                    if not isinstance(classify, dict):
                        errors.append(ValidationError(f"{rid} 'classify' must be a dictionary"))
                    else:
                        # Check for unexpected keys in classify
                        unknown_classify_keys = set(classify.keys()) - ALLOWED_CLASSIFY_KEYS
                        for key in unknown_classify_keys:
                            errors.append(ValidationError(f"{rid} 'classify' has unknown key: '{key}'"))

                        # Validate category
                        if "category" not in classify:
                            errors.append(ValidationError(f"{rid} 'classify.category' is required"))
                        else:
                            category = classify["category"]
                            if not isinstance(category, str) or not category.strip():
                                errors.append(ValidationError(
                                    f"{rid} 'classify.category' must be a non-empty string"
                                ))
                            else:
                                if category not in category_ids:
                                    errors.append(ValidationError(
                                        f"{rid} 'classify.category' references unknown category: '{category}'"
                                    ))

                        # Validate facets (optional)
                        if "facets" in classify:
                            facets_val = classify["facets"]
                            if isinstance(facets_val, str):
                                if not facets_val.strip():
                                    errors.append(ValidationError(
                                        f"{rid} 'classify.facets' cannot be an empty string"
                                    ))
                                else:
                                    if facets_val not in facet_definition_codes:
                                        errors.append(ValidationError(
                                            f"{rid} 'classify.facets' references unknown facet: '{facets_val}'"
                                        ))
                            elif isinstance(facets_val, list):
                                for facet in facets_val:
                                    if not isinstance(facet, str) or not facet.strip():
                                        errors.append(ValidationError(
                                            f"{rid} 'classify.facets' contains invalid value: {facet}"
                                        ))
                                    else:
                                        if facet not in facet_definition_codes:
                                            errors.append(ValidationError(
                                                f"{rid} 'classify.facets' references unknown facet: '{facet}'"
                                            ))
                            else:
                                errors.append(ValidationError(
                                    f"{rid} 'classify.facets' must be a string or list of strings"
                                ))

                # 7g. Validate 'ownership' (optional)
                if "ownership" in rule_data:
                    ownership = rule_data["ownership"]
                    if not isinstance(ownership, dict):
                        errors.append(ValidationError(f"{rid} 'ownership' must be a dictionary"))
                    else:
                        total_ownership = 0
                        for person_id, pct in ownership.items():
                            if person_id not in people_ids:
                                errors.append(ValidationError(
                                    f"{rid} 'ownership' references unknown person: '{person_id}'"
                                ))
                            if not isinstance(pct, int):
                                errors.append(ValidationError(
                                    f"{rid} 'ownership' for '{person_id}' must be an integer (got {type(pct).__name__})"
                                ))
                            elif pct < 0 or pct > 100:
                                errors.append(ValidationError(
                                    f"{rid} 'ownership' for '{person_id}' must be between 0 and 100 (got {pct})"
                                ))
                            total_ownership += pct

                        if total_ownership != 100:
                            errors.append(ValidationError(
                                f"{rid} 'ownership' totals must sum to exactly 100 (got {total_ownership})"
                            ))

                # 7h. Validate 'when' (optional)
                if "when" in rule_data:
                    when = rule_data["when"]
                    if not isinstance(when, list):
                        errors.append(ValidationError(f"{rid} 'when' must be a list"))
                    else:
                        for group_idx, group in enumerate(when, start=1):
                            if not isinstance(group, dict):
                                errors.append(ValidationError(
                                    f"{rid} 'when' group #{group_idx} must be a dictionary"
                                ))
                                continue

                            # Each group is a dict of conditions
                            for cond_type, cond_value in group.items():
                                if cond_type not in VALID_WHEN_CONDITIONS:
                                    errors.append(ValidationError(
                                        f"{rid} 'when' group #{group_idx} has unknown condition type: '{cond_type}'"
                                    ))
                                else:
                                    # Validate value types by condition type
                                    if cond_type == "amount_range":
                                        if not isinstance(cond_value, list) or len(cond_value) != 2:
                                            errors.append(ValidationError(
                                                f"{rid} 'when' group #{group_idx}.{cond_type} must be [min, max]"
                                            ))
                                        else:
                                            try:
                                                Decimal(cond_value[0])
                                                Decimal(cond_value[1])
                                            except:
                                                errors.append(ValidationError(
                                                    f"{rid} 'when' group #{group_idx}.{cond_type} values must be numbers"
                                                ))

                                    elif cond_type == "amount_exact":
                                        try:
                                            Decimal(cond_value)
                                        except:
                                            errors.append(ValidationError(
                                                f"{rid} 'when' group #{group_idx}.{cond_type} must be a number"
                                            ))

                                    elif cond_type == "line_numbers":
                                        if not isinstance(cond_value, list):
                                            errors.append(ValidationError(
                                                f"{rid} 'when' group #{group_idx}.{cond_type} must be a list of integers"
                                            ))
                                        else:
                                            for ln in cond_value:
                                                if not isinstance(ln, int):
                                                    errors.append(ValidationError(
                                                        f"{rid} 'when' group #{group_idx}.{cond_type} must contain integers"
                                                    ))

                                    elif cond_type == "tax_year":
                                        if not isinstance(cond_value, str):
                                            errors.append(ValidationError(
                                                f"{rid} 'when' group #{group_idx}.{cond_type} must be a string like '2023-2024'"
                                            ))
                                        elif "-" not in cond_value:
                                            errors.append(ValidationError(
                                                f"{rid} 'when' group #{group_idx}.{cond_type} must be in format 'YYYY-YYYY'"
                                            ))

                                    elif cond_type == "date_range":
                                        if not isinstance(cond_value, list) or len(cond_value) != 2:
                                            errors.append(ValidationError(
                                                f"{rid} 'when' group #{group_idx}.{cond_type} must be [start, end]"
                                            ))

    # ------------------------------------------------------------------
    # 8. Cross-reference: categories.default_facets must exist
    # ------------------------------------------------------------------
    if "categories" in raw and "facets" in raw:
        for cat_id, cat_data in raw["categories"].items():
            if "default_facets" in cat_data:
                for facet in cat_data["default_facets"]:
                    if facet not in facet_definition_codes:
                        errors.append(ValidationError(
                            f"Category '{cat_id}'.default_facets references unknown facet: '{facet}'"
                        ))

    return errors, warnings


def validate_match(match_data, rule_id, errors):
    """
    Validate the 'match' block.

    Args:
        match_data: The parsed YAML data from the match key.
        rule_id (str): The rule identifier (for error messages).
        errors (list): List to append ValidationError objects.
    """
    if isinstance(match_data, str):
        if not match_data.strip():
            errors.append(ValidationError(f"{rule_id} 'match' cannot be an empty string"))
        return

    if isinstance(match_data, dict):
        # Single condition: must have exactly one key
        if len(match_data) != 1:
            errors.append(ValidationError(
                f"{rule_id} 'match' dictionary must have exactly one key (got {len(match_data)})"
            ))
            return

        cond_type, cond_value = next(iter(match_data.items()))
        if cond_type not in ALLOWED_MATCH_TYPES:
            errors.append(ValidationError(
                f"{rule_id} 'match' has unknown condition type: '{cond_type}' (expected 'description' or 'prefix')"
            ))
        elif not isinstance(cond_value, str) or not cond_value.strip():
            errors.append(ValidationError(
                f"{rule_id} 'match' value for '{cond_type}' must be a non-empty string"
            ))
        return

    if isinstance(match_data, list):
        for cond_idx, cond in enumerate(match_data, start=1):
            if not isinstance(cond, dict):
                errors.append(ValidationError(
                    f"{rule_id} 'match' condition #{cond_idx} must be a dictionary"
                ))
                continue

            if len(cond) != 1:
                errors.append(ValidationError(
                    f"{rule_id} 'match' condition #{cond_idx} must have exactly one key (got {len(cond)})"
                ))
                continue

            cond_type, cond_value = next(iter(cond.items()))
            if cond_type not in ALLOWED_MATCH_TYPES:
                errors.append(ValidationError(
                    f"{rule_id} 'match' condition #{cond_idx} has unknown type: '{cond_type}'"
                ))
            elif not isinstance(cond_value, str) or not cond_value.strip():
                errors.append(ValidationError(
                    f"{rule_id} 'match' condition #{cond_idx} value must be a non-empty string"
                ))
        return

    errors.append(ValidationError(
        f"{rule_id} 'match' must be a string, dict, or list (got {type(match_data).__name__})"
    ))


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------

def main():
    """Parse arguments and run the validator."""
    parser = argparse.ArgumentParser(
        description="Strictly validate control.yaml for financial-statement-analyser.py"
    )
    parser.add_argument(
        "control_file",
        help="Path to control.yaml file to validate"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress warnings (only show errors)"
    )
    args = parser.parse_args()

    errors, warnings = validate_control_file(args.control_file)

    # Print errors
    if errors:
        print(f"\nERROR: Found {len(errors)} error(s):")
        for err in errors:
            print(f"  {err}")
        print()

    # Print warnings (unless quiet)
    if warnings and not args.quiet:
        print(f"\nWARNING: Found {len(warnings)} warning(s):")
        for warn in warnings:
            print(f"  {warn}")
        print()

    # Exit with appropriate status
    if errors:
        print(f"Validation failed: {len(errors)} error(s).")
        sys.exit(1)

    if warnings and not args.quiet:
        print(f"Validation passed with {len(warnings)} warning(s).")
        sys.exit(0)

    print("Validation passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()