# DDS Run Memo

## Overview

This document describes the optimized DDS (Data Domain Store) loading scripts for the Anomaly Detection project.

## Script Order

Execute scripts in this order:

1. `005_01_product.sql` - Load product data
2. `005_02_warehouse.sql` - Load warehouse data
3. `005_03_marking_code.sql` - Load marking code data
4. `005_04_picking_operation.sql` - Load picking operations (CREATE TABLE AS)
5. `005_05_warehouse_operation.sql` - Load warehouse operations (41M rows, CREATE TABLE AS)
6. `005_06_indexes.sql` - Create all indexes

## Optimization Notes

### Large Tables (41M+ rows)

- `dds.warehouse_operation`: Uses CREATE TABLE AS for better performance
- Indexes are created AFTER data load to avoid overhead during INSERT
- ROW_NUMBER() OVER() generates sequential IDs without SERIAL overhead

### JOIN Operations

- `dds.picking_operation`: LEFT JOIN between `wms_pickdiffactline` and `lfl_pickinglinebuffermarking`
- Join keys: `pickingrouteid = routeid` AND `itemid = itemid`

### Data Types

- All TEXT columns for IDs (flexible for future changes)
- NUMERIC for quantities and durations
- TIMESTAMP for dates
- BOOLEAN for flags

## Performance Tips

1. **Memory**: Ensure sufficient work_mem for large sorts
2. **Parallelism**: Consider setting `max_parallel_workers_per_gather` for large loads
3. **WAL**: For bulk loads, consider `WAL_LEVEL = minimal` temporarily
4. **Vacuum**: Run VACUUM ANALYZE after loading

## Monitoring

Each script includes verification queries. Check output for:
- `dds.product loaded: X`
- `dds.warehouse loaded: X`
- `dds.marking_code loaded: X`
- `dds.picking_operation loaded: X`
- `dds.warehouse_operation loaded: X`

## Rollback

To rollback all DDS data:
```sql
TRUNCATE dds.warehouse_operation CASCADE;
TRUNCATE dds.picking_operation CASCADE;
TRUNCATE dds.marking_code CASCADE;
TRUNCATE dds.warehouse CASCADE;
TRUNCATE dds.product CASCADE;
```
