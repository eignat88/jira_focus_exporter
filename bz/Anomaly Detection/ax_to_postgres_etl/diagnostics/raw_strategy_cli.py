"""
RAW → DDS Strategy Diagnostic CLI

Read-only diagnostic tool for analyzing raw_ax tables and recommending
loading strategies for RAW → DDS migration.

Usage:
    python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode scan
    python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode scan --table alk_markserial
    python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode explain --table alk_markserial
    python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode report
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
import yaml


# Default config
DEFAULT_CONFIG = {
    'postgres': {
        'host': 'localhost',
        'port': 5432,
        'database': 'wms_analysis',
        'user': 'postgres',
        'password': '123'
    },
    'diagnostics': {
        'source_schema': 'raw_ax',
        'target_schema': 'dds',
        'statement_timeout': '30s',
        'lock_timeout': '3s',
        'max_rows_for_exact_count': 1_000_000,
        'sample_size': 1000
    },
    'scoring': {
        'numeric_pk': 30,
        'unique_btree_index': 25,
        'btree_index': 20,
        'index_scan': 25,
        'not_nullable': 10,
        'high_uniqueness': 10,
        'monotonic_key': 10,
        'supports_resume': 15,
        'has_modifieddatetime': 10,
        'composite_index': 15,
        'text_key': -20,
        'requires_function': -25,
        'seq_scan': -30,
        'parallel_seq_scan_large': -50,
        'duplicates': -30,
        'high_null_ratio': -20,
        'no_unique_key': -15,
        'requires_sort': -20
    }
}


def load_config(config_path=None):
    """Load configuration."""
    config = DEFAULT_CONFIG.copy()
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f)
            if user_config:
                config.update(user_config)
    return config


def get_connection(config):
    """Create read-only PostgreSQL connection."""
    conn = psycopg2.connect(
        host=config['postgres']['host'],
        port=config['postgres']['port'],
        database=config['postgres']['database'],
        user=config['postgres']['user'],
        password=config['postgres'].get('password', '')
    )
    # Don't set readonly for system catalog queries
    # conn.set_session(readonly=True, autocommit=True)
    conn.autocommit = True
    return conn


def get_tables(conn, schema):
    """Get all tables in schema."""
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename 
        FROM pg_tables 
        WHERE schemaname = %s
        ORDER BY tablename
    """, (schema,))
    return [row[0] for row in cur.fetchall()]


def get_table_info(conn, schema, table):
    """Get table metadata."""
    cur = conn.cursor()
    
    # Basic info
    cur.execute("""
        SELECT 
            c.reltuples::bigint AS est_rows,
            pg_size_pretty(pg_total_relation_size(%s)) AS total_size,
            pg_total_relation_size(%s) AS total_size_bytes,
            pg_size_pretty(pg_table_size(%s)) AS heap_size,
            pg_size_pretty(pg_indexes_size(%s)) AS index_size
        FROM pg_class c
        JOIN pg_namespace n ON c.relnamespace = n.oid
        WHERE n.nspname = %s AND c.relname = %s
    """, (f"{schema}.{table}", f"{schema}.{table}", 
          f"{schema}.{table}", f"{schema}.{table}",
          schema, table))
    
    row = cur.fetchone()
    est_rows = row[0] or 0
    total_size = row[1]
    total_size_bytes = row[2] or 0
    heap_size = row[3]
    index_size = row[4]
    
    # Statistics - pg_stat_user_tables uses relname, not tablename
    cur.execute("""
        SELECT 
            n_live_tup,
            n_dead_tup,
            last_analyze,
            last_autoanalyze
        FROM pg_stat_user_tables
        WHERE schemaname = %s AND relname = %s
    """, (schema, table))
    
    stats = cur.fetchone()
    n_live_tup = stats[0] or 0
    n_dead_tup = stats[1] or 0
    last_analyze = stats[2]
    last_autoanalyze = stats[3]
    
    # Primary key
    cur.execute("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass
        AND i.indisprimary
    """, (f"{schema}.{table}",))
    
    pk_columns = [row[0] for row in cur.fetchall()]
    
    # Indexes - use information_schema for compatibility
    try:
        cur.execute("""
            SELECT 
                indexname,
                indexdef
            FROM pg_indexes
            WHERE schemaname = %s 
            AND tablename = %s
        """, (schema, table))
        indexes = [{'name': row[0], 'def': row[1]} for row in cur.fetchall()]
    except Exception as e:
        print(f"Warning: Could not fetch indexes: {e}")
        indexes = []
    
    indexes = [{'name': row[0], 'def': row[1]} for row in cur.fetchall()]
    
    # Columns
    cur.execute("""
        SELECT 
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (schema, table))
    
    columns = [{'name': row[0], 'type': row[1], 'nullable': row[2] == 'YES', 'default': row[3]} 
               for row in cur.fetchall()]
    
    return {
        'schema': schema,
        'table': table,
        'est_rows': est_rows,
        'total_size': total_size,
        'total_size_bytes': total_size_bytes,
        'heap_size': heap_size,
        'index_size': index_size,
        'n_live_tup': n_live_tup,
        'n_dead_tup': n_dead_tup,
        'last_analyze': last_analyze,
        'last_autoanalyze': last_autoanalyze,
        'primary_key': pk_columns,
        'indexes': indexes,
        'columns': columns,
        'column_count': len(columns)
    }


def analyze_key_candidates(conn, schema, table, columns):
    """Analyze potential chunk key candidates with proper index detection."""
    import re
    candidates = []
    
    # Get all indexes for the table using pg_index for accurate detection
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            i.indexrelid::regclass AS index_name,
            am.amname AS access_method,
            i.indisunique,
            i.indisprimary,
            i.indisvalid,
            i.indisready,
            i.indkey,
            pg_get_indexdef(i.indexrelid) AS index_definition,
            pg_get_expr(i.indexprs, i.indrelid) AS index_expression
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_am am ON am.oid = ic.relam
        WHERE n.nspname = %s AND t.relname = %s
    """, (schema, table))
    
    table_indexes = cur.fetchall()
    
    def column_has_index(col_name):
        """Check if column has a usable B-tree index."""
        for idx in table_indexes:
            idx_name, idx_method, is_unique, is_primary, is_valid, is_ready, indkey, idx_def, idx_expr = idx
            if not is_valid or not is_ready:
                continue
            if idx_method != 'btree':
                continue
            # Check if column is in indkey
            if indkey:
                # Get column positions
                cur.execute("""
                    SELECT attnum FROM pg_attribute 
                    WHERE attrelid = %s::regclass AND attname = %s
                """, (f"{schema}.{table}", col_name))
                col_attnum = cur.fetchone()
                if col_attnum:
                    col_attnum_val = col_attnum[0]
                    # indkey can be string, list, or tuple
                    if isinstance(indkey, str):
                        # Parse string like "5" or "5 6"
                        indkey_vals = [int(x) for x in indkey.split() if x.isdigit()]
                        if col_attnum_val in indkey_vals:
                            return True
                    elif isinstance(indkey, (list, tuple)):
                        if col_attnum_val in indkey:
                            return True
                    else:
                        if col_attnum_val == indkey:
                            return True
        return False
    
    def get_functional_index_expr(col_name):
        """Get functional index expression for a column if it exists."""
        for idx in table_indexes:
            idx_name, idx_method, is_unique, is_primary, is_valid, is_ready, indkey, idx_def, idx_expr = idx
            if not is_valid or not is_ready:
                continue
            if idx_expr and col_name.lower() in idx_expr.lower():
                return idx_expr
        return None
    
    # Check for recid
    for col in columns:
        if col['name'].lower() == 'recid':
            is_numeric = col['type'] in ('bigint', 'integer', 'smallint')
            is_text = col['type'] == 'text'
            
            has_btree_index = column_has_index(col['name'])
            functional_index = get_functional_index_expr(col['name'])
            
            # Check numeric values if text - skip expensive sampling for large tables
            numeric_ratio = 1.0
            if is_text:
                # For large tables, skip the expensive numeric ratio check
                # Just note that it's text type
                numeric_ratio = 0.5  # Unknown without sampling
            
            # Get index definition for display
            index_def = None
            for idx in table_indexes:
                idx_name, idx_method, is_unique, is_primary, is_valid, is_ready, indkey, idx_def, idx_expr = idx
                if idx_method == 'btree' and is_valid and is_ready:
                    index_def = idx_def
                    break
            
            # Determine resume safety
            resume_safety = 'not confirmed'
            if is_numeric and has_btree_index:
                resume_safety = 'confirmed (numeric)'
            elif is_text and functional_index:
                resume_safety = 'confirmed (functional numeric)'
            elif is_text and has_btree_index:
                resume_safety = 'lexical only (not numeric)'
            
            candidates.append({
                'column': col['name'],
                'type': col['type'],
                'is_numeric': is_numeric,
                'is_text': is_text,
                'has_btree_index': has_btree_index,
                'functional_index': functional_index,
                'index_definition': index_def,
                'numeric_ratio': numeric_ratio,
                'resume_safety': resume_safety,
                'priority': 1 if is_numeric and has_btree_index else (2 if is_text and functional_index else 3)
            })
    
    # Check for other numeric columns
    for col in columns:
        if col['type'] in ('bigint', 'integer') and col['name'].lower() != 'recid':
            has_btree_index = column_has_index(col['name'])
            candidates.append({
                'column': col['name'],
                'type': col['type'],
                'is_numeric': True,
                'is_text': False,
                'has_btree_index': has_btree_index,
                'functional_index': None,
                'numeric_ratio': 1.0,
                'priority': 4 if has_btree_index else 5
            })
    
    # Check for modifieddatetime
    for col in columns:
        if col['name'].lower() in ('modifieddatetime', 'createddatetime'):
            has_btree_index = column_has_index(col['name'])
            candidates.append({
                'column': col['name'],
                'type': col['type'],
                'is_numeric': False,
                'is_text': False,
                'has_btree_index': has_btree_index,
                'functional_index': None,
                'numeric_ratio': 0,
                'priority': 6 if has_btree_index else 7
            })
    
    candidates.sort(key=lambda x: x['priority'])
    return candidates, len(table_indexes)


def classify_strategy(table_info, key_candidates, explain_result):
    """Classify recommended strategy with strict eligibility rules."""
    est_rows = table_info['est_rows']
    pk = table_info['primary_key']
    
    # Find best chunk key
    best_key = key_candidates[0] if key_candidates else None
    
    # Score breakdown
    score_breakdown = []
    score = 50  # Base score
    
    # === ELIGIBILITY RULES (hard blocks) ===
    chunked_recid_eligible = True
    block_reasons = []
    
    # Rule 1: Large table needs numeric indexed key
    if est_rows >= 10_000_000:
        if best_key and best_key['is_text']:
            if not best_key.get('functional_index'):
                chunked_recid_eligible = False
                block_reasons.append('Large table with text key and no functional index')
    
    # Rule 2: EXPLAIN must succeed
    if explain_result and explain_result.get('plan_type') in ('ERROR', 'UNKNOWN'):
        chunked_recid_eligible = False
        block_reasons.append('Execution plan was not validated')
    
    # Rule 3: EXPLAIN must show index usage for large tables
    if est_rows >= 10_000_000 and explain_result:
        if explain_result.get('risk') in ('RISK', 'CRITICAL'):
            chunked_recid_eligible = False
            block_reasons.append('Range query performs full table scan')
    
    # === SCORING per strategy ===
    # Calculate scores for each strategy separately
    
    # Score for CHUNKED_RECID
    chunked_score = 50
    if pk:
        chunked_score += 30
    if best_key and best_key['is_numeric'] and best_key.get('has_btree_index'):
        chunked_score += 25
    if explain_result and explain_result.get('risk') == 'GOOD':
        chunked_score += 20
    if est_rows > 10_000_000:
        chunked_score -= 10
    if est_rows > 100_000_000:
        chunked_score -= 10
    if not pk:
        chunked_score -= 10
    if explain_result and explain_result.get('risk') == 'CRITICAL':
        chunked_score -= 50
    if best_key and best_key['is_text']:
        chunked_score -= 30
    if not best_key or not best_key.get('has_btree_index'):
        chunked_score -= 20
    
    # Score for NORMALIZED_STAGING
    normalized_score = 50
    if best_key and best_key['is_text']:
        normalized_score += 20  # Text key is good for this strategy
    if best_key and best_key.get('has_btree_index'):
        normalized_score += 10  # Has index for lexical ordering
    # Check for unique index in table_info
    if table_info.get('has_unique_index'):
        normalized_score += 10  # Unique source key
    if est_rows > 10_000_000:
        normalized_score -= 5  # Large table
    if est_rows > 100_000_000:
        normalized_score -= 5  # Very large table
    if explain_result and explain_result.get('risk') == 'CRITICAL':
        normalized_score -= 10  # Seq Scan for numeric (expected)
    
    # Score for FULL_LOAD
    full_load_score = 50
    if est_rows > 1_000_000:
        full_load_score -= 30  # Too large for full load
    if est_rows > 10_000_000:
        full_load_score -= 20
    
    # Score for BLOCKED
    blocked_score = 0
    
    # Strategy scores
    strategy_scores = {
        'NORMALIZED_STAGING': normalized_score,
        'CHUNKED_RECID': chunked_score,
        'FULL_LOAD': full_load_score,
        'BLOCKED': blocked_score
    }
    
    # Use NORMALIZED_STAGING score as main score
    score = normalized_score
    
    # Score breakdown for display
    score_breakdown = []
    if table_info.get('has_unique_index'):
        score_breakdown.append(('Unique source key', +10))
    if best_key and best_key.get('has_btree_index'):
        score_breakdown.append(('Native text Index Only Scan', +5))
    if best_key and best_key['is_text']:
        score_breakdown.append(('Text chunk key', -20))
    if not best_key or not best_key.get('functional_index'):
        score_breakdown.append(('No numeric-compatible index', -20))
    if explain_result and explain_result.get('risk') == 'CRITICAL':
        score_breakdown.append(('Numeric predicate Seq Scan', -30))
    if est_rows > 10_000_000:
        score_breakdown.append(('Large table', -10))
    if est_rows > 100_000_000:
        score_breakdown.append(('Very large table', -10))
    if not pk:
        score_breakdown.append(('No primary key', -5))
    
    # === STRATEGY SELECTION ===
    # Priority 1: NORMALIZED_STAGING for large tables with text keys
    if est_rows >= 10_000_000 and best_key and best_key['is_text'] and not best_key.get('functional_index'):
        strategy = 'NORMALIZED_STAGING'
        status = 'REQUIRES_PREPARATION'
    # Priority 2: CHUNKED_RECID if eligible
    elif chunked_recid_eligible and best_key and best_key['is_numeric'] and best_key.get('has_btree_index'):
        if est_rows < 1_000_000:
            strategy = 'FULL_LOAD'
            status = 'READY'
        else:
            strategy = 'CHUNKED_RECID'
            status = 'READY' if explain_result and explain_result.get('risk') == 'GOOD' else 'READY_WITH_WARNINGS'
    # Priority 3: INCREMENTAL if timestamp available
    elif best_key and best_key['column'].lower() in ('modifieddatetime', 'createddatetime'):
        strategy = 'INCREMENTAL_TIMESTAMP'
        status = 'REQUIRES_PREPARATION'
    # Priority 4: BLOCKED
    else:
        strategy = 'BLOCKED'
        status = 'BLOCKED'
    
    # Generate risks
    risks = []
    if best_key and best_key['is_text']:
        risks.append('Key stored as text')
    if best_key and not best_key.get('has_btree_index'):
        risks.append('No B-tree index on key')
    if est_rows > 10_000_000 and not pk:
        risks.append('Primary key is absent, but uniqueness is enforced by a unique text index')
    if explain_result and explain_result.get('risk') == 'CRITICAL':
        risks.append('Numeric range predicate performs Seq Scan over large table')
    for reason in block_reasons:
        risks.append(reason)
    
    # Generate recommendations
    recommendations = []
    if strategy == 'NORMALIZED_STAGING':
        recommendations.append('Idempotency preparation:')
        recommendations.append('  1. Verify that bigint normalization does not create collisions')
        recommendations.append('  2. Select the DDS conflict key')
        recommendations.append('  3. Create a unique constraint in DDS only after validation')
        recommendations.append('Prefer generating recid_bigint during SQL Server -> RAW loading')
        recommendations.append('Alternatively create a normalized staging table through controlled CTAS')
        recommendations.append('Create a B-tree index on the normalized numeric key')
        recommendations.append('Allow CHUNKED_RECID only after Index Scan is confirmed')
    if not pk:
        recommendations.append('Do not create primary key on large RAW table automatically')
    
    return {
        'strategy': strategy,
        'status': status,
        'score': score,
        'score_breakdown': score_breakdown,
        'risks': risks,
        'recommendations': recommendations,
        'chunk_key': best_key,
        'chunked_recid_eligible': chunked_recid_eligible,
        'block_reasons': block_reasons
    }


def run_explain(conn, schema, table, chunk_key, mode='text'):
    """Run EXPLAIN for chunk query.
    
    Args:
        mode: 'text' for native text predicate, 'numeric' for normalized numeric predicate
    """
    if not chunk_key:
        return {'plan_type': 'UNKNOWN', 'risk': 'UNKNOWN', 'mode': mode}
    
    cur = conn.cursor()
    col = chunk_key['column']
    
    # Build explain query based on mode
    if mode == 'text':
        # Native text predicate
        explain_sql = f"""
            EXPLAIN (FORMAT JSON)
            SELECT {col}
            FROM {schema}.{table}
            WHERE {col} > '0'
            ORDER BY {col}
            LIMIT 100000
        """
    else:
        # Numeric predicate (for text keys, try trim::bigint)
        explain_sql = f"""
            EXPLAIN (FORMAT JSON)
            SELECT {col}
            FROM {schema}.{table}
            WHERE trim({col})::bigint > 0
            ORDER BY trim({col})::bigint
            LIMIT 100000
        """
    
    try:
        cur.execute(explain_sql)
        plan = cur.fetchone()[0][0]
        
        # Analyze plan - look for the actual scan node (not Limit)
        plan_type = 'UNKNOWN'
        risk = 'UNKNOWN'
        
        # Get the plan tree
        root = plan.get('Plan', {})
        
        # Look for scan nodes in the plan tree
        def find_scan_node(node):
            """Recursively find the actual scan node."""
            node_type = node.get('Node Type', '')
            if 'Index Scan' in node_type or 'Index Only Scan' in node_type:
                return node_type, 'GOOD'
            elif 'Bitmap' in node_type:
                return node_type, 'ACCEPTABLE'
            elif 'Seq Scan' in node_type:
                if 'Parallel' in node_type:
                    return node_type, 'CRITICAL'
                return node_type, 'RISK'
            # Check child nodes
            for key in ['Plans', 'Plan']:
                if key in node:
                    children = node[key] if isinstance(node[key], list) else [node[key]]
                    for child in children:
                        result = find_scan_node(child)
                        if result[0] != 'UNKNOWN':
                            return result
            return 'UNKNOWN', 'UNKNOWN'
        
        plan_type, risk = find_scan_node(root)
        
        # Add interpretation for text mode
        interpretation = None
        if mode == 'text' and risk == 'GOOD':
            interpretation = 'Lexical ordering confirmed. Not suitable for numeric RECID chunks.'
        elif mode == 'numeric' and risk in ('RISK', 'CRITICAL'):
            interpretation = 'Numeric predicate requires full table scan. Normalized staging recommended.'
        
        return {
            'plan_type': plan_type,
            'risk': risk,
            'plan': plan,
            'mode': mode,
            'interpretation': interpretation
        }
    except Exception as e:
        error_msg = str(e)
        # Provide meaningful interpretation for common errors
        interpretation = None
        if 'operator does not exist' in error_msg.lower():
            interpretation = 'Type mismatch confirmed. Numeric predicate not supported without functional index.'
        
        return {'plan_type': 'ERROR', 'risk': 'UNKNOWN', 'error': error_msg, 'mode': mode, 'interpretation': interpretation}


def diagnose_table(conn, schema, table, config):
    """Run full diagnostic for a single table."""
    print(f"\nAnalyzing {schema}.{table}...")
    
    # Get table info
    table_info = get_table_info(conn, schema, table)
    
    # Analyze key candidates
    key_candidates, index_count = analyze_key_candidates(conn, schema, table, table_info['columns'])
    table_info['index_count'] = index_count
    
    # Find best chunk key for EXPLAIN
    best_key = key_candidates[0] if key_candidates else None
    
    # Run both EXPLAIN checks (text and numeric)
    explain_results = {}
    if best_key:
        explain_results['text'] = run_explain(conn, schema, table, best_key, mode='text')
        explain_results['numeric'] = run_explain(conn, schema, table, best_key, mode='numeric')
    
    # Classify strategy (use numeric explain for main classification)
    numeric_explain = explain_results.get('numeric', {})
    classification = classify_strategy(table_info, key_candidates, numeric_explain)
    
    # Add unique index info to classification
    if best_key and best_key.get('has_btree_index'):
        # Check if index is unique
        cur = conn.cursor()
        cur.execute("""
            SELECT i.indisunique
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = %s AND t.relname = %s
            AND i.indisvalid = true
        """, (schema, table))
        for row in cur.fetchall():
            if row[0]:
                classification['has_unique_index'] = True
                break
    
    # Build result
    result = {
        'schema': schema,
        'table': table,
        'timestamp': datetime.now().isoformat(),
        'table_info': table_info,
        'key_candidates': key_candidates,
        'classification': classification,
        'explain': explain_results
    }
    
    return result


def print_report(result):
    """Print diagnostic report to console."""
    info = result['table_info']
    cls = result['classification']
    explain = result.get('explain', {})
    
    print("\n" + "=" * 70)
    print("RAW → DDS STRATEGY DIAGNOSTIC")
    print("=" * 70)
    print(f"Table: {info['schema']}.{info['table']}")
    print(f"Estimated rows: {info['est_rows']:,}")
    print(f"Total size: {info['total_size']}")
    print(f"Indexes: {info.get('index_count', len(info['indexes']))}")
    print(f"Primary key: {', '.join(info['primary_key']) if info['primary_key'] else 'not found'}")
    
    print(f"\nRecommended strategy: {cls['strategy']}")
    print(f"Status: {cls['status']}")
    print(f"Score: {cls['score']}")
    
    # Strategy scores
    if cls.get('strategy_scores'):
        print(f"\nStrategy evaluation:")
        for strat_name, strat_score in cls['strategy_scores'].items():
            if strat_name == cls['strategy']:
                print(f"  {strat_name}:")
                print(f"    Score: {strat_score}")
                print(f"    Eligibility: ALLOWED")
                print(f"    Status: SELECTED")
            elif strat_score <= 0:
                print(f"  {strat_name}:")
                print(f"    Score: {strat_score}")
                print(f"    Eligibility: BLOCKED")
            else:
                print(f"  {strat_name}:")
                print(f"    Score: {strat_score}")
                print(f"    Eligibility: NOT SELECTED")
    
    # Score breakdown for selected strategy
    if cls.get('score_breakdown'):
        print(f"\n{cls['strategy']} score breakdown:")
        for reason, points in cls['score_breakdown']:
            sign = '+' if points >= 0 else ''
            print(f"  {reason:<40} {sign}{points}")
    
    # Chunk key details
    if cls['chunk_key']:
        key = cls['chunk_key']
        print(f"\nChunk key candidate:")
        print(f"  Column: {key['column']}")
        print(f"  Source type: {key['type']}")
        
        # Index details
        if key.get('has_btree_index'):
            print(f"  Direct B-tree index:")
            print(f"    Present: yes")
            print(f"    Ordering: {'numeric' if key['is_numeric'] else 'lexical'}")
            print(f"    Suitable for numeric RECID chunks: {'yes' if key['is_numeric'] else 'no'}")
            if key.get('index_definition'):
                print(f"    Definition: {key['index_definition'][:100]}...")
        else:
            print(f"  Direct B-tree index: no")
        
        # Functional index
        if key.get('functional_index'):
            print(f"  Functional numeric index:")
            print(f"    Present: yes")
            print(f"    Expression: {key['functional_index'][:80]}...")
        else:
            print(f"  Functional numeric index: no")
        
        # Numeric compatibility
        print(f"  Numeric compatibility: {'verified' if key['numeric_ratio'] > 0.99 else 'not verified'}")
        
        # Resume safety
        print(f"  Resume safety:")
        if key['is_numeric'] and key.get('has_btree_index'):
            print(f"    Lexical watermark: confirmed")
            print(f"    Numeric watermark: confirmed")
            print(f"    Overall: confirmed")
        elif key['is_text'] and key.get('functional_index'):
            print(f"    Lexical watermark: available")
            print(f"    Numeric watermark: confirmed (via functional index)")
            print(f"    Overall: confirmed")
        elif key['is_text'] and key.get('has_btree_index'):
            print(f"    Lexical watermark: technically available")
            print(f"    Numeric watermark: not confirmed")
            print(f"    Overall: not confirmed")
        else:
            print(f"    Lexical watermark: not available")
            print(f"    Numeric watermark: not confirmed")
            print(f"    Overall: not confirmed")
    
    # EXPLAIN results
    if explain:
        print(f"\nEXPLAIN checks:")
        
        # Text predicate
        if 'text' in explain:
            text_explain = explain['text']
            print(f"  Native text predicate:")
            print(f"    EXPLAIN status: {'SUCCESS' if text_explain['plan_type'] != 'ERROR' else 'ERROR'}")
            print(f"    Plan type: {text_explain['plan_type']}")
            print(f"    Plan confirmed: yes")
            if text_explain['plan_type'] in ('Index Scan', 'Index Only Scan', 'Bitmap Scan'):
                print(f"    Index used: yes")
                print(f"    Plan acceptable for lexical chunks: yes")
                print(f"    Plan acceptable for numeric RECID chunks: no")
            else:
                print(f"    Index used: no")
            if text_explain.get('error'):
                print(f"    Error: {text_explain['error']}")
        
        # Numeric predicate
        if 'numeric' in explain:
            num_explain = explain['numeric']
            print(f"  Numeric predicate:")
            print(f"    Expression: trim({cls['chunk_key']['column']})::bigint")
            print(f"    EXPLAIN status: {'SUCCESS' if num_explain['plan_type'] != 'ERROR' else 'ERROR'}")
            print(f"    Plan type: {num_explain['plan_type']}")
            print(f"    Plan confirmed: yes")
            if num_explain['plan_type'] in ('Index Scan', 'Index Only Scan', 'Bitmap Scan'):
                print(f"    Index used: yes")
                print(f"    Plan acceptable: yes")
            else:
                print(f"    Index used: no")
                print(f"    Plan acceptable: no")
            if num_explain.get('error'):
                print(f"    Error: {num_explain['error'][:80]}...")
    
    # Unique index info
    if cls.get('has_unique_index'):
        print(f"\nIdempotency:")
        print(f"  Unique index on recid: yes")
        print(f"  Note: Primary key is absent, but a unique index on recid exists.")
        print(f"  DDS idempotency still requires validation of the normalized key.")
    
    # Eligibility
    if cls.get('block_reasons'):
        print(f"\nCHUNKED_RECID eligibility: BLOCKED")
        for reason in cls['block_reasons']:
            print(f"  - {reason}")
    elif cls.get('chunked_recid_eligible'):
        print(f"\nCHUNKED_RECID eligibility: ELIGIBLE")
    else:
        print(f"\nCHUNKED_RECID eligibility: NOT ELIGIBLE")
    
    # Risks
    if cls['risks']:
        print("\nRisks:")
        for risk in cls['risks']:
            print(f"  - {risk}")
    
    # Recommendations
    if cls['recommendations']:
        print("\nRecommendations:")
        for rec in cls['recommendations']:
            print(f"  - {rec}")
    
    print("=" * 70)


def run_scan(config, tables=None):
    """Run scan mode - analyze tables."""
    conn = get_connection(config)
    schema = config['diagnostics']['source_schema']
    
    if tables:
        table_list = tables
    else:
        table_list = get_tables(conn, schema)
    
    results = []
    for table in table_list:
        try:
            result = diagnose_table(conn, schema, table, config)
            results.append(result)
            print_report(result)
        except Exception as e:
            print(f"Error analyzing {table}: {e}")
    
    conn.close()
    return results


def main():
    parser = argparse.ArgumentParser(description='RAW → DDS Strategy Diagnostic')
    parser.add_argument('--mode', choices=['scan', 'explain', 'report', 'validate-config'],
                       default='scan', help='Diagnostic mode')
    parser.add_argument('--table', help='Specific table to analyze')
    parser.add_argument('--config', help='Config file path')
    parser.add_argument('--output', help='Output JSON file')
    
    args = parser.parse_args()
    config = load_config(args.config)
    
    if args.mode == 'scan':
        tables = [args.table] if args.table else None
        results = run_scan(config, tables)
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2, default=str)
            print(f"\nResults saved to {args.output}")
    
    elif args.mode == 'explain':
        if not args.table:
            print("Error: --table required for explain mode")
            sys.exit(1)
        
        conn = get_connection(config)
        schema = config['diagnostics']['source_schema']
        result = diagnose_table(conn, schema, args.table, config)
        print_report(result)
        conn.close()
    
    elif args.mode == 'report':
        results = run_scan(config)
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2, default=str)
            print(f"\nReport saved to {args.output}")
    
    elif args.mode == 'validate-config':
        print("Validate-config mode not yet implemented")


if __name__ == '__main__':
    main()
