"""
Benchmark testing for SQL Server fetch performance.

The script tests different combinations of:
- cursor.arraysize
- cursor.fetchmany(fetch_size)

The benchmark uses the same table and the same 17 columns
that are loaded by the real ETL process for LFL_SCSPACKTASK.

Each combination is executed several times.
The median fetch time is used to select the best configuration.

Usage:
    python ax_to_postgres_etl/benchmark_fetch.py
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
import time
from typing import Any

# Add project root to Python path.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), ".."),
)

from configs.settings import get_settings
from connectors.sqlserver import SQLServerConnector


# ============================================================
# Benchmark configuration
# ============================================================

FETCH_SIZES = [1000, 5000, 10000, 20000, 50000]
ARRAY_SIZES = [1000, 5000, 10000, 20000, 50000]

ROW_LIMIT = 50000  # Reduced for faster testing
NUM_RUNS = 2       # Reduced for faster testing

TABLE_SCHEMA = "dbo"
TABLE_NAME = "ALK_MARKSERIAL"


# Columns for ALK_MARKSERIAL table
COLUMNS = [
    "EMISSIONTYPE",
    "GTIN",
    "INVENTLOCATIONID",
    "ITEMID",
    "OWNERINN",
    "OWNERNAME",
    "PROPRIETORINN",
    "PROPRIETORNAME",
    "SERIALID",
    "STATUSEXT",
    "UPDATED",
    "MODIFIEDDATETIME",
    "MODIFIEDBY",
    "CREATEDDATETIME",
    "CREATEDBY",
    "RECVERSION",
    "RECID",
    "MD5HASH",
    "MARKCODE",
    "MCLOAD",
    "PRODUCTIONDATE",
    "PRODDATE",
]

COLUMNS_SQL = ",\n    ".join(
    f"[{column}]"
    for column in COLUMNS
)

QUERY_TEMPLATE = """
SELECT TOP ({row_limit})
    {columns}
FROM [{schema}].[{table}]
WHERE [RECID] >= ?
ORDER BY [RECID]
"""


# ============================================================
# Metadata validation
# ============================================================

def get_table_columns(
    connection: Any,
    schema_name: str,
    table_name: str,
) -> list[str]:
    """
    Return all physical column names for the specified SQL Server table.
    """

    query = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """

    cursor = connection.cursor()

    try:
        cursor.execute(
            query,
            schema_name,
            table_name,
        )

        return [
            str(row[0])
            for row in cursor.fetchall()
        ]

    finally:
        cursor.close()


def validate_columns(
    connection: Any,
    schema_name: str,
    table_name: str,
    expected_columns: list[str],
) -> None:
    """
    Validate that all benchmark columns exist in SQL Server.

    The script stops before the benchmark if at least one column
    is missing. This avoids a long pyodbc ProgrammingError message.
    """

    actual_columns = get_table_columns(
        connection=connection,
        schema_name=schema_name,
        table_name=table_name,
    )

    actual_lookup = {
        column.upper(): column
        for column in actual_columns
    }

    missing_columns = [
        column
        for column in expected_columns
        if column.upper() not in actual_lookup
    ]

    if missing_columns:
        available_preview = ", ".join(actual_columns[:30])

        raise RuntimeError(
            f"Columns are missing in "
            f"[{schema_name}].[{table_name}]: "
            f"{', '.join(missing_columns)}.\n"
            f"Available columns, first 30: {available_preview}"
        )


def get_start_recid(connection: Any) -> int:
    """
    Return the minimum RECID from the benchmark table.
    """

    query = f"""
        SELECT MIN([RECID])
        FROM [{TABLE_SCHEMA}].[{TABLE_NAME}]
    """

    cursor = connection.cursor()

    try:
        cursor.execute(query)
        row = cursor.fetchone()

        if not row or row[0] is None:
            raise RuntimeError(
                f"Table [{TABLE_SCHEMA}].[{TABLE_NAME}] "
                f"contains no data."
            )

        return int(row[0])

    finally:
        cursor.close()


# ============================================================
# Benchmark execution
# ============================================================

def run_single_test(
    connection: Any,
    array_size: int,
    fetch_size: int,
    start_recid: int,
) -> dict[str, Any]:
    """
    Run one SQL Server fetch benchmark test.
    """

    cursor = connection.cursor()
    cursor.arraysize = array_size

    query = QUERY_TEMPLATE.format(
        row_limit=ROW_LIMIT,
        columns=COLUMNS_SQL,
        schema=TABLE_SCHEMA,
        table=TABLE_NAME,
    )

    execute_seconds = 0.0
    fetch_seconds = 0.0
    total_rows = 0
    batch_count = 0
    first_batch_seconds = 0.0
    max_batch_seconds = 0.0
    total_values = 0

    try:
        execute_started = time.perf_counter()

        cursor.execute(
            query,
            start_recid,
        )

        execute_seconds = (
            time.perf_counter() - execute_started
        )

        fetch_started = time.perf_counter()

        while True:
            batch_started = time.perf_counter()

            rows = cursor.fetchmany(fetch_size)

            batch_seconds = (
                time.perf_counter() - batch_started
            )

            if not rows:
                break

            if batch_count == 0:
                first_batch_seconds = batch_seconds

            max_batch_seconds = max(
                max_batch_seconds,
                batch_seconds,
            )

            row_count = len(rows)

            total_rows += row_count
            total_values += row_count * len(COLUMNS)
            batch_count += 1

        fetch_seconds = (
            time.perf_counter() - fetch_started
        )

    finally:
        cursor.close()

    rows_per_second = (
        total_rows / fetch_seconds
        if fetch_seconds > 0
        else 0.0
    )

    values_per_second = (
        total_values / fetch_seconds
        if fetch_seconds > 0
        else 0.0
    )

    average_batch_seconds = (
        fetch_seconds / batch_count
        if batch_count > 0
        else 0.0
    )

    return {
        "array_size": array_size,
        "fetch_size": fetch_size,
        "rows": total_rows,
        "columns": len(COLUMNS),
        "values": total_values,
        "batches": batch_count,
        "execute_seconds": round(execute_seconds, 3),
        "fetch_seconds": round(fetch_seconds, 3),
        "first_batch_seconds": round(
            first_batch_seconds,
            3,
        ),
        "average_batch_seconds": round(
            average_batch_seconds,
            3,
        ),
        "max_batch_seconds": round(
            max_batch_seconds,
            3,
        ),
        "rows_per_second": round(
            rows_per_second,
            0,
        ),
        "values_per_second": round(
            values_per_second,
            0,
        ),
    }


def run_benchmark() -> list[dict[str, Any]]:
    """
    Run the complete benchmark suite.
    """

    settings = get_settings()

    print("=" * 78)
    print("SQL Server Fetch Benchmark")
    print("=" * 78)

    print("\nConnecting to SQL Server...")

    ss = SQLServerConnector(
        server=settings.source.server,
        database=settings.source.database,
        driver=settings.source.driver,
    )

    ss.connect()

    all_runs: list[dict[str, Any]] = []
    summary_results: list[dict[str, Any]] = []

    try:
        print("Connected!")

        validate_columns(
            connection=ss.conn,
            schema_name=TABLE_SCHEMA,
            table_name=TABLE_NAME,
            expected_columns=COLUMNS,
        )

        start_recid = get_start_recid(ss.conn)

        print(
            f"Table: "
            f"[{TABLE_SCHEMA}].[{TABLE_NAME}]"
        )
        print(f"Start RECID: {start_recid}")
        print(f"Rows per test: {ROW_LIMIT}")
        print(
            f"Runs per combination: {NUM_RUNS}"
        )
        print(f"Columns: {len(COLUMNS)}")
        print(
            f"Values per complete test: "
            f"{ROW_LIMIT * len(COLUMNS):,}"
        )
        print()

        for array_size in ARRAY_SIZES:
            for fetch_size in FETCH_SIZES:
                print(
                    f"\nTesting "
                    f"arraysize={array_size}, "
                    f"fetch_size={fetch_size}..."
                )

                run_results: list[dict[str, Any]] = []

                for run_number in range(
                    1,
                    NUM_RUNS + 1,
                ):
                    result = run_single_test(
                        connection=ss.conn,
                        array_size=array_size,
                        fetch_size=fetch_size,
                        start_recid=start_recid,
                    )

                    result["run"] = run_number

                    run_results.append(result)
                    all_runs.append(result.copy())

                    print(
                        f"  Run {run_number}: "
                        f"execute="
                        f"{result['execute_seconds']:.3f}s, "
                        f"fetch="
                        f"{result['fetch_seconds']:.2f}s, "
                        f"rows={result['rows']}, "
                        f"batches={result['batches']}, "
                        f"speed="
                        f"{result['rows_per_second']:.0f} "
                        f"rows/sec"
                    )

                    print(
                        f"             "
                        f"first_batch="
                        f"{result['first_batch_seconds']:.3f}s, "
                        f"avg_batch="
                        f"{result['average_batch_seconds']:.3f}s, "
                        f"max_batch="
                        f"{result['max_batch_seconds']:.3f}s"
                    )

                fetch_times = [
                    result["fetch_seconds"]
                    for result in run_results
                ]

                speeds = [
                    result["rows_per_second"]
                    for result in run_results
                ]

                values_speeds = [
                    result["values_per_second"]
                    for result in run_results
                ]

                median_fetch = statistics.median(
                    fetch_times
                )

                median_speed = statistics.median(
                    speeds
                )

                median_values_speed = statistics.median(
                    values_speeds
                )

                min_fetch = min(fetch_times)
                max_fetch = max(fetch_times)

                fetch_stdev = (
                    statistics.stdev(fetch_times)
                    if len(fetch_times) > 1
                    else 0.0
                )

                summary = {
                    "array_size": array_size,
                    "fetch_size": fetch_size,
                    "rows": run_results[0]["rows"],
                    "columns": len(COLUMNS),
                    "batches": run_results[0]["batches"],
                    "median_fetch_seconds": round(
                        median_fetch,
                        3,
                    ),
                    "median_rows_per_second": round(
                        median_speed,
                        0,
                    ),
                    "median_values_per_second": round(
                        median_values_speed,
                        0,
                    ),
                    "min_fetch_seconds": round(
                        min_fetch,
                        3,
                    ),
                    "max_fetch_seconds": round(
                        max_fetch,
                        3,
                    ),
                    "fetch_stdev_seconds": round(
                        fetch_stdev,
                        3,
                    ),
                    "runs": NUM_RUNS,
                }

                summary_results.append(summary)

                print(
                    f"  Median fetch: "
                    f"{median_fetch:.2f}s"
                )
                print(
                    f"  Median speed: "
                    f"{median_speed:.0f} rows/sec"
                )
                print(
                    f"  Fetch deviation: "
                    f"{fetch_stdev:.2f}s"
                )

        save_results(
            all_runs=all_runs,
            summary_results=summary_results,
        )

        best = min(
            summary_results,
            key=lambda item: (
                item["median_fetch_seconds"],
                item["fetch_stdev_seconds"],
            ),
        )

        print("\n" + "=" * 78)
        print("Best configuration by median fetch time")
        print("=" * 78)

        print(
            f"arraysize: "
            f"{best['array_size']}"
        )
        print(
            f"fetch_size: "
            f"{best['fetch_size']}"
        )
        print(
            f"median fetch time: "
            f"{best['median_fetch_seconds']:.2f}s"
        )
        print(
            f"median speed: "
            f"{best['median_rows_per_second']:.0f} "
            f"rows/sec"
        )
        print(
            f"fetch deviation: "
            f"{best['fetch_stdev_seconds']:.2f}s"
        )

        print("\nTop 5 configurations:")

        top_five = sorted(
            summary_results,
            key=lambda item: (
                item["median_fetch_seconds"],
                item["fetch_stdev_seconds"],
            ),
        )[:5]

        for position, item in enumerate(
            top_five,
            start=1,
        ):
            print(
                f"{position}. "
                f"arraysize={item['array_size']}, "
                f"fetch_size={item['fetch_size']}, "
                f"median="
                f"{item['median_fetch_seconds']:.2f}s, "
                f"speed="
                f"{item['median_rows_per_second']:.0f} "
                f"rows/sec, "
                f"stdev="
                f"{item['fetch_stdev_seconds']:.2f}s"
            )

        return summary_results

    finally:
        ss.disconnect()
        print("\nDisconnected.")


# ============================================================
# CSV output
# ============================================================

def save_results(
    all_runs: list[dict[str, Any]],
    summary_results: list[dict[str, Any]],
) -> None:
    """
    Save detailed and summary benchmark results.
    """

    output_dir = os.path.dirname(__file__)

    detailed_file = os.path.join(
        output_dir,
        "fetch_benchmark_runs.csv",
    )

    summary_file = os.path.join(
        output_dir,
        "fetch_benchmark_summary.csv",
    )

    with open(
        detailed_file,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "array_size",
                "fetch_size",
                "run",
                "rows",
                "columns",
                "values",
                "batches",
                "execute_seconds",
                "fetch_seconds",
                "first_batch_seconds",
                "average_batch_seconds",
                "max_batch_seconds",
                "rows_per_second",
                "values_per_second",
            ],
        )

        writer.writeheader()
        writer.writerows(all_runs)

    with open(
        summary_file,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "array_size",
                "fetch_size",
                "rows",
                "columns",
                "batches",
                "median_fetch_seconds",
                "median_rows_per_second",
                "median_values_per_second",
                "min_fetch_seconds",
                "max_fetch_seconds",
                "fetch_stdev_seconds",
                "runs",
            ],
        )

        writer.writeheader()
        writer.writerows(summary_results)

    print(
        f"\nDetailed results saved to:\n"
        f"  {detailed_file}"
    )
    print(
        f"Summary results saved to:\n"
        f"  {summary_file}"
    )


if __name__ == "__main__":
    run_benchmark()