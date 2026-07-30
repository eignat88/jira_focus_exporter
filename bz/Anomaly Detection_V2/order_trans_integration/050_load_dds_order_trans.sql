\set ON_ERROR_STOP on
\pset pager off
\timing on

-- TEMPLATE FOR ONE DDS CHUNK.
-- Replace :from_key and :to_key with psql variables.
-- Example:
-- psql ... -v from_key=6000000000 -v to_key=6000500000 -f 050_load_dds_order_trans.sql

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30min';

INSERT INTO dds.order_trans
(
    rec_id,
    order_id,
    order_trans_id,
    item_id,
    invent_dim_id,
    qty,
    picked_qty,
    waste_qty,
    modified_datetime,
    created_datetime,
    data_area_id
)
SELECT
    recid_bigint,
    order_id,
    order_trans_id,
    item_id,
    invent_dim_id,
    qty,
    picked_qty,
    waste_qty,
    modified_datetime,
    created_datetime,
    data_area_id
FROM stage_ax.wmsordertrans_normalized
WHERE recid_bigint >= :from_key
  AND recid_bigint <  :to_key
ON CONFLICT (rec_id) DO UPDATE
SET order_id = EXCLUDED.order_id,
    order_trans_id = EXCLUDED.order_trans_id,
    item_id = EXCLUDED.item_id,
    invent_dim_id = EXCLUDED.invent_dim_id,
    qty = EXCLUDED.qty,
    picked_qty = EXCLUDED.picked_qty,
    waste_qty = EXCLUDED.waste_qty,
    modified_datetime = EXCLUDED.modified_datetime,
    created_datetime = EXCLUDED.created_datetime,
    data_area_id = EXCLUDED.data_area_id
WHERE dds.order_trans.modified_datetime IS DISTINCT FROM EXCLUDED.modified_datetime
   OR dds.order_trans.qty IS DISTINCT FROM EXCLUDED.qty
   OR dds.order_trans.picked_qty IS DISTINCT FROM EXCLUDED.picked_qty
   OR dds.order_trans.waste_qty IS DISTINCT FROM EXCLUDED.waste_qty;

COMMIT;
