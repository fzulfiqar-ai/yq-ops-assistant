"""The workbook engine behind /yq-month (and, in a later phase, /yq-week).

    model.py       the period (month; week later) and the report's data, tidied, with its lists
    categories.py  which Categories row an item sits in (the Sep 2026 Notes rule)
    formula.py     a small spreadsheet-formula evaluator: every formula is written with its value
    book.py        cells, formats, merges, rules, charts and page setup; writes once with XlsxWriter
    style.py       the formats and chart style of the Sep 2026 workbook
    sheets_core.py the eight Sep sheets: Dashboard, Salesmen, Categories, Products, Customers, Daily,
                   Data, Notes
    golden.py      compares a built workbook with a reference file, cell by cell and part by part
    build.py       `python -m scripts.ops report month [YYYY-MM]`: reads v_sales (read-only), checks
                   that the month is whole, builds, writes under exports/reports/monthly/

Nothing here writes to the database; the only file written is the workbook (never over an existing one).
"""
