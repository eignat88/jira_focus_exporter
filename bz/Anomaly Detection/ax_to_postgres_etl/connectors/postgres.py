"""PostgreSQL connector using psycopg2."""

import sys
import psycopg2
import psycopg2.extras
import io
from io import StringIO, BytesIO


class PostgresConnector:
    def __init__(self, host, port, database, user, password, schema="raw_ax"):
        self.conn_str = (
            f"host={host} "
            f"port={port} "
            f"dbname={database} "
            f"user={user} "
            f"password={password}"
        )
        self.schema = schema
        self.conn = None

    def connect(self):
        self.conn = psycopg2.connect(self.conn_str)
        self.conn.autocommit = False
        self.conn.set_client_encoding('UTF8')
        cursor = self.conn.cursor()
        cursor.execute("SHOW client_encoding")
        actual_enc = cursor.fetchone()[0]
        cursor.close()
        if actual_enc.upper() != 'UTF8':
            print(f"WARNING: set_client_encoding('UTF8') did not take effect! "
                  f"Actual client encoding: {actual_enc}", file=sys.stderr)
        return self.conn

    def check_encoding(self):
        cursor = self.conn.cursor()
        cursor.execute('SHOW server_encoding')
        server_enc = cursor.fetchone()[0]
        cursor.execute('SHOW client_encoding')
        client_enc = cursor.fetchone()[0]
        cursor.execute(
            "SELECT pg_encoding_to_char(encoding), datname "
            "FROM pg_database WHERE datname = %s",
            (self.conn.info.dbname,)
        )
        db_info = cursor.fetchone()
        cursor.close()
        return {
            'server_encoding': server_enc,
            'client_encoding': client_enc,
            'db_encoding': db_info[0] if db_info else None,
            'db_name': db_info[1] if db_info else None,
        }

    def disconnect(self):
        if self.conn:
            self.conn.close()

    def execute(self, sql):
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql)
            return cursor
        except Exception as e:
            self.conn.rollback()
            raise e

    def create_schema(self):
        sql = f"CREATE SCHEMA IF NOT EXISTS {self.schema}"
        self.execute(sql)
        self.conn.commit()

    def get_table_columns_info(self, table_name):
        # PostgreSQL stores table names in lowercase
        table_name_lower = table_name.lower()
        sql = f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = '{self.schema}'
        AND table_name = '{table_name_lower}'
        ORDER BY ordinal_position
        """
        cursor = self.execute(sql)
        return cursor.fetchall()

    def table_exists(self, table_name):
        """Check if table exists in schema."""
        # PostgreSQL stores table names in lowercase
        table_name_lower = table_name.lower()
        sql = f"""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = '{self.schema}'
            AND table_name = '{table_name_lower}'
        )
        """
        cursor = self.execute(sql)
        return cursor.fetchone()[0]

    def drop_table(self, table_name):
        sql = f"DROP TABLE IF EXISTS {self.schema}.{table_name}"
        self.execute(sql)
        self.conn.commit()

    def create_table(self, table_name, columns):
        cols = ", ".join([
            f"{col[0]} {col[1]} {'NULL' if col[2] == 'YES' else 'NOT NULL'}"
            for col in columns
        ])
        sql = f"CREATE TABLE IF NOT EXISTS {self.schema}.{table_name} ({cols})"
        self.execute(sql)
        self.conn.commit()

    def copy_from_buffer(self, table_name, columns, buffer):
        col_names = ", ".join(columns)
        sql = f"COPY {self.schema}.{table_name} ({col_names}) FROM STDIN WITH (FORMAT text, NULL E'')"
        cursor = self.conn.cursor()
        buffer.seek(0)
        cursor.copy_expert(sql, buffer)

    def create_staging_table(self, table_name, columns):
        """Drop old staging table if exists (cleanup from previous runs)."""
        staging_name = f"_staging_{table_name}"
        try:
            self.execute(f"DROP TABLE IF EXISTS {self.schema}.{staging_name}")
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def merge_staging_to_target(self, table_name, columns, mode='upsert'):
        """Merge staging to target.

        Modes:
          - 'upsert': INSERT ... ON CONFLICT (recid) DO NOTHING (default)
          - 'update': INSERT ... ON CONFLICT (recid) DO UPDATE SET ... (для incremental)
        """
        staging_name = f"_staging_{table_name}"
        col_names = ", ".join(columns)

        if mode == 'update':
            # Build UPDATE SET clause (excluding recid)
            update_parts = []
            for col in columns:
                if col.lower() != 'recid':
                    update_parts.append(f"{col} = EXCLUDED.{col}")
            update_clause = ", ".join(update_parts)

            sql = f"""
                INSERT INTO {self.schema}.{table_name} ({col_names})
                SELECT {col_names} FROM {self.schema}.{staging_name}
                ON CONFLICT (recid) DO UPDATE SET {update_clause}
            """
        else:
            sql = f"""
                INSERT INTO {self.schema}.{table_name} ({col_names})
                SELECT {col_names} FROM {self.schema}.{staging_name}
                ON CONFLICT (recid) DO NOTHING
            """

        self.execute(sql)
        self.conn.commit()
        # Cleanup staging
        self.execute(f"DROP TABLE IF EXISTS {self.schema}.{staging_name}")
        self.conn.commit()

    def ensure_recid_index(self, table_name):
        """Create unique index on RECID for UPSERT support."""
        sql = f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name.lower()}_recid
            ON {self.schema}.{table_name} (recid)
        """
        self.execute(sql)
        self.conn.commit()

    def create_indexes_after_load(self, table_name, log_func=None):
        """Создать индексы после полной загрузки.

        Алгоритм:
          1. PK/UNIQUE index на RECID (для UPSERT)
          2. ANALYZE для обновления статистики
        """
        if log_func:
            log_func(f"  INDEXES: Создание индексов для {table_name}...")

        # Unique index on RECID
        sql = f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name.lower()}_recid
            ON {self.schema}.{table_name} (recid)
        """
        self.execute(sql)

        # ANALYZE
        self.execute(f"ANALYZE {self.schema}.{table_name}")

        self.conn.commit()

        if log_func:
            log_func(f"  INDEXES: Готово для {table_name}")

    def get_table_count(self, table_name):
        sql = f"SELECT COUNT(*) FROM {self.schema}.{table_name}"
        cursor = self.execute(sql)
        return cursor.fetchone()[0]

    def create_etl_status_table(self):
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.etl_status (
            id SERIAL PRIMARY KEY,
            table_name TEXT NOT NULL,
            last_recid BIGINT DEFAULT 0,
            last_modified_dt TIMESTAMP,
            total_loaded_rows BIGINT DEFAULT 0,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            status TEXT DEFAULT 'PENDING',
            error_text TEXT
        )
        """
        self.execute(sql)
        self.conn.commit()
        # Add column if table already existed without it
        try:
            self.execute(f"""
                ALTER TABLE {self.schema}.etl_status
                ADD COLUMN IF NOT EXISTS last_modified_dt TIMESTAMP
            """)
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def create_etl_validation_table(self):
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.etl_validation (
            id SERIAL PRIMARY KEY,
            table_name TEXT NOT NULL,
            source_count BIGINT,
            target_count BIGINT,
            difference BIGINT,
            check_date TIMESTAMP DEFAULT NOW()
        )
        """
        self.execute(sql)
        self.conn.commit()

    # --- ETL v2: Status model tables ---

    def create_etl_run_table(self):
        """etl.run — хранит метаданные каждого запуска ETL."""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.etl_run (
            run_id SERIAL PRIMARY KEY,
            started_at TIMESTAMP DEFAULT NOW(),
            finished_at TIMESTAMP,
            status TEXT DEFAULT 'RUNNING',
            source_server TEXT,
            source_database TEXT,
            target_database TEXT,
            config_hash TEXT,
            error_message TEXT
        )
        """
        self.execute(sql)
        self.conn.commit()

    def create_etl_table_run_table(self):
        """etl.table_run — статус загрузки каждой таблицы."""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.etl_table_run (
            id SERIAL PRIMARY KEY,
            run_id INTEGER REFERENCES {self.schema}.etl_run(run_id),
            table_name TEXT NOT NULL,
            load_mode TEXT DEFAULT 'full',
            status TEXT DEFAULT 'PENDING',
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            source_count BIGINT DEFAULT 0,
            target_count BIGINT DEFAULT 0,
            inserted_count BIGINT DEFAULT 0,
            updated_count BIGINT DEFAULT 0,
            rejected_count BIGINT DEFAULT 0,
            last_recid BIGINT DEFAULT 0,
            last_modified_datetime TIMESTAMP,
            error_message TEXT
        )
        """
        self.execute(sql)
        self.conn.commit()

    def create_etl_chunk_run_table(self):
        """etl.chunk_run — статус каждого чанка (для parallel loader)."""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.etl_chunk_run (
            id SERIAL PRIMARY KEY,
            run_id INTEGER REFERENCES {self.schema}.etl_run(run_id),
            table_name TEXT NOT NULL,
            chunk_id INTEGER NOT NULL,
            range_from BIGINT,
            range_to BIGINT,
            status TEXT DEFAULT 'PENDING',
            attempt INTEGER DEFAULT 0,
            rows_read BIGINT DEFAULT 0,
            rows_written BIGINT DEFAULT 0,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            error_message TEXT
        )
        """
        self.execute(sql)
        self.conn.commit()

    def create_etl_errors_table(self):
        """etl.errors — таблица ошибок строк."""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.schema}.etl_errors (
            id SERIAL PRIMARY KEY,
            run_id INTEGER,
            table_name TEXT NOT NULL,
            recid BIGINT,
            batch_start INTEGER,
            batch_end INTEGER,
            error_type TEXT,
            error_message TEXT,
            source_row_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
        self.execute(sql)
        self.conn.commit()

    def create_etl_status_v2(self):
        """Создать все таблицы статусов ETL v2."""
        self.create_etl_run_table()
        self.create_etl_table_run_table()
        self.create_etl_chunk_run_table()
        self.create_etl_errors_table()

    def start_run(self, source_server, source_database, target_database, config_hash=None):
        """Создать запись запуска ETL, вернуть run_id."""
        sql = f"""
        INSERT INTO {self.schema}.etl_run
            (source_server, source_database, target_database, config_hash, status)
        VALUES
            ('{source_server}', '{source_database}', '{target_database}', '{config_hash}', 'RUNNING')
        RETURNING run_id
        """
        cursor = self.execute(sql)
        run_id = cursor.fetchone()[0]
        self.conn.commit()
        return run_id

    def finish_run(self, run_id, status='DONE', error_message=None):
        """Завершить запись запуска ETL."""
        cursor = self.conn.cursor()
        cursor.execute(
            f"UPDATE {self.schema}.etl_run SET finished_at = NOW(), status = %s, error_message = %s WHERE run_id = %s",
            (status, error_message, run_id)
        )
        self.conn.commit()

    def start_table_run(self, run_id, table_name, load_mode='full'):
        """Создать запись загрузки таблицы, вернуть table_run_id."""
        sql = f"""
        INSERT INTO {self.schema}.etl_table_run
            (run_id, table_name, load_mode, status, started_at)
        VALUES
            ({run_id}, '{table_name}', '{load_mode}', 'RUNNING', NOW())
        RETURNING id
        """
        cursor = self.execute(sql)
        table_run_id = cursor.fetchone()[0]
        self.conn.commit()
        return table_run_id

    def finish_table_run(self, table_run_id, status='DONE', source_count=0, target_count=0,
                         inserted=0, updated=0, rejected=0, last_recid=0, error_message=None):
        """Завершить запись загрузки таблицы."""
        cursor = self.conn.cursor()
        cursor.execute(
            f"""UPDATE {self.schema}.etl_table_run
                SET finished_at = NOW(), status = %s,
                    source_count = %s, target_count = %s,
                    inserted_count = %s, updated_count = %s, rejected_count = %s,
                    last_recid = %s, error_message = %s
                WHERE id = %s""",
            (status, source_count, target_count, inserted, updated, rejected, last_recid, error_message, table_run_id)
        )
        self.conn.commit()

    def start_chunk(self, run_id, table_name, chunk_id, range_from, range_to):
        """Создать запись чанка, вернуть chunk_id."""
        sql = f"""
        INSERT INTO {self.schema}.etl_chunk_run
            (run_id, table_name, chunk_id, range_from, range_to, status, started_at)
        VALUES
            ({run_id}, '{table_name}', {chunk_id}, {range_from}, {range_to}, 'RUNNING', NOW())
        RETURNING id
        """
        cursor = self.execute(sql)
        chunk_db_id = cursor.fetchone()[0]
        self.conn.commit()
        return chunk_db_id

    def finish_chunk(self, chunk_db_id, status='DONE', rows_read=0, rows_written=0, error_message=None):
        """Завершить запись чанка."""
        err_val = f"'{error_message}'" if error_message else 'NULL'
        sql = f"""
        UPDATE {self.schema}.etl_chunk_run
        SET finished_at = NOW(), status = '{status}',
            rows_read = {rows_read}, rows_written = {rows_written}, error_message = {err_val}
        WHERE id = {chunk_db_id}
        """
        self.execute(sql)
        self.conn.commit()

    def log_error(self, run_id, table_name, recid=None, batch_start=None, batch_end=None,
                  error_type=None, error_message=None, source_row_json=None):
        """Записать ошибку строки."""
        recid_val = recid if recid else 'NULL'
        bs_val = batch_start if batch_start else 'NULL'
        be_val = batch_end if batch_end else 'NULL'
        err_type = f"'{error_type}'" if error_type else 'NULL'
        err_msg = f"'{error_message}'" if error_message else 'NULL'
        row_json = f"'{source_row_json}'" if source_row_json else 'NULL'
        sql = f"""
        INSERT INTO {self.schema}.etl_errors
            (run_id, table_name, recid, batch_start, batch_end, error_type, error_message, source_row_json)
        VALUES
            ({run_id}, '{table_name}', {recid_val}, {bs_val}, {be_val}, {err_type}, {err_msg}, {row_json})
        """
        self.execute(sql)
        self.conn.commit()

    def get_last_recid(self, table_name):
        sql = f"""
        SELECT last_recid FROM {self.schema}.etl_status
        WHERE table_name = '{table_name}'
        ORDER BY id DESC LIMIT 1
        """
        cursor = self.execute(sql)
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_last_modified(self, table_name):
        sql = f"""
        SELECT last_modified_dt FROM {self.schema}.etl_status
        WHERE table_name = '{table_name}'
        AND last_modified_dt IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """
        cursor = self.execute(sql)
        result = cursor.fetchone()
        return result[0] if result else None

    def update_etl_status(self, table_name, last_recid, total_rows, status, error_text=None):
        last_recid_val = last_recid if last_recid is not None else 0
        sql = f"""
        INSERT INTO {self.schema}.etl_status
            (table_name, last_recid, total_loaded_rows, started_at, status, error_text)
        VALUES
            ('{table_name}', {last_recid_val}, {total_rows}, NOW(), '{status}', {f"'{error_text}'" if error_text else 'NULL'})
        """
        self.execute(sql)
        self.conn.commit()

    def update_last_modified(self, table_name, last_modified_dt, total_rows, status, error_text=None):
        mod_dt_val = f"'{last_modified_dt}'" if last_modified_dt else "NULL"
        sql = f"""
        INSERT INTO {self.schema}.etl_status
            (table_name, last_modified_dt, total_loaded_rows, started_at, status, error_text)
        VALUES
            ('{table_name}', {mod_dt_val}, {total_rows}, NOW(), '{status}', {f"'{error_text}'" if error_text else 'NULL'})
        """
        self.execute(sql)
        self.conn.commit()

    def save_validation(self, table_name, source_count, target_count):
        diff = source_count - target_count
        sql = f"""
        INSERT INTO {self.schema}.etl_validation
            (table_name, source_count, target_count, difference)
        VALUES
            ('{table_name}', {source_count}, {target_count}, {diff})
        """
        self.execute(sql)
        self.conn.commit()

    def extended_validation(self, table_name):
        """Extended validation: COUNT, DISTINCT RECID, MIN/MAX RECID."""
        results = {}
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.{table_name}")
            results['row_count'] = cursor.fetchone()[0]

            try:
                cursor.execute(f"SELECT COUNT(DISTINCT recid) FROM {self.schema}.{table_name}")
                results['unique_recid'] = cursor.fetchone()[0]
            except Exception:
                results['unique_recid'] = results['row_count']

            try:
                cursor.execute(f"SELECT MIN(recid::bigint), MAX(recid::bigint) FROM {self.schema}.{table_name}")
                row = cursor.fetchone()
                results['min_recid'] = row[0]
                results['max_recid'] = row[1]
            except Exception:
                results['min_recid'] = None
                results['max_recid'] = None

            try:
                cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.{table_name} WHERE recid IS NULL")
                results['null_recid'] = cursor.fetchone()[0]
            except Exception:
                results['null_recid'] = 0

            cursor.close()
        except Exception as e:
            results['error'] = str(e)

        return results

    def validate_by_ranges(self, table_name, range_size=10000000):
        """Validate data integrity by RECID ranges for large tables."""
        ranges = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SELECT MIN(recid::bigint), MAX(recid::bigint) FROM {self.schema}.{table_name}")
            row = cursor.fetchone()
            min_r, max_r = row[0] or 0, row[1] or 0

            for start in range(min_r, max_r + 1, range_size):
                end = min(start + range_size, max_r + 1)
                cursor.execute(f"""
                    SELECT COUNT(*), COUNT(DISTINCT recid)
                    FROM {self.schema}.{table_name}
                    WHERE recid::bigint >= {start} AND recid::bigint < {end}
                """)
                row = cursor.fetchone()
                ranges.append({
                    'range': f"{start:,}-{end:,}",
                    'count': row[0],
                    'unique': row[1],
                })
            cursor.close()
        except Exception as e:
            ranges.append({'error': str(e)})

        return ranges
