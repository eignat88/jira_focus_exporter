\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN READ ONLY;
SET LOCAL statement_timeout = '30min';

WITH latest_run AS (
    SELECT run_id, status, started_at, finished_at,
           total_chunks, completed_chunks, failed_chunks,
           rows_read, rows_inserted, rows_updated, rows_conflicted,
           error_message
    FROM etl.load_run
    WHERE source_table = 'salestable'
      AND target_table = 'sales_order'
    ORDER BY run_id DESC
    LIMIT 1
)
SELECT * FROM latest_run;

WITH raw_keys AS MATERIALIZED (
    SELECT btrim(recid)::bigint AS source_recid
    FROM raw_ax.salestable
    WHERE recid IS NOT NULL
      AND btrim(recid) ~ '^[0-9]+$'
), raw_summary AS (
    SELECT count(*) AS raw_valid_rows,
           count(DISTINCT source_recid) AS raw_distinct_keys
    FROM raw_keys
), dds_summary AS (
    SELECT count(*) AS dds_rows,
           count(*) FILTER (WHERE source_recid IS NULL) AS dds_null_keys
    FROM dds.sales_order
), duplicates AS (
    SELECT count(*) AS duplicate_key_groups
    FROM (
        SELECT source_recid
        FROM dds.sales_order
        GROUP BY source_recid
        HAVING count(*) > 1
    ) d
), missing AS (
    SELECT count(*) AS missing_in_dds
    FROM (
        SELECT source_recid FROM raw_keys
        EXCEPT
        SELECT source_recid FROM dds.sales_order
    ) m
), extra AS (
    SELECT count(*) AS extra_in_dds
    FROM (
        SELECT source_recid FROM dds.sales_order
        EXCEPT
        SELECT source_recid FROM raw_keys
    ) e
)
SELECT raw_summary.*, dds_summary.*, duplicates.*, missing.*, extra.*,
       CASE
           WHEN raw_distinct_keys = dds_rows
            AND dds_null_keys = 0
            AND duplicate_key_groups = 0
            AND missing_in_dds = 0
            AND extra_in_dds = 0
           THEN 'RECONCILED'
           ELSE 'REVIEW_REQUIRED'
       END AS reconciliation_status
FROM raw_summary, dds_summary, duplicates, missing, extra;

ROLLBACK;
