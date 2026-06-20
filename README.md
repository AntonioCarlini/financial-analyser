# Financial Statement Analyser

Analyse (some) UK bank statements and (some) credit card statements

## Purpose

This tool helps me:
- Categorise transactions financial statements.
- Produce monthly summaries of income and expenditure.
- Split transactions between multiple owners for shared finances.

## Scripts

### `financial-statement-analyser.py`
The main analysis script. Processes CSV statements against a YAML control file and produces reports.

### `financial-statement-analyser-control-file-checker.py`
Strict YAML validator for `control.yaml`. Catches structural errors before analysis.
