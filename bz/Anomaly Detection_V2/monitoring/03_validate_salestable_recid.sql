\set ON_ERROR_STOP on
\pset pager off
\timing on

-- READ ONLY, но выполняет один полный scan raw_ax.salestable.
-- Назначение: доказать, что expression index не упадет при cast к bigint.

BEGIN TRANSACTION READ ONLY;
SET LOCAL application_name = 'validate_salestable_recid';
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30min';

\echo '=== 1. Active operations before scan ==='
SELECT
    pid,
    application_name,
    state,
    wait_event_type,
    wait_event,
    clock_timestamp() - query_start AS duration,
    left(query, 1000) AS query
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
ORDER BY query_start;

\echo '=== 2. RECID full validation ==='
SELECT
    count(*) AS total_rows,
    count(*) FILTER (WHERE recid IS NULL) AS null_recid,
    count(*) FILTER (
        WHERE recid IS NOT NULL
          AND btrim(recid) = ''
    ) AS empty_recid,
    count(*) FILTER (
        WHERE recid IS NOT NULL
          AND btrim(recid) <> ''
          AND btrim(recid) !~ '^[0-9]+$'
    ) AS non_numeric_recid,
    count(*) FILTER (
        WHERE recid IS NOT NULL
          AND btrim(recid) ~ '^[0-9]+$'
          AND length(btrim(recid)) > 19
    ) AS too_long_for_bigint
FROM raw_ax.salestable;

\echo '=== 3. Invalid samples ==='
SELECT recid
FROM raw_ax.salestable
WHERE recid IS NULL
   OR btrim(recid) = ''
   OR btrim(recid) !~ '^[0-9]+$'
   OR length(btrim(recid)) > 19
LIMIT 100;

\echo '=== 4. Numeric min/max only when values are valid ==='
SELECT
    min(btrim(recid)::bigint) AS min_recid,
    max(btrim(recid)::bigint) AS max_recid
FROM raw_ax.salestable
WHERE recid IS NOT NULL
  AND btrim(recid) ~ '^[0-9]+$'
  AND length(btrim(recid)) <= 19;

ROLLBACK;
