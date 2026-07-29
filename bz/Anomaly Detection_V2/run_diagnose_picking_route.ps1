param(
    [string]$ProjectRoot = "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2",
    [string]$PgHost = "localhost",
    [int]$PgPort = 5432,
    [string]$PgDatabase = "wms_analysis",
    [string]$PgUser = "postgres",
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
)

$ErrorActionPreference = "Stop"

$LogsDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

$Ts = Get-Date -Format "yyyyMMdd_HHmmss"
$SqlFile = Join-Path $LogsDir "diagnose_picking_route_$Ts.sql"
$LogFile = Join-Path $LogsDir "diagnose_picking_route_$Ts.log"

if (-not (Test-Path $PsqlPath)) {
    throw "psql.exe not found: $PsqlPath"
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PGCLIENTENCODING = "UTF8"

$sql = @'
\set ON_ERROR_STOP on
\pset pager off
\pset null '[NULL]'
\timing on

SET application_name = 'diagnose_picking_route';
SET statement_timeout = '60s';
SET lock_timeout = '3s';
SET default_transaction_read_only = on;

BEGIN TRANSACTION READ ONLY;

\echo
\echo ============================================================
\echo 01. GENERAL
\echo ============================================================
SELECT
    clock_timestamp() AS collected_at,
    current_database() AS database_name,
    current_user AS database_user,
    version() AS postgres_version;

\echo
\echo ============================================================
\echo 02. OBJECT EXISTENCE
\echo ============================================================
SELECT
    to_regclass('raw_ax.wmspickingroute') AS source_table,
    to_regclass('dds.picking_route') AS target_table,
    to_regclass('etl.load_run') AS etl_load_run,
    to_regclass('etl.load_chunk') AS etl_load_chunk;

\echo
\echo ============================================================
\echo 03. SOURCE COLUMNS
\echo ============================================================
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'wmspickingroute'
ORDER BY ordinal_position;

\echo
\echo ============================================================
\echo 04. TARGET COLUMNS
\echo ============================================================
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'picking_route'
ORDER BY ordinal_position;

\echo
\echo ============================================================
\echo 05. TABLE SIZES AND STATISTICS
\echo ============================================================
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.reltuples::bigint AS reltuples_estimate,
    c.relpages,
    pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    s.n_live_tup,
    s.n_dead_tup,
    s.last_analyze,
    s.last_autoanalyze,
    s.last_vacuum,
    s.last_autovacuum
FROM pg_class c
JOIN pg_namespace n
  ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s
  ON s.relid = c.oid
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
ORDER BY n.nspname, c.relname;

\echo
\echo ============================================================
\echo 06. SOURCE INDEXES
\echo ============================================================
SELECT
    i.indexrelid::regclass AS index_name,
    i.indisprimary,
    i.indisunique,
    i.indisvalid,
    i.indisready,
    am.amname AS access_method,
    pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size,
    pg_get_indexdef(i.indexrelid) AS index_definition
FROM pg_index i
JOIN pg_class t
  ON t.oid = i.indrelid
JOIN pg_namespace n
  ON n.oid = t.relnamespace
JOIN pg_class ic
  ON ic.oid = i.indexrelid
JOIN pg_am am
  ON am.oid = ic.relam
WHERE n.nspname = 'raw_ax'
  AND t.relname = 'wmspickingroute'
ORDER BY i.indisprimary DESC, i.indisunique DESC, index_name;

\echo
\echo ============================================================
\echo 07. TARGET INDEXES
\echo ============================================================
SELECT
    i.indexrelid::regclass AS index_name,
    i.indisprimary,
    i.indisunique,
    i.indisvalid,
    i.indisready,
    am.amname AS access_method,
    pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size,
    pg_get_indexdef(i.indexrelid) AS index_definition
FROM pg_index i
JOIN pg_class t
  ON t.oid = i.indrelid
JOIN pg_namespace n
  ON n.oid = t.relnamespace
JOIN pg_class ic
  ON ic.oid = i.indexrelid
JOIN pg_am am
  ON am.oid = ic.relam
WHERE n.nspname = 'dds'
  AND t.relname = 'picking_route'
ORDER BY i.indisprimary DESC, i.indisunique DESC, index_name;

\echo
\echo ============================================================
\echo 08. CONSTRAINTS
\echo ============================================================
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    con.conname AS constraint_name,
    con.contype AS constraint_type,
    con.convalidated,
    pg_get_constraintdef(con.oid, true) AS constraint_definition
FROM pg_constraint con
JOIN pg_class c
  ON c.oid = con.conrelid
JOIN pg_namespace n
  ON n.oid = c.relnamespace
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
ORDER BY n.nspname, c.relname, con.contype, con.conname;

\echo
\echo ============================================================
\echo 09. EXPECTED KEY COLUMNS
\echo ============================================================
SELECT
    table_schema,
    table_name,
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE (table_schema, table_name, column_name) IN (
    ('raw_ax', 'wmspickingroute', 'recid'),
    ('raw_ax', 'wmspickingroute', 'recid_bigint'),
    ('raw_ax', 'wmspickingroute', 'pickingrouteid'),
    ('raw_ax', 'wmspickingroute', 'dataareaid'),
    ('dds', 'picking_route', 'route_id'),
    ('dds', 'picking_route', 'picking_route_id'),
    ('dds', 'picking_route', 'dataareaid')
)
ORDER BY table_schema, table_name, column_name;

\echo
\echo ============================================================
\echo 10. SOURCE SAMPLE
\echo ============================================================
SELECT *
FROM raw_ax.wmspickingroute
LIMIT 20;

\echo
\echo ============================================================
\echo 11. SOURCE RECID QUALITY SAMPLE
\echo ============================================================
SELECT
    recid,
    CASE
        WHEN recid IS NULL THEN 'NULL'
        WHEN BTRIM(recid) = '' THEN 'EMPTY'
        WHEN BTRIM(recid) ~ '^[0-9]+$' THEN 'NUMERIC_TEXT'
        ELSE 'INVALID'
    END AS recid_format
FROM raw_ax.wmspickingroute
LIMIT 100;

\echo
\echo ============================================================
\echo 12. SOURCE BUSINESS KEY STATISTICS
\echo ============================================================
SELECT
    attname,
    null_frac,
    n_distinct,
    most_common_vals,
    most_common_freqs
FROM pg_stats
WHERE schemaname = 'raw_ax'
  AND tablename = 'wmspickingroute'
  AND attname = 'pickingrouteid';

\echo
\echo ============================================================
\echo 12.1 SOURCE INDEXES ON pickingrouteid
\echo ============================================================
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'raw_ax'
  AND tablename = 'wmspickingroute'
  AND indexdef ILIKE '%pickingrouteid%'
ORDER BY indexname;

\echo
\echo ============================================================
\echo 12.2 SOURCE DUPLICATE SAMPLE
\echo NOTE: SAMPLE ONLY, NOT EXACT VALIDATION
\echo ============================================================
SELECT
    pickingrouteid,
    COUNT(*) AS row_count
FROM (
    SELECT pickingrouteid
    FROM raw_ax.wmspickingroute
    TABLESAMPLE SYSTEM (0.1)
    WHERE pickingrouteid IS NOT NULL
) src
GROUP BY pickingrouteid
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 100;

\echo
\echo ============================================================
\echo 13. SOURCE EMPTY BUSINESS KEY STATISTICS
\echo ============================================================
SELECT
    attname,
    null_frac,
    n_distinct
FROM pg_stats
WHERE schemaname = 'raw_ax'
  AND tablename = 'wmspickingroute'
  AND attname IN (
      'pickingrouteid',
      'recid_bigint',
      'dataareaid'
  )
ORDER BY attname;

\echo
\echo ============================================================
\echo 13.1 SOURCE EMPTY BUSINESS KEY SAMPLE
\echo NOTE: SAMPLE ONLY, NOT EXACT VALIDATION
\echo ============================================================
SELECT
    COUNT(*) AS sampled_rows,
    COUNT(*) FILTER (
        WHERE pickingrouteid IS NULL
    ) AS null_pickingrouteid,
    COUNT(*) FILTER (
        WHERE pickingrouteid IS NOT NULL
          AND BTRIM(pickingrouteid) = ''
    ) AS empty_pickingrouteid,
    COUNT(*) FILTER (
        WHERE recid_bigint IS NULL
    ) AS null_recid_bigint,
    COUNT(*) FILTER (
        WHERE dataareaid IS NULL
    ) AS null_dataareaid,
    COUNT(*) FILTER (
        WHERE dataareaid IS NOT NULL
          AND BTRIM(dataareaid) = ''
    ) AS empty_dataareaid
FROM raw_ax.wmspickingroute
TABLESAMPLE SYSTEM (0.1);

\echo
\echo ============================================================
\echo 14. TARGET DUPLICATES BY picking_route_id
\echo ============================================================
SELECT
    picking_route_id,
    COUNT(*) AS row_count
FROM dds.picking_route
GROUP BY picking_route_id
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 100;

\echo
\echo ============================================================
\echo 15. TARGET EMPTY BUSINESS KEYS
\echo ============================================================
SELECT
    COUNT(*) FILTER (
        WHERE picking_route_id IS NULL
           OR BTRIM(picking_route_id) = ''
    ) AS empty_picking_route_id,
    COUNT(*) AS total_rows
FROM dds.picking_route;

\echo
\echo ============================================================
\echo 16. ACTIVE SESSIONS
\echo ============================================================
SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    xact_start,
    clock_timestamp() - xact_start AS transaction_age,
    query_start,
    clock_timestamp() - query_start AS query_age,
    LEFT(query, 3000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND (
      query ILIKE '%wmspickingroute%'
      OR query ILIKE '%picking_route%'
      OR application_name ILIKE '%etl%'
  )
ORDER BY query_start NULLS LAST;

\echo
\echo ============================================================
\echo 17. LOCKS
\echo ============================================================
SELECT
    l.pid,
    a.usename,
    a.application_name,
    a.state,
    l.locktype,
    l.mode,
    l.granted,
    l.relation::regclass AS relation_name,
    a.xact_start,
    clock_timestamp() - a.xact_start AS transaction_age,
    LEFT(a.query, 2000) AS query
FROM pg_locks l
LEFT JOIN pg_stat_activity a
  ON a.pid = l.pid
WHERE l.relation IN (
    to_regclass('raw_ax.wmspickingroute'),
    to_regclass('dds.picking_route')
)
ORDER BY l.granted, l.pid, l.mode;

\echo
\echo ============================================================
\echo 18. LONG TRANSACTIONS
\echo ============================================================
SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    xact_start,
    clock_timestamp() - xact_start AS transaction_age,
    backend_xmin,
    LEFT(query, 2000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND xact_start IS NOT NULL
  AND clock_timestamp() - xact_start > interval '5 minutes'
ORDER BY xact_start;

\echo
\echo ============================================================
\echo 19. VACUUM PROGRESS
\echo ============================================================
SELECT
    pid,
    relid::regclass AS relation_name,
    phase,
    heap_blks_total,
    heap_blks_scanned,
    heap_blks_vacuumed,
    index_vacuum_count,
    max_dead_tuple_bytes,
    dead_tuple_bytes,
    num_dead_item_ids,
    indexes_total,
    indexes_processed
FROM pg_stat_progress_vacuum
WHERE relid IN (
    to_regclass('raw_ax.wmspickingroute'),
    to_regclass('dds.picking_route')
);

\echo
\echo ============================================================
\echo 20. CREATE INDEX PROGRESS
\echo ============================================================
SELECT
    pid,
    relid::regclass AS relation_name,
    index_relid::regclass AS index_name,
    command,
    phase,
    lockers_total,
    lockers_done,
    blocks_total,
    blocks_done,
    tuples_total,
    tuples_done
FROM pg_stat_progress_create_index
WHERE relid IN (
    to_regclass('raw_ax.wmspickingroute'),
    to_regclass('dds.picking_route')
);

\echo
\echo ============================================================
\echo 21. WAL
\echo ============================================================
SELECT
    wal_records,
    wal_fpi,
    wal_bytes,
    wal_buffers_full,
    wal_write,
    wal_sync,
    stats_reset
FROM pg_stat_wal;

\echo
\echo ============================================================
\echo 22. CHECKPOINTER
\echo ============================================================
SELECT *
FROM pg_stat_checkpointer;

\echo
\echo ============================================================
\echo 23. DATABASE SIZE
\echo ============================================================
SELECT
    current_database() AS database_name,
    pg_size_pretty(pg_database_size(current_database())) AS database_size;

\echo
\echo ============================================================
\echo 24. EXPLAIN TEXT RECID
\echo ============================================================
EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT *
FROM raw_ax.wmspickingroute
WHERE recid >= '1'
  AND recid < '100001';

\echo
\echo ============================================================
\echo 25. EXPLAIN CAST RECID
\echo ============================================================
EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT *
FROM raw_ax.wmspickingroute
WHERE NULLIF(BTRIM(recid), '')::bigint >= 1
  AND NULLIF(BTRIM(recid), '')::bigint < 100001;

\echo
\echo ============================================================
\echo 26. EXPLAIN RECID_BIGINT IF COLUMN EXISTS
\echo ============================================================
SELECT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'raw_ax'
      AND table_name = 'wmspickingroute'
      AND column_name = 'recid_bigint'
) AS has_recid_bigint
\gset

\if :has_recid_bigint
EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT *
FROM raw_ax.wmspickingroute
WHERE recid_bigint >= 1
  AND recid_bigint < 100001;
\else
\echo SKIPPED: raw_ax.wmspickingroute.recid_bigint does not exist
\endif

\echo
\echo ============================================================
\echo 27. ETL RUN HISTORY
\echo ============================================================
SELECT
    run_id,
    status,
    load_mode,
    chunk_strategy,
    chunk_column,
    started_at,
    finished_at,
    heartbeat_at,
    total_chunks,
    completed_chunks,
    failed_chunks,
    rows_read,
    rows_inserted,
    rows_updated,
    rows_conflicted,
    error_message
FROM etl.load_run
WHERE target_schema = 'dds'
  AND target_table = 'picking_route'
ORDER BY run_id DESC
LIMIT 20;

\echo
\echo ============================================================
\echo 28. FINAL FLAGS
\echo ============================================================
WITH flags AS (
    SELECT
        EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'raw_ax'
              AND table_name = 'wmspickingroute'
              AND column_name = 'recid'
        ) AS recid_exists,
        EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'raw_ax'
              AND table_name = 'wmspickingroute'
              AND column_name = 'recid_bigint'
              AND udt_name = 'int8'
        ) AS recid_bigint_exists,
        EXISTS (
            SELECT 1
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_am am ON am.oid = ic.relam
            WHERE n.nspname = 'raw_ax'
              AND t.relname = 'wmspickingroute'
              AND am.amname = 'btree'
              AND i.indisvalid
              AND i.indisready
              AND pg_get_indexdef(i.indexrelid) ILIKE '%recid_bigint%'
        ) AS recid_bigint_btree_exists,
        EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'dds'
              AND table_name = 'picking_route'
              AND column_name = 'picking_route_id'
        ) AS picking_route_id_exists,
        EXISTS (
            SELECT 1
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'dds'
              AND t.relname = 'picking_route'
              AND i.indisunique
              AND i.indisvalid
              AND i.indisready
              AND pg_get_indexdef(i.indexrelid) ILIKE '%picking_route_id%'
        ) AS picking_route_id_unique,
        EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'dds'
              AND table_name = 'picking_route'
              AND column_name = 'route_id'
              AND column_default ILIKE '%nextval%'
        ) AS route_id_is_surrogate
)
SELECT
    *,
    CASE
        WHEN NOT recid_exists
            THEN 'BLOCKED: source recid missing'
        WHEN NOT recid_bigint_exists
            THEN 'BLOCKED: recid_bigint missing'
        WHEN NOT recid_bigint_btree_exists
            THEN 'BLOCKED: B-tree index on recid_bigint missing'
        WHEN NOT picking_route_id_exists
            THEN 'BLOCKED: picking_route_id missing'
        WHEN NOT picking_route_id_unique
            THEN 'BLOCKED: picking_route_id has no unique index'
        WHEN route_id_is_surrogate
            THEN 'WARNING: route_id is surrogate and should not be conflict key'
        ELSE 'READY FOR CONFIG REVIEW'
    END AS diagnostic_result
FROM flags;

ROLLBACK;
'@

[System.IO.File]::WriteAllText($SqlFile, $sql, $Utf8NoBom)

$header = @"
============================================================
PICKING_ROUTE FULL DIAGNOSTIC
Started:  $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Database: ${PgHost}:${PgPort}/${PgDatabase}
SQL:      $SqlFile
Log:      $LogFile
Read-only: YES
============================================================

"@

[System.IO.File]::WriteAllText($LogFile, $header, $Utf8NoBom)
Write-Host $header

$args = @(
    "-X",
    "-v", "ON_ERROR_STOP=1",
    "-h", $PgHost,
    "-p", "$PgPort",
    "-U", $PgUser,
    "-d", $PgDatabase,
    "-f", $SqlFile
)

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$output = & $PsqlPath @args 2>&1
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference

$text = ($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
[System.IO.File]::AppendAllText($LogFile, $text + [Environment]::NewLine, $Utf8NoBom)

$footer = @"

============================================================
Finished: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
ExitCode: $exitCode
Log:      $LogFile
SQL:      $SqlFile
============================================================
"@

[System.IO.File]::AppendAllText($LogFile, $footer, $Utf8NoBom)
Write-Host $footer

if ($exitCode -ne 0) {
    throw "Diagnostic failed. See log: $LogFile"
}

Write-Host "Diagnostic completed successfully."
