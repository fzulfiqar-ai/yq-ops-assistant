"""The look of the report: the formats of the Sep 2026 workbook, one name each, and the chart style.

FORMATS reproduces that file's cell formats exactly (font, size, colour, borders, alignment and
number format; Arial 10 underneath): 0 to 2 decimals, as his file has them. New sheets (stock,
movements, KPI) use three decimals. A sheet builder names a format; book.py turns the name into an
XlsxWriter format once.
"""
from __future__ import annotations

DEFAULT_FONT = {"font_name": "Arial", "font_size": 10}

# colours of the report
INK, GREY, MUTED, LINE, HAIR, BLUE, PALE = "#0B0B0B", "#52514E", "#898781", "#C3C2B7", "#E1E0D9", "#184F95", "#86B6EF"
BAR = "#CDE2FB"                      # data bars
GREEN, RED = "#006300", "#D03B3B"    # a rise, a fall (conditional formats)

FORMATS: dict[str, dict] = {
    'band': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bg_color': '#184F95', 'valign': 'vcenter'},
    'company': {'bold': True, 'font_size': 8.5, 'font_color': '#52514E', 'valign': 'vcenter'},
    'meta_r': {'font_size': 8.5, 'font_color': '#52514E', 'align': 'right', 'valign': 'vcenter'},
    'report_title': {'bold': True, 'font_size': 18, 'font_color': '#0B0B0B', 'valign': 'vcenter'},
    'report_sub': {'font_size': 10.5, 'font_color': '#52514E', 'valign': 'vcenter'},
    'rule_medium': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 2, 'bottom_color': '#0B0B0B', 'valign': 'vcenter'},
    'note': {'font_size': 8.5, 'font_color': '#52514E', 'valign': 'vcenter'},
    'tile_label': {'font_size': 8.5, 'font_color': '#52514E', 'left': 1, 'left_color': '#C3C2B7', 'align': 'left', 'valign': 'vcenter', 'indent': 1},
    'gutter': {'font_size': 9.5, 'font_color': '#0B0B0B', 'valign': 'vcenter'},
    'tile_money': {'bold': True, 'font_size': 32, 'font_color': '#0B0B0B', 'align': 'left', 'num_format': '#,##0'},
    'tile_int': {'bold': True, 'font_size': 20, 'font_color': '#0B0B0B', 'left': 1, 'left_color': '#C3C2B7', 'align': 'left', 'indent': 1, 'num_format': '#,##0'},
    'tile_1dp': {'bold': True, 'font_size': 20, 'font_color': '#0B0B0B', 'left': 1, 'left_color': '#C3C2B7', 'align': 'left', 'indent': 1, 'num_format': '0.0'},
    'tile_2dp': {'bold': True, 'font_size': 20, 'font_color': '#0B0B0B', 'left': 1, 'left_color': '#C3C2B7', 'align': 'left', 'indent': 1, 'num_format': '0.00'},
    'tile_change_first': {'bold': True, 'font_size': 9, 'font_color': '#0B0B0B', 'align': 'left', 'valign': 'vcenter', 'num_format': '"▲ "#,##0.0%;"▼ "#,##0.0%;"–"'},
    'tile_from_int': {'font_size': 8.5, 'font_color': '#52514E', 'align': 'left', 'valign': 'vcenter', 'num_format': '"from "#,##0'},
    'tile_change': {'bold': True, 'font_size': 9, 'font_color': '#0B0B0B', 'left': 1, 'left_color': '#C3C2B7', 'align': 'left', 'valign': 'vcenter', 'indent': 1, 'num_format': '"▲ "#,##0.0%;"▼ "#,##0.0%;"–"'},
    'tile_from_1dp': {'font_size': 8.5, 'font_color': '#52514E', 'align': 'left', 'valign': 'vcenter', 'num_format': '"from "0.0'},
    'tile_from_2dp': {'font_size': 8.5, 'font_color': '#52514E', 'align': 'left', 'valign': 'vcenter', 'num_format': '"from "0.00'},
    'tile_foot': {'font_size': 9.5, 'font_color': '#0B0B0B', 'left': 1, 'left_color': '#C3C2B7', 'align': 'left', 'valign': 'vcenter', 'indent': 1},
    'rule_thin': {'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#C3C2B7', 'valign': 'vcenter'},
    'section': {'bold': True, 'font_size': 11, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#0B0B0B', 'valign': 'vcenter'},
    'comment': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'text_wrap': True},
    'mini_th': {'bold': True, 'font_size': 8.5, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'valign': 'vcenter'},
    'mini_th_r': {'bold': True, 'font_size': 8.5, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'align': 'right', 'valign': 'vcenter'},
    'mini_td': {'font_size': 9, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter'},
    'mini_int': {'font_size': 9, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0;-#,##0;"–"'},
    'mini_change': {'font_size': 9, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter', 'num_format': '+#,##0;-#,##0;"–"'},
    'mini_total': {'bold': True, 'font_size': 9, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter'},
    'mini_total_int': {'bold': True, 'font_size': 9, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '#,##0;-#,##0;"–"'},
    'mini_total_change': {'bold': True, 'font_size': 9, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'align': 'right', 'valign': 'vcenter', 'num_format': '+#,##0;-#,##0;"–"'},
    'mini_note': {'font_size': 8, 'font_color': '#52514E', 'valign': 'top', 'text_wrap': True},
    'mini_pct': {'font_size': 9, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '0.0%;-0.0%;"–"'},
    'mini_total_pct': {'bold': True, 'font_size': 9, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '0.0%;-0.0%;"–"'},
    'footnote': {'font_size': 8, 'font_color': '#52514E', 'valign': 'vcenter'},
    'sheet_title': {'bold': True, 'font_size': 14, 'font_color': '#0B0B0B', 'valign': 'vcenter'},
    'sheet_sub': {'font_size': 9.5, 'font_color': '#52514E', 'valign': 'vcenter'},
    'group_head': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#C3C2B7', 'align': 'center', 'valign': 'vcenter'},
    'th': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'text_wrap': True},
    'th_r': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'align': 'right', 'text_wrap': True},
    'td': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter'},
    'money': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0.00;-#,##0.00;"–"'},
    'money_change': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '+#,##0.00;-#,##0.00;"–"'},
    'change_pct': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter', 'num_format': '"▲ "#,##0.0%;"▼ "#,##0.0%;"–"'},
    'share': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '0.0%;-0.0%;"–"'},
    'int': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0;-#,##0;"–"'},
    'money_1dp': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0.0;-#,##0.0;"–"'},
    'total_label': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter'},
    'total_money': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '#,##0.00;-#,##0.00;"–"'},
    'total_money_change': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '+#,##0.00;-#,##0.00;"–"'},
    'total_change_pct': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'align': 'right', 'valign': 'vcenter', 'num_format': '"▲ "#,##0.0%;"▼ "#,##0.0%;"–"'},
    'total_share': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '0.0%;-0.0%;"–"'},
    'total_int': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '#,##0;-#,##0;"–"'},
    'total_1dp': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'top': 1, 'top_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '#,##0.0;-#,##0.0;"–"'},
    'subhead': {'bold': True, 'font_size': 11, 'font_color': '#0B0B0B', 'valign': 'vcenter'},
    'th_date': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'align': 'right', 'num_format': 'd mmm'},
    'money_1dp_bold': {'bold': True, 'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0.0;-#,##0.0;"–"'},
    'ref_group_head': {'bold': True, 'font_size': 9, 'font_color': '#898781', 'bottom': 1, 'bottom_color': '#C3C2B7', 'align': 'center', 'valign': 'vcenter'},
    'ref_th': {'bold': True, 'font_size': 9, 'font_color': '#898781', 'bottom': 1, 'bottom_color': '#0B0B0B', 'text_wrap': True, 'indent': 2, 'valign': 'bottom'},
    'ref_th_r': {'bold': True, 'font_size': 9, 'font_color': '#898781', 'bottom': 1, 'bottom_color': '#0B0B0B', 'align': 'right', 'text_wrap': True},
    'ref_td': {'font_size': 8.5, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'left', 'valign': 'vcenter', 'indent': 2},
    'ref_int': {'font_size': 8.5, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': 1},
    'subhead_small': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'valign': 'vcenter'},
    'th_indent3': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'text_wrap': True, 'indent': 3, 'valign': 'bottom'},
    'status': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'left', 'valign': 'vcenter', 'indent': 3},
    'rank': {'font_size': 8.5, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter', 'num_format': 1},
    'day': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'left', 'valign': 'vcenter', 'num_format': 1},
    'money_plain': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0.00'},
    'data_th_wh': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#0B0B0B', 'text_wrap': True, 'indent': 2, 'valign': 'bottom'},
    'data_th_calc': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bg_color': '#EEF3FA', 'bottom': 1, 'bottom_color': '#0B0B0B', 'text_wrap': True},
    'data_th_calc_r': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bg_color': '#EEF3FA', 'bottom': 1, 'bottom_color': '#0B0B0B', 'align': 'right', 'text_wrap': True},
    'data_th_calc_indent': {'bold': True, 'font_size': 9, 'font_color': '#52514E', 'bg_color': '#EEF3FA', 'bottom': 1, 'bottom_color': '#0B0B0B', 'text_wrap': True, 'indent': 2, 'valign': 'bottom'},
    'data_date': {'font_size': 9, 'font_color': '#0B0B0B', 'align': 'left', 'valign': 'vcenter', 'num_format': 'dd-mmm-yy'},
    'data_text': {'font_size': 9, 'font_color': '#0B0B0B', 'valign': 'vcenter'},
    'data_int': {'font_size': 9, 'font_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': 1},
    'data_money': {'font_size': 9, 'font_color': '#0B0B0B', 'valign': 'vcenter', 'num_format': '#,##0.00'},
    'data_indent': {'font_size': 9, 'font_color': '#0B0B0B', 'align': 'left', 'valign': 'vcenter', 'indent': 2},
    'data_month': {'font_size': 9, 'font_color': '#0B0B0B', 'align': 'left', 'valign': 'vcenter', 'num_format': 'mmm yyyy'},
    'input_date': {'font_size': 9.5, 'font_color': '#0000FF', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter', 'num_format': 'd mmm yyyy'},
    'help': {'font_size': 8.5, 'font_color': '#52514E', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'left', 'valign': 'vcenter', 'indent': 1},
    'calc_date': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter', 'num_format': 'd mmm yyyy'},
    'input_text': {'font_size': 9.5, 'font_color': '#0000FF', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter'},
    'para': {'font_size': 9.5, 'font_color': '#0B0B0B', 'valign': 'top', 'text_wrap': True},
    'input_int': {'font_size': 9.5, 'font_color': '#0000FF', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0;-#,##0;"–"'},
    'diff_int': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0;-#,##0;0'},
    'input_money': {'font_size': 9.5, 'font_color': '#0000FF', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0.00;-#,##0.00;"–"'},
    'diff_money': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '#,##0.00;-#,##0.00;0.00'},
    'pct_plain': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': '0.0%'},
    'int_plain': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'valign': 'vcenter', 'num_format': 1},
    'calc_text': {'font_size': 9.5, 'font_color': '#0B0B0B', 'bottom': 1, 'bottom_color': '#E1E0D9', 'align': 'right', 'valign': 'vcenter'},
}

# Conditional-format font colours (dxf): a value of "n/a" in grey, a rise in green, a fall in red.
DXF = {"na": {"font_color": GREY}, "up": {"font_color": GREEN}, "down": {"font_color": RED}}


def props(name: str) -> dict:
    if name not in FORMATS:
        raise KeyError(f"no format named {name!r} in scripts/ops/workbook/style.py")
    return dict(FORMATS[name])


def dxf(name: str) -> dict:
    return dict(DXF[name])


def change_rules(with_na: bool = True) -> list[dict]:
    """The three rules every change column carries: "n/a" grey, above nought green, below red."""
    rules = [{"type": "cell", "criteria": "equal to", "value": '"n/a"', "format": "na"}] if with_na else []
    return rules + [{"type": "cell", "criteria": "greater than", "value": 0, "format": "up"},
                    {"type": "cell", "criteria": "less than", "value": 0, "format": "down"}]


DATA_BAR = {"type": "data_bar", "bar_color": BAR, "bar_solid": True, "bar_no_border": True, "bar_direction": "left",
            "min_type": "num", "min_value": 0, "max_type": "max"}
COLOR_SCALE = {"type": "2_color_scale", "min_type": "num", "min_value": 0, "min_color": "#FFFFFF",
               "max_type": "max", "max_color": PALE}
SPARKLINE = {"type": "column", "series_color": PALE, "min": 0}


def font(size: float, color: str = GREY, bold: bool | None = None) -> dict:
    f = {"name": "Arial", "size": size, "color": color}
    if bold is not None:
        f["bold"] = bold
    return f


def build_chart(wb, spec: dict):
    """An XlsxWriter chart from a plain spec (type, series, axes, legend, areas, size)."""
    opts = {"type": spec["type"]}
    if spec.get("subtype"):
        opts["subtype"] = spec["subtype"]
    chart = wb.add_chart(opts)
    for s in spec["series"]:
        chart.add_series(dict(s))
    if "x_axis" in spec:
        chart.set_x_axis(dict(spec["x_axis"]))
    if "y_axis" in spec:
        chart.set_y_axis(dict(spec["y_axis"]))
    chart.set_legend(dict(spec.get("legend") or {"position": "top", "font": font(9)}))
    chart.set_title({"none": True})
    chart.set_plotarea(dict(spec.get("plotarea") or {"border": {"none": True}, "fill": {"none": True}}))
    chart.set_chartarea(dict(spec.get("chartarea") or {"border": {"none": True}, "fill": {"color": "#FFFFFF"}}))
    if spec.get("size"):
        chart.set_size(dict(spec["size"]))
    if spec.get("gap") is not None:
        chart.gap = spec["gap"]
    return chart
