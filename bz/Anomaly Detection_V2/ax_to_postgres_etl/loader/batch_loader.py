"""Batch loader using RECID-based pagination."""

import sys
import time
import io
import uuid
import threading
import psycopg2


def format_value(val, pg_type=None):
    """
    Format a value for PostgreSQL COPY/text ingestion.

    Edge cases handled:
      - None → empty string
      - bool → 't'/'f'
      - NaN/Infinity → empty (PostgreSQL rejects them in COPY)
      - AX empty dates (0001-01-01, 1900-01-01) → empty
      - \\x00 null bytes → removed
      - Control characters (tab, newline) → sanitized
      - Bytes → decoded with fallback encodings
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return "t" if val else "f"
    if isinstance(val, uuid.UUID):
        return str(val)

    # Handle NaN/Infinity (PostgreSQL COPY rejects these)
    if isinstance(val, float):
        import math
        if math.isnan(val) or math.isinf(val):
            return ""

    # Handle AX empty dates
    if isinstance(val, str):
        # AX uses 0001-01-01 and 1900-01-01 as "empty" dates
        if val in ("0001-01-01", "1900-01-01", "0001-01-01T00:00:00", "1900-01-01T00:00:00"):
            return ""
        # Remove \\x00 null bytes
        val = val.replace("\x00", "")

    if isinstance(val, bytes):
        # Decode bytes to string, trying common encodings
        for enc in ("utf-8", "cp1252", "cp1251", "latin-1"):
            try:
                s = val.decode(enc)
                return _sanitize_string(s)
            except UnicodeDecodeError:
                continue
        # Last resort: decode with replacement
        s = val.decode("utf-8", errors="replace")
        return _sanitize_string(s)

    s = str(val)
    return _sanitize_string(s)


def _sanitize_string(s: str) -> str:
    """
    Remove control characters that break tab-delimited COPY format.
    
    Preserves UTF-8 text (Cyrillic, Unicode) — no ASCII encoding.
    """
    s = s.replace("\x00", "")   # Null bytes
    s = s.replace("\t", " ")    # Tab → space (tab is delimiter)
    s = s.replace("\n", " ")    # Newline → space (newline is row separator)
    s = s.replace("\r", "")     # Carriage return → remove
    return s


def _start_async_count(connector, table_name):
    """Запустить COUNT(*) в фоновом потоке с ОТДЕЛЬНЫМ соединением."""
    result = {'count': None, 'error': None}

    def worker():
        try:
            # Создаём отдельное соединение чтобы не конфликтовать с основным процессом
            if hasattr(connector, 'conn_str'):
                import pyodbc
                conn = pyodbc.connect(connector.conn_str)
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                result['count'] = cursor.fetchone()[0]
                cursor.close()
                conn.close()
            else:
                # Для PostgreSQL
                conn = psycopg2.connect(connector.conn_str)
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                result['count'] = cursor.fetchone()[0]
                cursor.close()
                conn.close()
        except Exception as e:
            result['error'] = e

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, result


def _build_batch_sql(table_name, col_list, where_clause, order_clause, batch_size):
    """Build the SELECT TOP query for fetching a batch."""
    return f"""
    SELECT TOP ({batch_size}) {col_list} FROM {table_name}
    {where_clause}
    {order_clause}
    """


def _build_buffer(rows, col_count, log_func=None):
    """Build a tab-delimited buffer from rows with row-level error isolation.

    Each row is processed independently — if one row fails formatting,
    it is skipped and counted rather than aborting the entire batch.

    Args:
        rows: Iterable of tuples from SQL Server.
        col_count: Expected number of columns per row (for logging).
        log_func: Optional logging callback.

    Returns:
        Tuple of (StringIO buffer, raw content string, skipped row count).
    """
    # Pre-allocate buffer with estimated size
    estimated_size = len(rows) * col_count * 20  # ~20 chars per value
    buffer = io.StringIO()
    skipped_rows = 0

    # Optimized: process all rows in one pass with minimal function calls
    for i, row in enumerate(rows):
        try:
            # Fast path: join pre-processed values
            values = []
            for val in row:
                # Inline format_value for speed
                if val is None:
                    values.append("")
                elif isinstance(val, bool):
                    values.append("t" if val else "f")
                elif isinstance(val, (int, float)):
                    values.append(str(val))
                else:
                    s = str(val) if not isinstance(val, str) else val
                    # Quick sanitize
                    s = s.replace("\x00", "").replace("\t", " ").replace("\n", " ").replace("\r", "")
                    values.append(s)
            buffer.write("\t".join(values) + "\n")
        except Exception as e:
            skipped_rows += 1
            if log_func:
                log_func(f"  WARNING: Row {i} skipped: {e}")
            continue

    if skipped_rows > 0 and log_func:
        log_func(f"  Skipped {skipped_rows} rows due to errors")

    buffer.seek(0)
    content = buffer.read()
    content = content.replace("\x00", "")  # Remove null bytes only
    return io.StringIO(content), content, skipped_rows


def _execute_values_fallback(pg_connector, table_name, columns, rows):
    """Fallback: use psycopg2 execute_values() when COPY fails.
    Inserts rows in chunks of 5000 using VALUES syntax.
    """
    col_names = ", ".join(columns)
    sql = f"INSERT INTO {pg_connector.schema}.{table_name} ({col_names}) VALUES %s"
    from psycopg2.extras import execute_values
    cursor = pg_connector.conn.cursor()
    try:
        chunk_size = 5000
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            clean_chunk = []
            for row in chunk:
                clean_row = []
                for val in row:
                    v = format_value(val)
                    clean_row.append(v if v != "" else None)
                clean_chunk.append(clean_row)
            execute_values(cursor, sql, clean_chunk, page_size=chunk_size)
        pg_connector.conn.commit()
    except Exception as e:
        pg_connector.conn.rollback()
        raise e


def _binary_search_bad_row(pg_connector, table_name, pg_col_names, rows, log_func=None):
    """Binary search to find problematic row in a failed batch."""
    if len(rows) <= 1:
        if log_func:
            recid = rows[0][-1] if rows[0] else 'unknown'
            log_func(f"  BAD ROW found: RECID={recid}")
        return rows[0] if rows else None

    mid = len(rows) // 2
    left = rows[:mid]
    right = rows[mid:]

    try:
        buffer, _, _ = _build_buffer(left, len(pg_col_names))
        pg_connector.copy_to_staging(table_name, pg_col_names, buffer)
    except Exception:
        return _binary_search_bad_row(pg_connector, table_name, pg_col_names, left, log_func)

    try:
        buffer, _, _ = _build_buffer(right, len(pg_col_names))
        pg_connector.copy_to_staging(table_name, pg_col_names, buffer)
    except Exception:
        return _binary_search_bad_row(pg_connector, table_name, pg_col_names, right, log_func)

    return None


def load_table(ss_connector, pg_connector, table_name, batch_size=100000, log_func=None, date_filter=None, query_timeout=600, columns=None, paginate_by=None, incremental_field=None, load_mode="full"):
    """Load table from SQL Server to PostgreSQL.

    Load modes:
      - full:       полная загрузка, CREATE TABLE IF NOT EXISTS, INSERT
      - resume:     продолжение прерванной загрузки (resume from last_recid)
      - incremental: загрузка новых/изменённых записей (WHERE modifiedDateTime > watermark)
      - reload:     очистка таблицы и повторная загрузка (TRUNCATE + INSERT)

    Pagination strategy:
      - paginate_by=None → RECID (primary key) pagination, fastest path
      - paginate_by='STARTDATE' → uses RECID internally if RECID is in columns
      - incremental_field='MODIFIEDDATETIME' → incremental load with watermark
    """
    start_time = time.time()
    use_recid_pagination = False
    use_incremental = incremental_field is not None

    # --- Determine start point based on load_mode ---
    if load_mode == "reload":
        # TRUNCATE table before loading
        try:
            pg_connector.execute(f"TRUNCATE TABLE {pg_connector.schema}.{table_name}")
            pg_connector.conn.commit()
            if log_func:
                log_func(f"  RELOAD: Table truncated")
        except Exception as e:
            if log_func:
                log_func(f"  RELOAD: Truncate failed ({e}), continuing...")

    if load_mode == "full":
        # Start from beginning, ignore any previous state
        last_recid = 0
        last_value = 0
    elif load_mode == "resume":
        # Continue from last checkpoint
        last_recid = pg_connector.get_last_recid(table_name) if not use_incremental else 0
        last_value = last_recid if not use_incremental else None
    elif load_mode == "reload":
        last_recid = 0
        last_value = 0
    else:  # incremental
        last_value = pg_connector.get_last_modified(table_name)
        last_recid = 0

    # Track original columns for PostgreSQL (user-specified, no pagination aids)
    original_columns = list(columns) if columns else None

    if columns:
        cols_upper = [c.upper() for c in columns]
        # Ensure paginate_by column is in the SELECT list
        if paginate_by and paginate_by.upper() not in cols_upper:
            columns = columns + [paginate_by]
        # Ensure incremental_field column is in the SELECT list
        if incremental_field and incremental_field.upper() not in cols_upper:
            columns = columns + [incremental_field]
        # Always include RECID for efficient pagination if not already present
        if "RECID" not in cols_upper:
            columns = columns + ["RECID"]
            use_recid_pagination = True
            if log_func:
                log_func(f"  Added RECID column for efficient pagination")
        elif not paginate_by and not incremental_field:
            use_recid_pagination = True
        # SQL Server SELECT column names (includes RECID for pagination)
        ss_col_names = [c.lower() for c in columns]
        col_list = ", ".join(columns)
        # PostgreSQL INSERT column names (excludes RECID and incremental_field if added)
        pg_col_names = [c.lower() for c in original_columns]
        # Find RECID index in raw rows for pagination advancement
        recid_idx = ss_col_names.index("recid") if "recid" in ss_col_names else None
        # Find incremental_field index in raw rows
        inc_field_idx = ss_col_names.index(incremental_field.lower()) if incremental_field and incremental_field.lower() in ss_col_names else None
        # col_count = columns shown to user (excluding hidden pagination aids)
        hidden = 1 if use_recid_pagination and (paginate_by or incremental_field) else (1 if use_recid_pagination else 0)
        col_count = len(columns) - hidden
    else:
        all_columns = ss_connector.get_table_columns(table_name)
        ss_col_names = [col[0].lower() for col in all_columns]
        pg_col_names = ss_col_names  # SELECT * means PG table matches SS
        col_list = "*"
        col_count = len(ss_col_names)
        use_recid_pagination = True
        recid_idx = ss_col_names.index("recid") if "recid" in ss_col_names else None
        inc_field_idx = ss_col_names.index(incremental_field.lower()) if incremental_field and incremental_field.lower() in ss_col_names else None

    total_loaded = 0
    total_skipped = 0
    copy_errors = 0

    if log_func:
        log_func(f"START {table_name} (mode={load_mode}, batch={batch_size}, resume_from={last_value}, columns={col_count}, filter={date_filter}, paginate_by={paginate_by}, incremental_field={incremental_field}, use_recid={use_recid_pagination})")

    pg_connector.update_etl_status(table_name, last_recid, 0, "RUNNING")

    # Запуск async COUNT(*) для валидации (выполняется параллельно с загрузкой)
    ss_count_thread, ss_count_result = _start_async_count(ss_connector, table_name)
    pg_count_thread, pg_count_result = _start_async_count(pg_connector, table_name)

    try:
        while True:
            # Build WHERE clause
            if use_incremental:
                # Composite cursor: modifiedDateTime + RECID
                if last_value and last_recid:
                    where_clause = (
                        f"WHERE {incremental_field} > '{last_value}' "
                        f"OR ({incremental_field} = '{last_value}' AND RECID > {last_recid})"
                    )
                elif last_value:
                    where_clause = f"WHERE {incremental_field} > '{last_value}'"
                else:
                    where_clause = ""
            elif use_recid_pagination:
                where_clause = f"WHERE RECID > {last_recid}"
            elif paginate_by:
                where_clause = f"WHERE {paginate_by} > '{last_value}'" if last_value else ""
            else:
                where_clause = f"WHERE RECID > {last_recid}"

            if date_filter:
                where_clause += f" AND {date_filter}" if where_clause else f"WHERE {date_filter}"

            # ORDER BY clause
            if use_incremental:
                order_clause = f"ORDER BY {incremental_field}, RECID"
            elif use_recid_pagination:
                order_clause = "ORDER BY RECID"
            else:
                order_clause = f"ORDER BY {paginate_by}"

            sql = _build_batch_sql(table_name, col_list, where_clause, order_clause, batch_size)

            if log_func:
                log_func(f"  QUERY: SELECT TOP ({batch_size}) ... {where_clause} {order_clause}")

            # Profiling: execute
            t_start = time.perf_counter()
            cursor = ss_connector.execute(sql, timeout=query_timeout)
            t_execute = time.perf_counter() - t_start

            # Profiling: fetch - detailed timing with fetchmany()
            t_start = time.perf_counter()
            rows = []
            fetch_size = 5000  # Optimized per benchmark results
            first_batch_time = 0
            batch_times = []
            
            # Measure first row time
            t_first_row = time.perf_counter()
            first_batch = cursor.fetchmany(1)
            first_row_time = time.perf_counter() - t_first_row
            if first_batch:
                rows.extend(first_batch)
            
            # Measure remaining batches
            t_batches = time.perf_counter()
            while True:
                batch = cursor.fetchmany(fetch_size)
                if not batch:
                    break
                rows.extend(batch)
                batch_times.append(time.perf_counter() - t_batches)
                t_batches = time.perf_counter()
            
            t_fetch = time.perf_counter() - t_start
            cursor.close()

            if not rows:
                if log_func:
                    log_func(f"  No more data to load")
                break

            if log_func:
                log_func(f"  FETCH: {len(rows)} rows received")
                log_func(f"  FETCH_DETAIL: first_row={first_row_time:.3f}s batches={len(batch_times)} avg_batch={sum(batch_times)/len(batch_times):.3f}s" if batch_times else f"  FETCH_DETAIL: first_row={first_row_time:.3f}s")

            # Filter rows to PG columns only (exclude RECID and incremental_field if added for pagination)
            pg_rows = rows
            if len(ss_col_names) != len(pg_col_names):
                skip_indices = set()
                if recid_idx is not None:
                    skip_indices.add(recid_idx)
                if inc_field_idx is not None:
                    skip_indices.add(inc_field_idx)
                if skip_indices:
                    pg_rows = [tuple(val for i, val in enumerate(row) if i not in skip_indices) for row in rows]

            # Profiling: buffer
            t_start = time.perf_counter()
            buffer, buffer_content, skipped_rows = _build_buffer(pg_rows, len(pg_col_names), log_func=log_func)
            t_buffer = time.perf_counter() - t_start

            buffer_size = len(buffer_content)
            line_count = buffer_content.count("\n")

            if log_func:
                log_func(f"  BUFFER: {buffer_size:,} bytes, {line_count} rows, {len(pg_col_names)} columns")

            # --- TRANSACTIONAL WRITE: COPY + etl_status in same transaction ---
            t_start = time.perf_counter()
            try:
                pg_connector.conn.autocommit = False

                # Direct COPY to target table
                pg_connector.copy_to_staging(table_name, pg_col_names, buffer)

                # Update checkpoint in same transaction
                if use_incremental and inc_field_idx is not None:
                    raw_val = rows[-1][inc_field_idx] if rows else None
                    new_last_value = str(raw_val) if raw_val is not None else None
                    new_last_recid = int(rows[-1][recid_idx]) if recid_idx is not None and rows else 0
                    # Store both modifiedDateTime and RECID for composite cursor
                    pg_connector.update_last_modified(table_name, new_last_value, total_loaded + line_count, "RUNNING")
                    last_recid = new_last_recid
                elif use_recid_pagination:
                    new_last_recid = int(rows[-1][recid_idx]) if recid_idx is not None and rows else 0
                    pg_connector.update_etl_status(table_name, new_last_recid, total_loaded + line_count, "RUNNING")

                pg_connector.conn.commit()
                t_copy = time.perf_counter() - t_start
                if log_func:
                    log_func(f"  UPSERT+CHECKPOINT: OK (single transaction)")
                    time_per_row = (t_execute + t_fetch + t_buffer + t_copy) / line_count if line_count > 0 else 0
                    buffer_mb = buffer_size / 1024 / 1024
                    log_func(f"  TIMING: execute={t_execute:.2f}s fetch={t_fetch:.2f}s buffer={t_buffer:.2f}s upsert={t_copy:.2f}s")
                    log_func(f"  METRICS: rows={line_count} buffer={buffer_mb:.1f}MB time_per_row={time_per_row:.6f}s")
            except Exception as copy_err:
                pg_connector.conn.rollback()
                copy_errors += 1
                if log_func:
                    log_func(f"  UPSERT failed ({type(copy_err).__name__}: {copy_err})")
                    log_func(f"  Running binary search to find bad row...")

                # Binary search to find and isolate bad row
                bad_row = _binary_search_bad_row(pg_connector, table_name, pg_col_names, pg_rows, log_func)

                if bad_row:
                    # Log the bad row
                    try:
                        recid_val = bad_row[-1] if bad_row else None
                        pg_connector.log_error(
                            run_id=None,
                            table_name=table_name,
                            recid=recid_val,
                            error_type=type(copy_err).__name__,
                            error_message=str(copy_err)[:500],
                        )
                        if log_func:
                            log_func(f"  Bad row logged to etl_errors")
                    except Exception as log_err:
                        if log_func:
                            log_func(f"  Failed to log error: {log_err}")
                else:
                    if log_func:
                        log_func(f"  Binary search: all rows written successfully")

            # Advance cursor (use raw rows which include RECID for pagination)
            if use_incremental and inc_field_idx is not None:
                # Incremental: advance both modifiedDateTime and RECID cursors
                raw_val = rows[-1][inc_field_idx] if rows else None
                last_value = str(raw_val) if raw_val is not None else None
                last_recid = int(rows[-1][recid_idx]) if recid_idx is not None and rows else 0
            elif use_recid_pagination:
                last_recid = rows[-1][recid_idx] if recid_idx is not None and rows else 0
                last_value = last_recid
            elif paginate_by:
                pag_idx = ss_col_names.index(paginate_by.lower()) if paginate_by.lower() in ss_col_names else None
                last_value = str(rows[-1][pag_idx]) if pag_idx is not None and rows else None

            total_loaded += len(rows) - skipped_rows
            total_skipped += skipped_rows

            elapsed = time.time() - start_time
            speed = total_loaded / elapsed if elapsed > 0 else 0

            if log_func:
                if use_incremental:
                    log_func(f"  TOTAL: {total_loaded:,} rows, {speed:,.0f} rows/sec, Last {incremental_field}: {last_value}")
                else:
                    last_cursor = last_recid if use_recid_pagination else last_value
                    log_func(f"  TOTAL: {total_loaded:,} rows, {speed:,.0f} rows/sec, Last: {last_cursor}")

    except Exception as e:
        if log_func:
            import traceback
            log_func(f"  EXCEPTION: {type(e).__name__}: {e}")
            log_func(f"  TRACEBACK:\n{traceback.format_exc()}")
        if use_incremental:
            pg_connector.update_last_modified(table_name, last_value, total_loaded, "ERROR", str(e))
        else:
            last_cursor = last_recid if use_recid_pagination else last_value
            pg_connector.update_etl_status(table_name, last_cursor if last_cursor else 0, total_loaded, "ERROR", str(e))
        raise

    elapsed = time.time() - start_time
    speed = total_loaded / elapsed if elapsed > 0 else 0

    if use_incremental:
        pg_connector.update_last_modified(table_name, last_value, total_loaded, "DONE")
    else:
        last_cursor = last_recid if use_recid_pagination else last_value
        pg_connector.update_etl_status(table_name, last_cursor if last_cursor else 0, total_loaded, "DONE")

    # Сбор результатов async COUNT(*) (не блокирует процесс)
    ss_count_thread.join(timeout=5)
    pg_count_thread.join(timeout=5)
    source_count = ss_count_result['count'] if ss_count_result['count'] is not None else -1
    target_count = pg_count_result['count'] if pg_count_result['count'] is not None else -1
    pg_connector.save_validation(table_name, source_count, target_count)

    if log_func:
        log_func(f"DONE {table_name}: {total_loaded:,} rows loaded, {elapsed:.1f}s, {speed:,.0f} rows/sec")
        if total_skipped > 0:
            log_func(f"  Skipped: {total_skipped:,} rows (row-level errors)")
        if copy_errors:
            log_func(f"  COPY fallbacks: {copy_errors}")
        log_func(f"  Source: {source_count:,}, Target: {target_count:,}, Diff: {source_count - target_count:,}")

        # Extended validation
        ext = pg_connector.extended_validation(table_name)
        if 'error' not in ext:
            log_func(f"  VALIDATION: rows={ext['row_count']:,} unique_recid={ext['unique_recid']:,} min_recid={ext['min_recid']} max_recid={ext['max_recid']} null_recid={ext['null_recid']}")

    return total_loaded
