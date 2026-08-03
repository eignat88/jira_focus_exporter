\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN READ ONLY;
SET LOCAL statement_timeout = '30min';

WITH raw_keys AS MATERIALIZED (
    SELECT btrim(recid)::bigint AS task_id
    FROM raw_ax.lfl_scspacktask
    WHERE recid IS NOT NULL
      AND btrim(recid) ~ '^[0-9]+$'
), raw_summary AS (
    SELECT count(*) AS raw_valid_rows,
           count(DISTINCT task_id) AS raw_distinct_keys
    FROM raw_keys
), dds_summary AS (
    SELECT count(*) AS dds_rows,
           count(*) FILTER (WHERE task_id IS NULL) AS dds_null_keys
    FROM dds.pack_task
), duplicates AS (
    SELECT count(*) AS duplicate_key_groups
    FROM (
        SELECT task_id
        FROM dds.pack_task
        GROUP BY task_id
        HAVING count(*) > 1
    ) d
), missing AS (
    SELECT count(*) AS missing_in_dds
    FROM (
        SELECT task_id FROM raw_keys
        EXCEPT
        SELECT task_id FROM dds.pack_task
    ) m
)
SELECT raw_summary.*, dds_summary.*, duplicates.*, missing.*,
       CASE
           WHEN raw_distinct_keys = dds_rows
            AND dds_null_keys = 0
            AND duplicate_key_groups = 0
            AND missing_in_dds = 0
           THEN 'RECONCILED'
           ELSE 'REVIEW_REQUIRED'
       END AS reconciliation_status
FROM raw_summary, dds_summary, duplicates, missing;

ROLLBACK;
