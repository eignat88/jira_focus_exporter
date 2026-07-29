"""SQL Server connector using pyodbc."""

import pyodbc


class SQLServerConnector:
    def __init__(self, server, database, driver="SQL Server", user=None, password=None, domain=None):
        self.conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"Trusted_Connection=yes;"
        )
        self.conn = None

    def connect(self):
        self.conn = pyodbc.connect(self.conn_str)
        # Set arraysize for faster fetch performance (optimized: 5000)
        cursor = self.conn.cursor()
        cursor.arraysize = 5000
        return self.conn

    def disconnect(self):
        if self.conn:
            self.conn.close()

    def execute(self, sql, timeout=0):
        cursor = self.conn.cursor()
        if timeout and timeout > 0:
            cursor.timeout = timeout
        cursor.execute(sql)
        return cursor

    def fetchmany(self, sql, size):
        cursor = self.conn.cursor()
        cursor.execute(sql)
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                break
            yield rows

    def get_table_columns(self, table_name):
        sql = f"""
        SELECT
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{table_name}'
        ORDER BY ORDINAL_POSITION
        """
        cursor = self.execute(sql)
        return cursor.fetchall()

    def get_table_count(self, table_name):
        sql = f"SELECT COUNT(*) FROM {table_name}"
        cursor = self.execute(sql)
        return cursor.fetchone()[0]

    def get_max_recid(self, table_name):
        sql = f"SELECT MAX(RECID) FROM {table_name}"
        cursor = self.execute(sql)
        result = cursor.fetchone()[0]
        return result if result else 0

    def get_batch_sql(self, table_name, last_recid, batch_size):
        return f"""
        SELECT * FROM {table_name}
        WHERE RECID > {last_recid}
        ORDER BY RECID
        OFFSET 0 ROWS
        FETCH NEXT {batch_size} ROWS ONLY
        """
