# Purchase Order RAW → DDS diagnostics

## Files

Place both files in:

```text
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring
```

Files:

- `run_purchase_order_diagnostics.ps1`
- `purchase_order_diagnostics.sql`

The script writes each run into:

```text
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\3\purchase_order_YYYYMMDD_HHMMSS
```

## Safety

The diagnostic SQL is read-only. It does not execute:

- `INSERT`, `UPDATE`, `DELETE`;
- `ANALYZE`;
- `VACUUM`;
- `CREATE INDEX`;
- `EXPLAIN ANALYZE`.

It does perform an exact scan of `raw_ax.purchtable` to check `recid` quality and range. For the current table size of about 293 MB this is acceptable, but it still consumes read I/O.

## Authentication

The script does not store the PostgreSQL password.

Preferred options:

1. Configure `%APPDATA%\postgresql\pgpass.conf`.
2. Set `PGPASSWORD` only for the current PowerShell session:

```powershell
$env:PGPASSWORD = "your_password"
```

Environment variables `PGHOST`, `PGPORT`, `PGDATABASE`, and `PGUSER` are also supported.

## Run

```powershell
cd "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring"

powershell.exe -ExecutionPolicy Bypass `
    -File ".\run_purchase_order_diagnostics.ps1"
```

Using an existing preflight JSON:

```powershell
powershell.exe -ExecutionPolicy Bypass `
    -File ".\run_purchase_order_diagnostics.ps1" `
    -PreflightJson "D:\path\preflight_purchase_order_20260731_153109.json"
```

Skip preflight and run only PostgreSQL diagnostics:

```powershell
powershell.exe -ExecutionPolicy Bypass `
    -File ".\run_purchase_order_diagnostics.ps1" `
    -SkipPreflight
```

## Main result

The main file is:

```text
purchase_order_summary_YYYYMMDD_HHMMSS.csv
```

It includes:

- preflight result, warnings, errors, and all checks;
- source and target existence;
- source/target sizes and estimated rows;
- source and target key types;
- `purchase_order_pkey`;
- missing mapping columns;
- `recid` quality and numeric range;
- whether AX RECID exceeds `int4`;
- presence of `recid_bigint`;
- source B-tree index on `recid`;
- last `ANALYZE`/autoanalyze timestamp;
- active sessions, index builds, and vacuum;
- final decision: `BLOCKED` or `READY_FOR_TEST_LOAD`.

Detailed CSV files and console logs are stored in the same run directory.
