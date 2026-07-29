"""Performance report generator for ETL loads."""

import os
from datetime import datetime
from typing import List, Dict, Any, Optional


def generate_performance_report(
    table_name: str,
    metrics: Dict[str, Any],
    batch_stats: Optional[Dict[str, Any]] = None,
    comparison: Optional[Dict[str, Any]] = None,
    output_dir: str = "reports",
) -> str:
    """
    Generate detailed HTML performance report.

    Returns:
        Path to generated HTML file
    """
    elapsed = metrics.get("elapsed_seconds", 0)
    rows = metrics.get("rows_inserted", 0)
    speed = rows / elapsed if elapsed > 0 else 0
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)

    # Batch stats
    batch_html = ""
    if batch_stats:
        batch_html = f"""
        <div class="section">
            <h2>Batch Statistics</h2>
            <table>
                <tr><td>Total batches</td><td>{batch_stats.get('total_batches', 0)}</td></tr>
                <tr><td>Avg batch time</td><td>{batch_stats.get('avg_batch_time_ms', 0):.1f} ms</td></tr>
                <tr><td>P95 batch time</td><td>{batch_stats.get('p95_batch_time_ms', 0):.1f} ms</td></tr>
                <tr><td>Failed batches</td><td>{batch_stats.get('failed_batches', 0)}</td></tr>
            </table>
        </div>
        """

    # Comparison
    comparison_html = ""
    if comparison:
        diff = comparison.get("diff", {})
        inserted_diff = diff.get("inserted", 0)
        speed_diff = diff.get("speed_pct", 0)
        diff_color = "#4CAF50" if inserted_diff >= 0 else "#f44336"
        comparison_html = f"""
        <div class="section">
            <h2>Comparison with Previous Run</h2>
            <table>
                <tr><td>Previous inserted</td><td>{comparison.get('previous', {}).get('inserted', 0):,}</td></tr>
                <tr><td>Difference</td><td style="color:{diff_color}">{inserted_diff:+,}</td></tr>
                <tr><td>Speed change</td><td>{speed_diff:+.1f}%</td></tr>
            </table>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Performance Report — {table_name}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
        .section {{ margin: 20px 0; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td, th {{ padding: 10px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; }}
        .metric {{ font-size: 28px; font-weight: bold; color: #333; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin: 20px 0; }}
        .card {{ background: #f8f9fa; padding: 15px; border-radius: 6px; text-align: center; }}
        .card .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Performance Report: {table_name}</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <div class="grid">
            <div class="card">
                <div class="label">Duration</div>
                <div class="metric">{hours:02d}:{minutes:02d}:{seconds:02d}</div>
            </div>
            <div class="card">
                <div class="label">Rows Inserted</div>
                <div class="metric">{rows:,}</div>
            </div>
            <div class="card">
                <div class="label">Speed</div>
                <div class="metric">{speed:,.0f} rows/s</div>
            </div>
        </div>

        <div class="section">
            <h2>Details</h2>
            <table>
                <tr><td>Source rows</td><td>{metrics.get('source_rows', 0):,}</td></tr>
                <tr><td>Target rows</td><td>{metrics.get('target_rows', 0):,}</td></tr>
                <tr><td>Rows fetched</td><td>{metrics.get('rows_fetched', 0):,}</td></tr>
                <tr><td>Rows conflicted</td><td>{metrics.get('rows_conflicted', 0):,}</td></tr>
                <tr><td>Chunks completed</td><td>{metrics.get('chunks_completed', 0)}/{metrics.get('chunks_total', 0)}</td></tr>
                <tr><td>Failed chunks</td><td>{metrics.get('chunks_failed', 0)}</td></tr>
                <tr><td>Workers</td><td>{metrics.get('workers', 0)}</td></tr>
                <tr><td>Peak memory</td><td>{metrics.get('peak_memory_mb', 0):.1f} MB</td></tr>
            </table>
        </div>

        {batch_html}
        {comparison_html}
    </div>
</body>
</html>"""

    os.makedirs(output_dir, exist_ok=True)
    filename = f"perf_report_{table_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return filepath
