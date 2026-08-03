\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN READ ONLY;
SET LOCAL statement_timeout = '30min';

SELECT run_id, status, started_at, finished_at,
       total_chunks, completed_chunks, failed_chunks,
       rows_read, rows_inserted, rows_updated, rows_conflicted,
       error_message
FROM etl.load_run
WHERE run_id IN (66, 67, 68)
ORDER BY run_id;

SELECT run_id, status, count(*) AS chunks,
       sum(rows_read) AS rows_read,
       sum(rows_inserted) AS rows_inserted,
       sum(rows_updated) AS rows_updated,
       sum(rows_conflicted) AS rows_conflicted
FROM etl.load_chunk
WHERE run_id IN (66, 67, 68)
GROUP BY run_id, status
ORDER BY run_id, status;

WITH raw_keys AS MATERIALIZED (
    SELECT NULLIF(btrim(purchid), '') AS purchase_id,
           NULLIF(btrim(dataareaid), '') AS data_area_id
    FROM raw_ax.purchtable
), raw_summary AS (
    SELECT count(*) AS raw_rows,
           count(*) FILTER (
               WHERE purchase_id IS NOT NULL AND data_area_id IS NOT NULL
           ) AS raw_valid_rows,
           count(DISTINCT (purchase_id, data_area_id)) FILTER (
               WHERE purchase_id IS NOT NULL AND data_area_id IS NOT NULL
           ) AS raw_distinct_keys,
           count(*) FILTER (
               WHERE purchase_id IS NULL OR data_area_id IS NULL
           ) AS raw_invalid_keys
    FROM raw_keys
), dds_summary AS (
    SELECT count(*) AS dds_rows,
           count(*) FILTER (
               WHERE purchase_id IS NULL OR btrim(purchase_id) = ''
                  OR data_area_id IS NULL OR btrim(data_area_id) = ''
           ) AS dds_invalid_keys
    FROM dds.purchase_order
), dds_duplicates AS (
    SELECT count(*) AS duplicate_key_groups
    FROM (
        SELECT purchase_id, data_area_id
        FROM dds.purchase_order
        GROUP BY purchase_id, data_area_id
        HAVING count(*) > 1
    ) d
), missing AS (
    SELECT count(*) AS missing_in_dds
    FROM (
        SELECT purchase_id, data_area_id
        FROM raw_keys
        WHERE purchase_id IS NOT NULL AND data_area_id IS NOT NULL
        EXCEPT
        SELECT purchase_id, data_area_id
        FROM dds.purchase_order
    ) m
), extra AS (
    SELECT count(*) AS extra_in_dds
    FROM (
        SELECT purchase_id, data_area_id
        FROM dds.purchase_order
        EXCEPT
        SELECT purchase_id, data_area_id
        FROM raw_keys
        WHERE purchase_id IS NOT NULL AND data_area_id IS NOT NULL
    ) e
)
SELECT raw_summary.*, dds_summary.*, dds_duplicates.*,
       missing.*, extra.*,
       CASE
           WHEN raw_distinct_keys = dds_rows
            AND raw_invalid_keys = 0
            AND dds_invalid_keys = 0
            AND duplicate_key_groups = 0
            AND missing_in_dds = 0
            AND extra_in_dds = 0
           THEN 'RECONCILED'
           ELSE 'REVIEW_REQUIRED'
       END AS reconciliation_status
FROM raw_summary, dds_summary, dds_duplicates, missing, extra;

ROLLBACK;
