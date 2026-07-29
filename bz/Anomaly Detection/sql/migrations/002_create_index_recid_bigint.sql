-- ============================================================
-- Migration: Create index on recid_bigint
-- Task: Performance optimization for RAW -> DDS
-- Date: 2026-07-23
-- ============================================================

-- Create index concurrently (non-blocking, requires PostgreSQL 12+)
-- This may take several minutes for 150M+ rows
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_alk_markserial_recid_bigint
ON raw_ax.alk_markserial(recid_bigint);

-- Analyze for query planner
ANALYZE raw_ax.alk_markserial;
