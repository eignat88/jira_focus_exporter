\set ON_ERROR_STOP on

/*
One-time migration for dds.purchase_order.

Safety:
- validates the confirmed RAW business key before changing DDS;
- does not modify raw_ax;
- CREATE INDEX CONCURRENTLY is intentionally outside a transaction;
- on the diagnosed 32 KB DDS table, WAL and duration are expected to be low;
- if validation fails, the index is not created.

The RAW duplicate check scans raw_ax.purchtable once (about 293 MB in the
2026-08-03 diagnostic). Run when no purchase_order load is active.
*/

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM raw_ax.purchtable
        WHERE NULLIF(btrim(purchid), '') IS NULL
           OR NULLIF(btrim(dataareaid), '') IS NULL
        LIMIT 1
    ) THEN
        RAISE EXCEPTION
            'RAW business key contains empty purchid or dataareaid';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM raw_ax.purchtable
        GROUP BY
            NULLIF(btrim(purchid), ''),
            NULLIF(btrim(dataareaid), '')
        HAVING count(*) > 1
        LIMIT 1
    ) THEN
        RAISE EXCEPTION
            'RAW business key (purchid, dataareaid) contains duplicates';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM dds.purchase_order
        WHERE purchase_id IS NULL OR data_area_id IS NULL
        LIMIT 1
    ) THEN
        RAISE EXCEPTION
            'DDS business key contains NULL purchase_id or data_area_id';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM dds.purchase_order
        GROUP BY purchase_id, data_area_id
        HAVING count(*) > 1
        LIMIT 1
    ) THEN
        RAISE EXCEPTION
            'DDS business key (purchase_id, data_area_id) contains duplicates';
    END IF;
END
$$;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
    ux_purchase_order_business_key
ON dds.purchase_order (purchase_id, data_area_id);
