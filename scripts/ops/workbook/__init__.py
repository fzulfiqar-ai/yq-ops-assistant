"""The workbook engine behind /yq-month and /yq-week.

    model.py        the period (a month, or a Sunday-to-Saturday week) and the report's data, tidied, with its lists
    categories.py   which Categories row an item sits in (the Sep 2026 Notes rule)
    formula.py      a small spreadsheet-formula evaluator: every formula is written with its value
    book.py         cells, formats, merges, rules, charts and page setup; writes once with XlsxWriter
    style.py        the formats and chart style of the Sep 2026 workbook
    sheets_core.py  the eight Sep sheets: Dashboard, Salesmen, Categories, Products, Customers, Daily,
                    Data, Notes
    sheets_extra.py the Stock and Movements sheets the weekly and the month-end workbook share, totals only
    sheets_week.py  the eleven sheets of the weekly workbook, every tile in four windows
    golden.py       compares a built workbook with a reference file, cell by cell and part by part
    build.py        `python -m scripts.ops report month [YYYY-MM]`: reads v_sales (read-only), checks
                    that the month is whole, builds, writes under exports/reports/monthly/; hands
                    `report week` to week.py
    week.py         `python -m scripts.ops report week [YYYY-MM-DD]`: the week the brief calls the last full
                    one by default, built only when complete, written under exports/reports/weekly/

Nothing here writes to the database; the only files written are the workbooks (never over an existing one)
and the small records of their builds.
"""
