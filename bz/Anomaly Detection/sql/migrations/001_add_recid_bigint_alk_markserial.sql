-- ============================================================
-- Migration: Add recid_bigint column to raw_ax.alk_markserial
-- Task: Performance optimization for RAW -> DDS
-- Date: 2026-07-23
-- ============================================================

-- Step 1: Add column (fast, no table rewrite)
ALTER TABLE raw_ax.alk_markserial
ADD COLUMN IF NOT EXISTS recid_bigint bigint;

-- Step 2: Populate in batches (run separately if table is large)
-- This is a one-time migration script
UPDATE raw_ax.alk_markserial
SET recid_bigint = CASE
    WHEN trim(recid) ~ '^[0-9]+$'
    THEN trim(recid)::bigint
    ELSE NULL
END
WHERE recid_bigint IS NULL;

-- Step 3: Create index concurrently (non-blocking)
-- Run this separately after population is complete
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_alk_markserial_recid_bigint
-- ON raw_ax.alk_markserial(recid_bigint);

-- Step 4: Analyze for query planner
ANALYZE raw_ax.alk_markserial;
