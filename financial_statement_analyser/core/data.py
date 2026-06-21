import os
import yaml

def load_data_file(filename):
    """
    Load the YAML data file and resolve relative paths.
    Returns a tuple: (control_file_path, list_of_tax_years)
    where each tax_year is a dict: {'year': '...', 'statements': [...]}
    """
    base_dir = os.path.dirname(os.path.abspath(filename))

    with open(filename, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    control_file = raw.get('control_file')
    if control_file:
        # Resolve relative to data file's directory
        control_file = os.path.join(base_dir, control_file)
    else:
        raise ValueError("data file must contain 'control_file' key")

    extra_info_file = raw.get('extra_info_file')
    if extra_info_file:
        extra_info_file = os.path.join(base_dir, extra_info_file)

    tax_years = raw.get('tax_years', [])
    if not tax_years:
        raise ValueError("data file must contain at least one tax_year")

    # Resolve statement file paths
    for ty in tax_years:
        for stmt in ty.get('statements', []):
            stmt['file'] = os.path.join(base_dir, stmt['file'])

    return control_file, tax_years, extra_info_file

def list_data_file_info(tax_years, filter_years=None):
    """
    Print the tax years and statements that would be processed.
    If filter_years is a list, only show those years.
    """
    if filter_years:
        filtered = [ty for ty in tax_years if ty['year'] in filter_years]
    else:
        filtered = tax_years

    if not filtered:
        print("No tax years match the filter.")
        return

    print()
    print("============================================================")
    print("DATA FILE SUMMARY")
    print("============================================================")
    print()

    for ty in filtered:
        print(f"Tax Year: {ty['year']}")
        if 'description' in ty:
            print(f"  Description: {ty['description']}")
        print(f"  Statements:")
        for stmt in ty.get('statements', []):
            print(f"    - Type: {stmt['type']:15} File: {stmt['file']}")
        print()
