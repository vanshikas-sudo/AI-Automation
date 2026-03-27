"""Quick test: generate a PDF with realistic data to verify charts are populated."""
import os
import sys

from app.services.pdf_report_service import generate_fiscal_report_pdf

data = {
    "fiscal_year": "2024-2025",
    "organization_name": "Test Organization",
    "total_sales": 150000.50,
    "gross_profit": 45000.25,
    "total_expenses": 105000.25,
    "net_income": 45000.25,
    "sales_summary": {"description": "Total sales of $150K across 12 invoices."},
    "monthly_sales": [
        {"month": "Apr", "amount": 12000}, {"month": "May", "amount": 15000},
        {"month": "Jun", "amount": 10000}, {"month": "Jul", "amount": 18000},
        {"month": "Aug", "amount": 14000}, {"month": "Sep", "amount": 11000},
        {"month": "Oct", "amount": 16000}, {"month": "Nov", "amount": 13000},
        {"month": "Dec", "amount": 9000},  {"month": "Jan", "amount": 12000},
        {"month": "Feb", "amount": 10000}, {"month": "Mar", "amount": 10000.50},
    ],
    "sales_breakdown": [
        {"category": "INV-001", "amount": 50000, "percentage": 33.3, "invoice_count": 1},
        {"category": "INV-002", "amount": 30000, "percentage": 20.0, "invoice_count": 1},
    ],
    "top_item": {"name": "Widget Pro", "revenue": 5000, "quantity_sold": 100, "margin": 2500},
    "gross_profit_details": {
        "total_revenue": 150000.50, "cost_of_goods": 105000.25,
        "gross_profit": 45000.25, "margin_pct": 30.0,
    },
    "monthly_gross_profit": [
        {"month": "Apr", "revenue": 12000, "cost": 8000},
        {"month": "May", "revenue": 15000, "cost": 10000},
        {"month": "Jun", "revenue": 10000, "cost": 7000},
        {"month": "Jul", "revenue": 18000, "cost": 12000},
        {"month": "Aug", "revenue": 14000, "cost": 9500},
        {"month": "Sep", "revenue": 11000, "cost": 7500},
        {"month": "Oct", "revenue": 16000, "cost": 11000},
        {"month": "Nov", "revenue": 13000, "cost": 9000},
        {"month": "Dec", "revenue": 9000, "cost": 6000},
        {"month": "Jan", "revenue": 12000, "cost": 8500},
        {"month": "Feb", "revenue": 10000, "cost": 7000},
        {"month": "Mar", "revenue": 10000.50, "cost": 9500.25},
    ],
    "top_5_items": [
        {"name": "Widget Pro", "revenue": 5000, "quantity": 100, "margin": 2500},
        {"name": "Gadget X", "revenue": 3000, "quantity": 50, "margin": 1500},
        {"name": "Tool Kit", "revenue": 2000, "quantity": 30, "margin": 800},
        {"name": "Sensor A", "revenue": 1500, "quantity": 25, "margin": 600},
        {"name": "Cable B", "revenue": 1000, "quantity": 200, "margin": 400},
    ],
    "least_5_items": [
        {"name": "Old Part", "revenue": 50, "quantity": 2, "margin": 10},
        {"name": "Screw Set", "revenue": 100, "quantity": 5, "margin": 30},
        {"name": "Washer", "revenue": 150, "quantity": 10, "margin": 50},
        {"name": "Bolt M3", "revenue": 200, "quantity": 15, "margin": 70},
        {"name": "Nut M5", "revenue": 250, "quantity": 20, "margin": 90},
    ],
    "accounts_receivable": {
        "total_outstanding": 25000, "current": 15000, "overdue": 10000,
        "aging": [
            {"period": "Current", "amount": 15000},
            {"period": "1-30 days", "amount": 5000},
            {"period": "31-60 days", "amount": 3000},
            {"period": "61-90 days", "amount": 1500},
            {"period": "90+ days", "amount": 500},
        ],
        "details": [
            {"invoice_number": "INV-001", "date": "2024-06-01", "due_date": "2024-07-01",
             "amount": 5000, "balance": 5000, "status": "overdue"},
        ],
    },
    "accounts_payable": {
        "total_outstanding": 12000, "current": 8000, "overdue": 4000,
        "details": [
            {"bill_number": "BILL-001", "date": "2024-08-01", "due_date": "2024-09-01",
             "amount": 4000, "balance": 4000, "status": "overdue"},
        ],
    },
    "regional_data": [],
    "expense_breakdown": [
        {"category": "Office Supplies", "amount": 30000, "percentage": 28.6, "trend": "+5%"},
        {"category": "Utilities", "amount": 25000, "percentage": 23.8, "trend": "-2%"},
        {"category": "Travel", "amount": 20000, "percentage": 19.0, "trend": "+10%"},
        {"category": "Marketing", "amount": 15000, "percentage": 14.3, "trend": "+3%"},
        {"category": "Miscellaneous", "amount": 15000.25, "percentage": 14.3, "trend": "0%"},
    ],
    "journal_report": {
        "summary": "15 journal entries recorded in FY 2024-2025.",
        "total_entries": 15, "total_debit": 75000, "total_credit": 75000,
        "entries": [
            {"date": "2024-05-01", "journal_number": "JE-001", "account": "Cash",
             "debit": 5000, "credit": 0, "notes": "Opening balance"},
        ],
        "monthly_totals": [
            {"month": "Apr", "debit": 6000, "credit": 6000},
            {"month": "May", "debit": 7000, "credit": 7000},
            {"month": "Jun", "debit": 5500, "credit": 5500},
            {"month": "Jul", "debit": 8000, "credit": 8000},
            {"month": "Aug", "debit": 6500, "credit": 6500},
            {"month": "Sep", "debit": 5000, "credit": 5000},
            {"month": "Oct", "debit": 7500, "credit": 7500},
            {"month": "Nov", "debit": 6000, "credit": 6000},
            {"month": "Dec", "debit": 5000, "credit": 5000},
            {"month": "Jan", "debit": 6000, "credit": 6000},
            {"month": "Feb", "debit": 5500, "credit": 5500},
            {"month": "Mar", "debit": 7000, "credit": 7000},
        ],
    },
    "strategic_insights": [
        "Revenue grew steadily through Q2 with a peak in July.",
        "Accounts receivable aging shows $10K overdue — follow up needed.",
        "Expense control is on track with utilities trending down.",
    ],
    "recommendations": [
        {"title": "Follow up on overdue invoices", "description": "Contact clients with 90+ day overdue.", "priority": "High"},
        {"title": "Review travel expenses", "description": "Travel spending up 10% YoY.", "priority": "Medium"},
    ],
}

try:
    path = generate_fiscal_report_pdf(data)
    size = os.path.getsize(path)
    print(f"PDF generated: {path}")
    print(f"Size: {size:,} bytes")
    print("SUCCESS - All charts and data should be populated!")
    # Don't delete - let user inspect it
    print(f"\nYou can open the PDF to verify: {path}")
except Exception as e:
    print(f"FAILED: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
