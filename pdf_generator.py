"""Render invoice HTML to PDF using WeasyPrint."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _format_amount(value: float) -> str:
    """Format like '5056,00' (comma decimal, no thousands separator).

    Matches the original Word-template invoices.
    """
    return f"{value:.2f}".replace(".", ",")


def render_invoice_pdf(context: dict, output_path: Path) -> Path:
    """Render the invoice HTML template to a PDF on disk."""
    # Pre-format numeric values for the template
    daily_rate = float(context["daily_rate"])
    total = float(context["total"])
    vat_pct = float(context.get("vat_percentage", 0.0))
    vat_amount = round(total * vat_pct / 100.0, 2)
    grand_total = round(total + vat_amount, 2)

    context = {
        **context,
        "daily_rate_str": _format_amount(daily_rate),
        "total_str": _format_amount(total),
        "vat_amount_str": _format_amount(vat_amount) if vat_amount else "0",
        "grand_total_str": _format_amount(grand_total),
        "vat_percentage_str": (
            f"{int(vat_pct)}" if vat_pct == int(vat_pct) else f"{vat_pct}".replace(".", ",")
        ),
    }

    template = _env.get_template("invoice_pdf.html")
    html_str = template.render(**context)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str).write_pdf(str(output_path))
    return output_path
