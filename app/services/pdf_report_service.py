"""
Fiscal Year PDF Report Generator
Generates a visually appealing, multi-page PDF report with charts and tables.
Reports included:
  - Overall Sales Summary
  - Top Items & Gross Profit
  - Top 5 & Least 5 Performing Items
  - Accounts Receivable
  - Accounts Payable
  - Regional Comparison
  - Expense Breakdown
  - Journal Report
  - Strategic Insights & Recommendations
"""

import io
import logging
import os
import tempfile
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ─── Brand Colors ─────────────────────────────────────────────────
NAVY = colors.HexColor("#1B2A4A")
DARK_BLUE = colors.HexColor("#2C3E6B")
ACCENT_BLUE = colors.HexColor("#3B82F6")
LIGHT_BLUE = colors.HexColor("#DBEAFE")
ACCENT_GREEN = colors.HexColor("#10B981")
LIGHT_GREEN = colors.HexColor("#D1FAE5")
ACCENT_RED = colors.HexColor("#EF4444")
LIGHT_RED = colors.HexColor("#FEE2E2")
ACCENT_AMBER = colors.HexColor("#F59E0B")
LIGHT_AMBER = colors.HexColor("#FEF3C7")
ACCENT_PURPLE = colors.HexColor("#8B5CF6")
LIGHT_PURPLE = colors.HexColor("#EDE9FE")
DARK_GRAY = colors.HexColor("#374151")
MED_GRAY = colors.HexColor("#6B7280")
LIGHT_GRAY = colors.HexColor("#F3F4F6")
WHITE = colors.HexColor("#FFFFFF")
BORDER_GRAY = colors.HexColor("#E5E7EB")

# Chart color palette
CHART_COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
                "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1"]


def _get_styles():
    """Build custom paragraph styles for the report."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "CoverTitle", parent=styles["Title"],
        fontSize=36, leading=44, textColor=WHITE,
        alignment=TA_CENTER, fontName="Helvetica-Bold",
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        "CoverSubtitle", parent=styles["Normal"],
        fontSize=16, leading=22, textColor=colors.HexColor("#93C5FD"),
        alignment=TA_CENTER, fontName="Helvetica",
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "SectionTitle", parent=styles["Heading1"],
        fontSize=22, leading=28, textColor=NAVY,
        fontName="Helvetica-Bold", spaceAfter=16, spaceBefore=8,
        borderWidth=0, borderPadding=0,
    ))
    styles.add(ParagraphStyle(
        "SubSectionTitle", parent=styles["Heading2"],
        fontSize=14, leading=18, textColor=DARK_BLUE,
        fontName="Helvetica-Bold", spaceAfter=10, spaceBefore=6,
    ))
    styles.add(ParagraphStyle(
        "BodyText2", parent=styles["Normal"],
        fontSize=10, leading=14, textColor=DARK_GRAY,
        fontName="Helvetica", spaceAfter=6,
        alignment=TA_JUSTIFY,
    ))
    styles.add(ParagraphStyle(
        "SmallGray", parent=styles["Normal"],
        fontSize=8, leading=10, textColor=MED_GRAY,
        fontName="Helvetica",
    ))
    styles.add(ParagraphStyle(
        "TableHeader", parent=styles["Normal"],
        fontSize=9, leading=12, textColor=WHITE,
        fontName="Helvetica-Bold", alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "TableCell", parent=styles["Normal"],
        fontSize=9, leading=12, textColor=DARK_GRAY,
        fontName="Helvetica",
    ))
    styles.add(ParagraphStyle(
        "TableCellRight", parent=styles["Normal"],
        fontSize=9, leading=12, textColor=DARK_GRAY,
        fontName="Helvetica", alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "KPIValue", parent=styles["Normal"],
        fontSize=24, leading=30, textColor=NAVY,
        fontName="Helvetica-Bold", alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "KPILabel", parent=styles["Normal"],
        fontSize=9, leading=12, textColor=MED_GRAY,
        fontName="Helvetica", alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "InsightBullet", parent=styles["Normal"],
        fontSize=10, leading=15, textColor=DARK_GRAY,
        fontName="Helvetica", spaceAfter=6,
        leftIndent=20, bulletIndent=8,
        bulletFontName="Helvetica-Bold", bulletFontSize=10,
        bulletColor=ACCENT_BLUE,
    ))
    styles.add(ParagraphStyle(
        "FooterStyle", parent=styles["Normal"],
        fontSize=8, leading=10, textColor=MED_GRAY,
        fontName="Helvetica", alignment=TA_CENTER,
    ))
    return styles


def _fmt_currency(value, symbol="$"):
    """Format a number as currency."""
    if value is None:
        return f"{symbol}0.00"
    try:
        v = float(value)
        if abs(v) >= 1_000_000:
            return f"{symbol}{v/1_000_000:,.2f}M"
        if abs(v) >= 1_000:
            return f"{symbol}{v/1_000:,.1f}K"
        return f"{symbol}{v:,.2f}"
    except (ValueError, TypeError):
        return f"{symbol}0.00"


def _fmt_number(value):
    """Format a number with comma separators."""
    try:
        return f"{float(value):,.0f}"
    except (ValueError, TypeError):
        return "0"


def _fmt_pct(value):
    """Format as percentage."""
    try:
        return f"{float(value):.1f}%"
    except (ValueError, TypeError):
        return "0.0%"


def _create_chart_image(fig, dpi=150):
    """Convert matplotlib figure to a ReportLab Image flowable."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def _style_chart(ax, title="", bg_color="#FAFBFC"):
    """Apply consistent styling to chart axes."""
    ax.set_facecolor(bg_color)
    ax.figure.set_facecolor("#FFFFFF")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#E5E7EB")
    ax.spines["bottom"].set_color("#E5E7EB")
    ax.tick_params(colors="#6B7280", labelsize=8)
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", color="#1B2A4A",
                      pad=12, loc="left")


def _make_kpi_card(value_text, label_text, accent_color, styles):
    """Create a KPI card as a styled table."""
    card_data = [
        [Paragraph(value_text, styles["KPIValue"])],
        [Paragraph(label_text, styles["KPILabel"])],
    ]
    card = Table(card_data, colWidths=[130])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("BOX", (0, 0), (-1, -1), 1.5, accent_color),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, -1), (0, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEABOVE", (0, 0), (-1, 0), 3, accent_color),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return card


def _make_data_table(headers, rows, col_widths=None, accent=ACCENT_BLUE):
    """Create a styled data table."""
    styles = _get_styles()
    header_cells = [Paragraph(h, styles["TableHeader"]) for h in headers]
    table_data = [header_cells]
    for row in rows:
        table_data.append([Paragraph(str(c), styles["TableCell"]) for c in row])

    if not col_widths:
        col_widths = [None] * len(headers)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
    ]
    t.setStyle(TableStyle(style_cmds))
    return t


# ═══════════════════════════════════════════════════════════════════
# Page decorators
# ═══════════════════════════════════════════════════════════════════

def _cover_page_bg(canvas, doc):
    """Draw gradient-like cover page background."""
    w, h = A4
    # Dark navy gradient background
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    # Accent stripe
    canvas.setFillColor(ACCENT_BLUE)
    canvas.rect(0, h * 0.38, w, 4, fill=1, stroke=0)
    # Subtle circles for visual interest
    canvas.setFillColor(colors.HexColor("#2C3E6B"))
    canvas.circle(w * 0.85, h * 0.85, 80, fill=1, stroke=0)
    canvas.circle(w * 0.1, h * 0.15, 50, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#344C7C"))
    canvas.circle(w * 0.75, h * 0.2, 35, fill=1, stroke=0)


def _body_page_bg(canvas, doc):
    """Draw body page header/footer decorations."""
    w, h = A4
    # Top header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 28, w, 28, fill=1, stroke=0)
    # Thin accent line under header
    canvas.setFillColor(ACCENT_BLUE)
    canvas.rect(0, h - 30, w, 2, fill=1, stroke=0)
    # Footer line
    canvas.setStrokeColor(BORDER_GRAY)
    canvas.setLineWidth(0.5)
    canvas.line(40, 35, w - 40, 35)
    # Page number
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MED_GRAY)
    canvas.drawCentredString(w / 2, 22, f"Page {doc.page}")
    # Report title in header
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(WHITE)
    canvas.drawString(40, h - 20, "Fiscal Year Financial Report")
    # Date in header
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(w - 40, h - 20, datetime.now().strftime("%B %d, %Y"))


# ═══════════════════════════════════════════════════════════════════
# Section Builders
# ═══════════════════════════════════════════════════════════════════

def _build_cover_page(story, data, styles):
    """Build the cover page."""
    story.append(Spacer(1, 140))
    story.append(Paragraph("FISCAL YEAR", styles["CoverSubtitle"]))
    story.append(Paragraph("Financial Report", styles["CoverTitle"]))

    fy = data.get("fiscal_year", "2025-2026")
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"{fy}", ParagraphStyle(
        "FYLabel", parent=styles["CoverSubtitle"], fontSize=20,
        textColor=ACCENT_BLUE,
    )))
    story.append(Spacer(1, 30))

    org = data.get("organization_name", "Your Organization")
    story.append(Paragraph(org, ParagraphStyle(
        "OrgName", parent=styles["CoverSubtitle"], fontSize=13,
        textColor=colors.HexColor("#CBD5E1"),
    )))

    gen_date = datetime.now().strftime("%B %d, %Y")
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Generated on {gen_date}", ParagraphStyle(
        "GenDate", parent=styles["CoverSubtitle"], fontSize=10,
        textColor=colors.HexColor("#94A3B8"),
    )))

    story.append(NextPageTemplate("body"))
    story.append(PageBreak())


def _build_executive_summary(story, data, styles):
    """KPI cards row at the top of the report."""
    story.append(Paragraph("Executive Summary", styles["SectionTitle"]))
    story.append(Spacer(1, 4))

    total_sales = _fmt_currency(data.get("total_sales", 0))
    gross_profit = _fmt_currency(data.get("gross_profit", 0))
    total_expenses = _fmt_currency(data.get("total_expenses", 0))
    net_income = _fmt_currency(data.get("net_income", 0))

    cards = [
        _make_kpi_card(total_sales, "Total Sales", ACCENT_BLUE, styles),
        _make_kpi_card(gross_profit, "Gross Profit", ACCENT_GREEN, styles),
        _make_kpi_card(total_expenses, "Total Expenses", ACCENT_AMBER, styles),
        _make_kpi_card(net_income, "Net Income", ACCENT_PURPLE, styles),
    ]
    row = Table([cards], colWidths=[135, 135, 135, 135])
    row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(row)
    story.append(Spacer(1, 20))


def _build_overall_sales(story, data, styles):
    """Overall Sales section with monthly chart."""
    story.append(Paragraph("Overall Sales", styles["SectionTitle"]))

    sales_summary = data.get("sales_summary", {})
    if sales_summary:
        desc = sales_summary.get("description", "")
        if desc:
            story.append(Paragraph(desc, styles["BodyText2"]))
            story.append(Spacer(1, 8))

    # Monthly sales chart
    monthly = data.get("monthly_sales", [])
    if monthly:
        months = [m.get("month", "") for m in monthly]
        amounts = [float(m.get("amount", 0)) for m in monthly]

        # Only render chart if there's actual data
        if any(a > 0 for a in amounts):
            fig, ax = plt.subplots(figsize=(7, 3))
            _style_chart(ax, "Monthly Sales Trend")

            max_amt = max(amounts) if amounts else 0
            # Use appropriate scale for large values
            if max_amt >= 1_000_000:
                scale = 1_000_000
                scale_label = "(in Millions $)"
                display_amounts = [a / scale for a in amounts]
            elif max_amt >= 1_000:
                scale = 1_000
                scale_label = "(in Thousands $)"
                display_amounts = [a / scale for a in amounts]
            else:
                scale = 1
                scale_label = "($)"
                display_amounts = amounts

            bars = ax.bar(months, display_amounts, color=CHART_COLORS[0], width=0.6,
                          edgecolor="white", linewidth=0.5, zorder=3)
            # Add value labels on bars
            max_disp = max(display_amounts) if display_amounts else 0
            for bar, val in zip(bars, amounts):
                label = _fmt_currency(val)
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max_disp*0.02,
                        label, ha="center", va="bottom", fontsize=7,
                        color="#374151", fontweight="bold")
            ax.set_ylabel(scale_label, fontsize=8, color="#6B7280")
            ax.set_ylim(0, max_disp * 1.25 if max_disp > 0 else 1)
            ax.grid(axis="y", alpha=0.3, linestyle="--", color="#D1D5DB")
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()

            buf = _create_chart_image(fig)
            story.append(Image(buf, width=480, height=200))
            story.append(Spacer(1, 12))
        else:
            story.append(Paragraph(
                "<i>No monthly sales data available for this fiscal year period.</i>",
                styles["BodyText2"]))
            story.append(Spacer(1, 12))

    # Sales breakdown table
    sales_data = data.get("sales_breakdown", [])
    if sales_data:
        headers = ["Category", "Amount", "% of Total", "Invoices"]
        rows = []
        for s in sales_data:
            rows.append([
                s.get("category", ""),
                _fmt_currency(s.get("amount", 0)),
                _fmt_pct(s.get("percentage", 0)),
                _fmt_number(s.get("invoice_count", 0)),
            ])
        story.append(_make_data_table(headers, rows, [180, 100, 80, 80]))
        story.append(Spacer(1, 16))


def _build_top_item(story, data, styles):
    """Top selling item highlight."""
    story.append(Paragraph("Top Selling Item", styles["SectionTitle"]))
    top = data.get("top_item", {})
    if top:
        name = top.get("name", "N/A")
        revenue = _fmt_currency(top.get("revenue", 0))
        qty = _fmt_number(top.get("quantity_sold", 0))
        margin = _fmt_pct(top.get("margin", 0))

        card_data = [
            [Paragraph(f"<b>{name}</b>", ParagraphStyle(
                "TopItemName", fontSize=16, leading=20, textColor=NAVY,
                fontName="Helvetica-Bold", alignment=TA_CENTER,
            ))],
            [Paragraph(f"Revenue: {revenue}  |  Units Sold: {qty}  |  Margin: {margin}",
                       ParagraphStyle("TopItemDetails", fontSize=10, leading=14,
                                      textColor=MED_GRAY, alignment=TA_CENTER))],
        ]
        card = Table(card_data, colWidths=[480])
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
            ("BOX", (0, 0), (-1, -1), 1.5, ACCENT_BLUE),
            ("TOPPADDING", (0, 0), (0, 0), 16),
            ("BOTTOMPADDING", (0, -1), (0, -1), 16),
            ("LEFTPADDING", (0, 0), (-1, -1), 16),
            ("RIGHTPADDING", (0, 0), (-1, -1), 16),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        story.append(card)
        story.append(Spacer(1, 16))


def _build_gross_profit(story, data, styles):
    """Gross Profit analysis with chart."""
    story.append(Paragraph("Gross Profit Analysis", styles["SectionTitle"]))

    gp = data.get("gross_profit_details", {})
    if gp:
        revenue = _fmt_currency(gp.get("total_revenue", 0))
        cogs = _fmt_currency(gp.get("cost_of_goods", 0))
        profit = _fmt_currency(gp.get("gross_profit", 0))
        margin = _fmt_pct(gp.get("margin_pct", 0))

        story.append(Paragraph(
            f"Total Revenue: <b>{revenue}</b> &nbsp; | &nbsp; "
            f"Cost of Goods: <b>{cogs}</b> &nbsp; | &nbsp; "
            f"Gross Profit: <b><font color='#10B981'>{profit}</font></b> &nbsp; | &nbsp; "
            f"Margin: <b>{margin}</b>",
            styles["BodyText2"],
        ))
        story.append(Spacer(1, 12))

    # Revenue vs COGS chart
    monthly_gp = data.get("monthly_gross_profit", [])
    if monthly_gp:
        months = [m.get("month", "") for m in monthly_gp]
        rev = [float(m.get("revenue", 0)) for m in monthly_gp]
        cost = [float(m.get("cost", 0)) for m in monthly_gp]

        # Only render chart if there's actual data
        if any(v > 0 for v in rev) or any(v > 0 for v in cost):
            fig, ax = plt.subplots(figsize=(7, 3))
            _style_chart(ax, "Revenue vs Cost of Goods (Monthly)")

            max_val = max(max(rev) if rev else 0, max(cost) if cost else 0)
            # Use appropriate scale for large values
            if max_val >= 1_000_000:
                scale = 1_000_000
                scale_label = "(in Millions $)"
            elif max_val >= 1_000:
                scale = 1_000
                scale_label = "(in Thousands $)"
            else:
                scale = 1
                scale_label = "($)"

            rev_scaled = [v / scale for v in rev]
            cost_scaled = [v / scale for v in cost]

            x = range(len(months))
            ax.bar([i - 0.2 for i in x], rev_scaled, 0.4, label="Revenue",
                   color=CHART_COLORS[0], edgecolor="white", zorder=3)
            ax.bar([i + 0.2 for i in x], cost_scaled, 0.4, label="COGS",
                   color=CHART_COLORS[3], edgecolor="white", zorder=3)
            ax.set_xticks(list(x))
            ax.set_xticklabels(months, rotation=45, ha="right")
            ax.set_ylabel(scale_label, fontsize=8, color="#6B7280")
            max_disp = max(max(rev_scaled) if rev_scaled else 0,
                           max(cost_scaled) if cost_scaled else 0)
            ax.set_ylim(0, max_disp * 1.25 if max_disp > 0 else 1)
            ax.legend(fontsize=8, frameon=False)
            ax.grid(axis="y", alpha=0.3, linestyle="--", color="#D1D5DB")
            plt.tight_layout()

            buf = _create_chart_image(fig)
            story.append(Image(buf, width=480, height=200))
            story.append(Spacer(1, 16))
        else:
            story.append(Paragraph(
                "<i>No revenue/cost data available for monthly gross profit chart.</i>",
                styles["BodyText2"]))
            story.append(Spacer(1, 16))


def _build_performance_items(story, data, styles):
    """Top 5 and Least 5 performing items."""
    story.append(Paragraph("Item Performance", styles["SectionTitle"]))

    # Top 5
    top5 = data.get("top_5_items", [])
    # Filter out items with zero revenue
    top5_valid = [i for i in top5 if float(i.get("revenue", 0)) > 0]
    if top5_valid:
        story.append(Paragraph("Top 5 Performing Items", styles["SubSectionTitle"]))
        headers = ["#", "Item", "Revenue", "Units Sold", "Margin"]
        rows = []
        for i, item in enumerate(top5_valid[:5], 1):
            rows.append([
                str(i),
                item.get("name", ""),
                _fmt_currency(item.get("revenue", 0)),
                _fmt_number(item.get("quantity", 0)),
                _fmt_pct(item.get("margin", 0)),
            ])
        story.append(_make_data_table(headers, rows,
                                       [30, 200, 90, 80, 80], ACCENT_GREEN))
        story.append(Spacer(1, 16))

    # Chart for top 5
    if top5_valid:
        names = [i.get("name", "")[:20] for i in top5_valid[:5]]
        revenues = [float(i.get("revenue", 0)) for i in top5_valid[:5]]

        if any(r > 0 for r in revenues):
            fig, ax = plt.subplots(figsize=(7, 2.5))
            _style_chart(ax, "Top 5 Items by Revenue")
            bars = ax.barh(names[::-1], revenues[::-1],
                           color=[CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(names))],
                           edgecolor="white", height=0.5, zorder=3)
            for bar, val in zip(bars, revenues[::-1]):
                ax.text(bar.get_width() + max(revenues)*0.02, bar.get_y() + bar.get_height()/2,
                        _fmt_currency(val), va="center", fontsize=7, color="#374151")
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: _fmt_currency(v)))
            ax.grid(axis="x", alpha=0.3, linestyle="--", color="#D1D5DB")
            plt.tight_layout()

            buf = _create_chart_image(fig)
            story.append(Image(buf, width=480, height=170))
            story.append(Spacer(1, 16))

    if not top5_valid:
        story.append(Paragraph(
            "<i>No item performance data available for this period.</i>",
            styles["BodyText2"]))
        story.append(Spacer(1, 12))

    # Least 5
    least5 = data.get("least_5_items", [])
    least5_valid = [i for i in least5 if float(i.get("revenue", 0)) > 0]
    if least5_valid:
        story.append(Paragraph("Least 5 Performing Items", styles["SubSectionTitle"]))
        headers = ["#", "Item", "Revenue", "Units Sold", "Margin"]
        rows = []
        for i, item in enumerate(least5_valid[:5], 1):
            rows.append([
                str(i),
                item.get("name", ""),
                _fmt_currency(item.get("revenue", 0)),
                _fmt_number(item.get("quantity", 0)),
                _fmt_pct(item.get("margin", 0)),
            ])
        story.append(_make_data_table(headers, rows,
                                       [30, 200, 90, 80, 80], ACCENT_RED))
        story.append(Spacer(1, 16))


def _build_accounts_receivable(story, data, styles):
    """Accounts Receivable section."""
    story.append(Paragraph("Accounts Receivable", styles["SectionTitle"]))

    ar = data.get("accounts_receivable", {})
    total = _fmt_currency(ar.get("total_outstanding", 0))
    current = _fmt_currency(ar.get("current", 0))
    overdue = _fmt_currency(ar.get("overdue", 0))

    # KPI cards
    cards = [
        _make_kpi_card(total, "Total Outstanding", ACCENT_BLUE, styles),
        _make_kpi_card(current, "Current", ACCENT_GREEN, styles),
        _make_kpi_card(overdue, "Overdue", ACCENT_RED, styles),
    ]
    # Center the 3-card row within the available page width
    card_w = 155
    gap = 10
    total_cards_w = card_w * 3 + gap * 2
    side_pad = (480 - total_cards_w) / 2
    row = Table([cards], colWidths=[card_w, card_w, card_w])
    row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), gap // 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), gap // 2),
    ]))
    # Wrap in outer table for centering
    outer = Table([[row]], colWidths=[480])
    outer.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(outer)
    story.append(Spacer(1, 12))

    # Aging breakdown chart
    aging = ar.get("aging", [])
    if aging:
        labels = [a.get("period", "") for a in aging]
        amounts = [float(a.get("amount", 0)) for a in aging]
        clrs = [CHART_COLORS[1], CHART_COLORS[0], CHART_COLORS[4],
                CHART_COLORS[3], "#991B1B"][:len(labels)]

        # Filter out zero/negative entries to avoid NaN in pie chart
        filtered = [(l, a, c) for l, a, c in zip(labels, amounts, clrs) if a > 0]
        if not filtered:
            filtered = [("No Data", 1, "#D1D5DB")]
        labels, amounts, clrs = zip(*filtered)

        fig, ax = plt.subplots(figsize=(5, 3))
        fig.set_facecolor("#FFFFFF")
        wedges, texts, autotexts = ax.pie(
            amounts, labels=labels, colors=clrs, autopct="%1.1f%%",
            startangle=90, pctdistance=0.75, textprops={"fontsize": 8})
        for t in autotexts:
            t.set_fontsize(7)
            t.set_color("white")
            t.set_fontweight("bold")
        ax.set_title("Receivable Aging", fontsize=11, fontweight="bold",
                      color="#1B2A4A", pad=12)

        buf = _create_chart_image(fig)
        story.append(Image(buf, width=340, height=200))
        story.append(Spacer(1, 12))

    # Receivable table (no customer names/emails)
    items = ar.get("details", [])
    if items:
        headers = ["Invoice #", "Date", "Due Date", "Amount", "Balance", "Status"]
        rows = []
        for inv in items[:15]:
            rows.append([
                inv.get("invoice_number", ""),
                inv.get("date", ""),
                inv.get("due_date", ""),
                _fmt_currency(inv.get("amount", 0)),
                _fmt_currency(inv.get("balance", 0)),
                inv.get("status", ""),
            ])
        story.append(_make_data_table(headers, rows,
                                       [80, 70, 70, 80, 80, 70]))
        story.append(Spacer(1, 16))


def _build_accounts_payable(story, data, styles):
    """Accounts Payable section."""
    story.append(Paragraph("Accounts Payable", styles["SectionTitle"]))

    ap = data.get("accounts_payable", {})
    total = _fmt_currency(ap.get("total_outstanding", 0))
    current = _fmt_currency(ap.get("current", 0))
    overdue = _fmt_currency(ap.get("overdue", 0))

    cards = [
        _make_kpi_card(total, "Total Payable", ACCENT_AMBER, styles),
        _make_kpi_card(current, "Current", ACCENT_GREEN, styles),
        _make_kpi_card(overdue, "Overdue", ACCENT_RED, styles),
    ]
    card_w = 155
    gap = 10
    row = Table([cards], colWidths=[card_w, card_w, card_w])
    row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), gap // 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), gap // 2),
    ]))
    outer = Table([[row]], colWidths=[480])
    outer.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(outer)
    story.append(Spacer(1, 12))

    # Payable details (no vendor names/emails)
    items = ap.get("details", [])
    if items:
        headers = ["Bill #", "Date", "Due Date", "Amount", "Balance", "Status"]
        rows = []
        for bill in items[:15]:
            rows.append([
                bill.get("bill_number", ""),
                bill.get("date", ""),
                bill.get("due_date", ""),
                _fmt_currency(bill.get("amount", 0)),
                _fmt_currency(bill.get("balance", 0)),
                bill.get("status", ""),
            ])
        story.append(_make_data_table(headers, rows,
                                       [80, 70, 70, 80, 80, 70], ACCENT_AMBER))
        story.append(Spacer(1, 16))


def _build_regional_comparison(story, data, styles):
    """Regional Comparison — where cash is coming from."""
    story.append(Paragraph("Regional Comparison", styles["SectionTitle"]))

    regions = data.get("regional_data", [])
    if not regions:
        story.append(Paragraph(
            "<i>No regional data available. Regional breakdown requires location-based "
            "invoicing data in Zoho Books.</i>",
            styles["BodyText2"]))
        story.append(Spacer(1, 16))
        return

    story.append(Paragraph(
        "Analysis of revenue distribution across regions, showing where the "
        "majority of cash inflows originate.",
        styles["BodyText2"],
    ))
    story.append(Spacer(1, 8))
    if regions:
        names = [r.get("region", "") for r in regions]
        amounts = [float(r.get("amount", 0)) for r in regions]
        clrs = CHART_COLORS[:len(names)]

        # Pie chart
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 3),
                                         gridspec_kw={"width_ratios": [1, 1.2]})
        fig.set_facecolor("#FFFFFF")
        wedges, texts, autotexts = ax1.pie(
            amounts, colors=clrs, autopct="%1.1f%%", startangle=90,
            pctdistance=0.8, textprops={"fontsize": 7})
        for t in autotexts:
            t.set_fontsize(7)
            t.set_color("white")
            t.set_fontweight("bold")
        ax1.set_title("Revenue by Region", fontsize=10, fontweight="bold",
                       color="#1B2A4A", pad=10)

        # Bar chart
        _style_chart(ax2, "Revenue Comparison")
        ax2.barh(names[::-1], amounts[::-1], color=clrs[:len(names)],
                 edgecolor="white", height=0.5, zorder=3)
        ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: _fmt_currency(v)))
        ax2.grid(axis="x", alpha=0.3, linestyle="--", color="#D1D5DB")
        plt.tight_layout()

        buf = _create_chart_image(fig)
        story.append(Image(buf, width=480, height=200))
        story.append(Spacer(1, 12))

    # Table
    if regions:
        headers = ["Region", "Revenue", "% Share", "Growth"]
        rows = []
        for r in regions:
            rows.append([
                r.get("region", ""),
                _fmt_currency(r.get("amount", 0)),
                _fmt_pct(r.get("percentage", 0)),
                r.get("growth", "N/A"),
            ])
        story.append(_make_data_table(headers, rows, [150, 120, 80, 80]))
        story.append(Spacer(1, 16))


def _build_expense_breakdown(story, data, styles):
    """Expense Breakdown section."""
    story.append(Paragraph("Expense Breakdown", styles["SectionTitle"]))

    expenses = data.get("expense_breakdown", [])
    # Filter out zero-amount entries
    expenses = [e for e in expenses if float(e.get("amount", 0)) > 0]
    if not expenses:
        story.append(Paragraph(
            "<i>No expense/bill data available for this fiscal year period.</i>",
            styles["BodyText2"]))
        story.append(Spacer(1, 16))
        return

    if expenses:
        cats = [e.get("category", "") for e in expenses]
        amts = [float(e.get("amount", 0)) for e in expenses]
        clrs = CHART_COLORS[:len(cats)]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 3.2),
                                         gridspec_kw={"width_ratios": [1, 1.3]})
        fig.set_facecolor("#FFFFFF")

        # Donut chart
        wedges, texts, autotexts = ax1.pie(
            amts, colors=clrs, autopct="%1.1f%%", startangle=90,
            pctdistance=0.8, wedgeprops={"width": 0.5},
            textprops={"fontsize": 7})
        for t in autotexts:
            t.set_fontsize(6)
            t.set_color("white")
            t.set_fontweight("bold")
        ax1.set_title("Expense Distribution", fontsize=10, fontweight="bold",
                       color="#1B2A4A", pad=10)

        # Horizontal bar
        _style_chart(ax2, "Expenses by Category")
        ax2.barh(cats[::-1], amts[::-1], color=clrs[:len(cats)],
                 edgecolor="white", height=0.5, zorder=3)
        for bar, val in zip(ax2.patches, amts[::-1]):
            ax2.text(bar.get_width() + max(amts)*0.02, bar.get_y() + bar.get_height()/2,
                     _fmt_currency(val), va="center", fontsize=7, color="#374151")
        ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: _fmt_currency(v)))
        ax2.grid(axis="x", alpha=0.3, linestyle="--", color="#D1D5DB")
        plt.tight_layout()

        buf = _create_chart_image(fig)
        story.append(Image(buf, width=480, height=215))
        story.append(Spacer(1, 12))

    # Table
    if expenses:
        headers = ["Category", "Amount", "% of Total", "Trend"]
        rows = []
        for e in expenses:
            rows.append([
                e.get("category", ""),
                _fmt_currency(e.get("amount", 0)),
                _fmt_pct(e.get("percentage", 0)),
                e.get("trend", "—"),
            ])
        story.append(_make_data_table(headers, rows,
                                       [180, 100, 80, 80], ACCENT_AMBER))
        story.append(Spacer(1, 16))


def _build_journal_report(story, data, styles):
    """Journal entries report."""
    story.append(Paragraph("Journal Report", styles["SectionTitle"]))

    journal = data.get("journal_report", {})
    summary = journal.get("summary", "")
    if summary:
        story.append(Paragraph(summary, styles["BodyText2"]))
        story.append(Spacer(1, 8))

    # KPI row
    total_entries = journal.get("total_entries", 0)
    total_debit = _fmt_currency(journal.get("total_debit", 0))
    total_credit = _fmt_currency(journal.get("total_credit", 0))

    cards = [
        _make_kpi_card(str(total_entries), "Journal Entries", ACCENT_PURPLE, styles),
        _make_kpi_card(total_debit, "Total Debits", ACCENT_BLUE, styles),
        _make_kpi_card(total_credit, "Total Credits", ACCENT_GREEN, styles),
    ]
    row = Table([cards], colWidths=[170, 170, 170])
    row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(row)
    story.append(Spacer(1, 12))

    # Journal entries table (no customer/vendor names)
    entries = journal.get("entries", [])
    if entries:
        headers = ["Date", "Journal #", "Account", "Debit", "Credit", "Notes"]
        rows = []
        for e in entries[:20]:
            rows.append([
                e.get("date", ""),
                e.get("journal_number", ""),
                e.get("account", ""),
                _fmt_currency(e.get("debit", 0)),
                _fmt_currency(e.get("credit", 0)),
                e.get("notes", "")[:40],
            ])
        story.append(_make_data_table(headers, rows,
                                       [60, 65, 120, 70, 70, 100], ACCENT_PURPLE))
        story.append(Spacer(1, 16))

    # Monthly chart
    monthly = journal.get("monthly_totals", [])
    if monthly:
        months = [m.get("month", "") for m in monthly]
        debits = [float(m.get("debit", 0)) for m in monthly]
        credits = [float(m.get("credit", 0)) for m in monthly]

        # Only render chart if there's actual data
        if any(d > 0 for d in debits) or any(c > 0 for c in credits):
            fig, ax = plt.subplots(figsize=(7, 2.8))
            _style_chart(ax, "Monthly Journal Activity")
            x = range(len(months))
            ax.plot(list(x), debits, marker="o", color=CHART_COLORS[0],
                    linewidth=2, markersize=5, label="Debits", zorder=3)
            ax.plot(list(x), credits, marker="s", color=CHART_COLORS[1],
                    linewidth=2, markersize=5, label="Credits", zorder=3)
            ax.set_xticks(list(x))
            ax.set_xticklabels(months, rotation=45, ha="right")
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: _fmt_currency(v)))
            ax.legend(fontsize=8, frameon=False)
            ax.grid(axis="y", alpha=0.3, linestyle="--", color="#D1D5DB")
            plt.tight_layout()

            buf = _create_chart_image(fig)
            story.append(Image(buf, width=480, height=190))
            story.append(Spacer(1, 16))


def _build_strategic_insights(story, data, styles):
    """Strategic Insights & Key Recommendations."""
    story.append(Paragraph("Strategic Insights &amp; Recommendations",
                           styles["SectionTitle"]))

    insights = data.get("strategic_insights", [])
    if insights:
        story.append(Paragraph("Key Insights", styles["SubSectionTitle"]))
        for insight in insights:
            story.append(Paragraph(
                f"<bullet>&bull;</bullet> {insight}",
                styles["InsightBullet"],
            ))
        story.append(Spacer(1, 12))

    recommendations = data.get("recommendations", [])
    if recommendations:
        story.append(Paragraph("Recommendations", styles["SubSectionTitle"]))
        for i, rec in enumerate(recommendations, 1):
            title = rec.get("title", "")
            desc = rec.get("description", "")
            priority = rec.get("priority", "Medium")

            p_color = {"High": "#EF4444", "Medium": "#F59E0B", "Low": "#10B981"}.get(
                priority, "#6B7280")

            story.append(Paragraph(
                f'<b>{i}. {title}</b> '
                f'<font color="{p_color}" size="8">[{priority} Priority]</font>',
                styles["BodyText2"],
            ))
            story.append(Paragraph(desc, ParagraphStyle(
                "RecDesc", parent=styles["BodyText2"],
                leftIndent=20, spaceAfter=8, textColor=MED_GRAY,
            )))
        story.append(Spacer(1, 16))


# ═══════════════════════════════════════════════════════════════════
# Main PDF Generation
# ═══════════════════════════════════════════════════════════════════

def generate_fiscal_report_pdf(data: dict) -> str:
    """
    Generate a fiscal year PDF report from structured data.

    Args:
        data: Dictionary containing all report sections' data.

    Returns:
        File path to the generated PDF.
    """
    # Output to temp directory
    output_dir = os.path.join(tempfile.gettempdir(), "fiscal_reports")
    os.makedirs(output_dir, exist_ok=True)

    fy = data.get("fiscal_year", "2025-2026").replace("/", "-")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Fiscal_Report_{fy}_{timestamp}.pdf"
    filepath = os.path.join(output_dir, filename)

    styles = _get_styles()
    w, h = A4

    # Build document with two templates: cover and body
    doc = BaseDocTemplate(
        filepath, pagesize=A4,
        leftMargin=40, rightMargin=40,
        topMargin=50, bottomMargin=50,
    )

    cover_frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        w - doc.leftMargin - doc.rightMargin,
        h - doc.topMargin - doc.bottomMargin,
        id="cover",
    )
    body_frame = Frame(
        doc.leftMargin, doc.bottomMargin + 10,
        w - doc.leftMargin - doc.rightMargin,
        h - doc.topMargin - doc.bottomMargin - 30,
        id="body",
    )

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=_cover_page_bg),
        PageTemplate(id="body", frames=[body_frame], onPage=_body_page_bg),
    ])

    story = []

    # Build all sections
    _build_cover_page(story, data, styles)
    _build_executive_summary(story, data, styles)
    _build_overall_sales(story, data, styles)

    story.append(PageBreak())
    _build_top_item(story, data, styles)
    _build_gross_profit(story, data, styles)

    story.append(PageBreak())
    _build_performance_items(story, data, styles)

    story.append(PageBreak())
    _build_accounts_receivable(story, data, styles)

    story.append(PageBreak())
    _build_accounts_payable(story, data, styles)

    story.append(PageBreak())
    _build_regional_comparison(story, data, styles)

    story.append(PageBreak())
    _build_expense_breakdown(story, data, styles)

    story.append(PageBreak())
    _build_journal_report(story, data, styles)

    story.append(PageBreak())
    _build_strategic_insights(story, data, styles)

    # Build PDF
    doc.build(story)
    logger.info("PDF report generated: %s", filepath)
    return filepath
