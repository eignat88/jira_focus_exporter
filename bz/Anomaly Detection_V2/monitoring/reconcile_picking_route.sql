\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN READ ONLY;
SET LOCAL statement_timeout = '30min';

WITH raw_keys AS MATERIALIZED (
    SELECT NULLIF(btrim(pickingrouteid), '') AS picking_route_id
    FROM raw_ax.wmspickingroute
), raw_summary AS (
    SELECT count(*) AS raw_rows,
           count(*) FILTER (WHERE picking_route_id IS NOT NULL) AS raw_valid_rows,
           count(DISTINCT picking_route_id) FILTER (
               WHERE picking_route_id IS NOT NULL
           ) AS raw_distinct_keys,
           count(*) FILTER (WHERE picking_route_id IS NULL) AS raw_invalid_keys
    FROM raw_keys
), dds_summary AS (
    SELECT count(*) AS dds_rows,
           count(*) FILTER (
               WHERE picking_route_id IS NULL OR btrim(picking_route_id) = ''
           ) AS dds_invalid_keys
    FROM dds.picking_route
), duplicates AS (
    SELECT count(*) AS duplicate_key_groups
    FROM (
        SELECT picking_route_id
        FROM dds.picking_route
        GROUP BY picking_route_id
        HAVING count(*) > 1
    ) d
), missing AS (
    SELECT count(*) AS missing_in_dds
    FROM (
        SELECT picking_route_id
        FROM raw_keys
        WHERE picking_route_id IS NOT NULL
        EXCEPT
        SELECT picking_route_id FROM dds.picking_route
    ) m
)
SELECT raw_summary.*, dds_summary.*, duplicates.*, missing.*,
       CASE
           WHEN raw_distinct_keys = dds_rows
            AND dds_invalid_keys = 0
            AND duplicate_key_groups = 0
            AND missing_in_dds = 0
           THEN 'RECONCILED'
           ELSE 'REVIEW_REQUIRED'
       END AS reconciliation_status
FROM raw_summary, dds_summary, duplicates, missing;

ROLLBACK;
