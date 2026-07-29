"""Export ETL reports to PDF."""

import os
from datetime import datetime
from typing import Dict, Any, Optional


def export_summary_pdf(
    table_name: str,
    metrics: Dict[str, Any],
    output_dir: str = "reports",
) -> str:
    """
    Export load summary to PDF.

    Uses reportlab if available, otherwise falls back to HTML.
    """
    try:
        return _export_with_reportlab(table_name, metrics, output_dir)
    except ImportError:
        return _export_as_html(table_name, metrics, output_dir)


def _export_with_reportlab(
    table_name: str,
    metrics: Dict[str, Any],
    output_dir: str,
) -> str:
    """Export using reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    os.makedirs(output_dir, exist_ok=True)
    filename = f"summary_{table_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    filepath = os.path.join(output_dir, filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph(f"ETL Load Summary: {table_name}", styles['Title']))
    elements.append(Spacer(1, 12))

    # Metrics table
    data = [
        ["Metric", "Value"],
        ["Status", metrics.get("status", "unknown")],
        ["Rows Inserted", f"{metrics.get('rows_inserted', 0):,}"],
        ["Rows Fetched", f"{metrics.get('rows_fetched', 0):,}"],
        ["Duration", f"{metrics.get('elapsed_seconds', 0):.1f}s"],
        ["Speed", f"{metrics.get('speed_rows_per_sec', 0):,.0f} rows/s"],
        ["Chunks", f"{metrics.get('chunks_completed', 0)}/{metrics.get('chunks_total', 0)}"],
        ["Failed Chunks", str(metrics.get('chunks_failed', 0))],
    ]

    table = Table(data, colWidths=[200, 200])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))

    elements.append(table)
    doc.build(elements)

    return filepath


def _export_as_html(
    table_name: str,
    metrics: Dict[str, Any],
    output_dir: str,
) -> str:
    """Fallback: export as HTML (can be printed to PDF)."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"summary_{table_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.join(output_dir, filename)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>ETL Summary: {table_name}</title>
    <style>
        body {{ font-family: Arial; margin: 40px; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; }}
        td, th {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4472C4; color: white; }}
    </style>
</head>
<body>
    <h1>ETL Load Summary: {table_name}</h1>
    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Status</td><td>{metrics.get('status', 'unknown')}</td></tr>
        <tr><td>Rows Inserted</td><td>{metrics.get('rows_inserted', 0):,}</td></tr>
        <tr><td>Duration</td><td>{metrics.get('elapsed_seconds', 0):.1f}s</td></tr>
        <tr><td>Speed</td><td>{metrics.get('speed_rows_per_sec', 0):,.0f} rows/s</td></tr>
        <tr><td>Chunks</td><td>{metrics.get('chunks_completed', 0)}/{metrics.get('chunks_total', 0)}</td></tr>
    </table>
</body>
</html>"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    return filepath
