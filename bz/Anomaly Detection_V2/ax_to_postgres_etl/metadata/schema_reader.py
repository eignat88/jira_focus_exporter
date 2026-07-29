"""Schema reader for SQL Server → PostgreSQL type mapping."""


TYPE_MAP = {
    # Strings
    "nvarchar": "text",
    "varchar": "text",
    "nchar": "text",
    "char": "text",
    "text": "text",
    "ntext": "text",
    "xml": "text",
    # Integers
    "int": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "tinyint": "smallint",
    # Boolean
    "bit": "boolean",
    # DateTime
    "datetime": "text",  # Храним как text для AX-дат (0001-01-01)
    "datetime2": "text",
    "smalldatetime": "text",
    "datetimeoffset": "text",
    "date": "text",  # Храним как text для AX-дат
    "time": "text",
    # Numeric
    "decimal": "numeric",
    "numeric": "numeric",
    "float": "double precision",
    "real": "real",
    "smallmoney": "numeric(10,4)",
    "money": "numeric(19,4)",
    # Binary
    "uniqueidentifier": "text",  # Храним как text, не uuid
    "varbinary": "bytea",
    "binary": "bytea",
    "image": "bytea",
    # Other
    "hierarchyid": "text",
    "sql_variant": "text",
    "geometry": "text",
    "geography": "text",
}


def ss_type_to_pg(ss_type, max_length=None, precision=None, scale=None):
    pg_type = TYPE_MAP.get(ss_type.lower(), "text")
    return pg_type


def read_table_schema(ss_connector, table_name, columns=None):
    all_columns = ss_connector.get_table_columns(table_name)
    if columns:
        col_set = {c.upper() for c in columns}
        all_columns = [col for col in all_columns if col[0].upper() in col_set]
    pg_columns = []
    for col in all_columns:
        col_name = col[0].lower()
        pg_type = ss_type_to_pg(col[1], col[2], col[3], col[4])
        nullable = col[5]
        pg_columns.append((col_name, pg_type, nullable))
    return pg_columns


def sync_target_schema(pg_connector, table_name, ss_columns, log_func=None):
    """Синхронизировать схему PostgreSQL с SQL Server.

    Алгоритм:
      1. Таблица не существует → CREATE TABLE IF NOT EXISTS
      2. Таблица существует, схема совпадает → ничего не делаем
      3. Таблица существует, схема не совпадает → ALTER TABLE ADD COLUMN

    НИКОГДА не удаляет таблицу и не теряет данные.
    """
    desired_cols = [col[0].lower() for col in ss_columns]
    desired_set = set(desired_cols)

    table_exists = pg_connector.table_exists(table_name)

    if not table_exists:
        cols_str = ", ".join([f"{col[0].lower()} text" for col in ss_columns])
        sql = f"CREATE TABLE IF NOT EXISTS {pg_connector.schema}.{table_name} ({cols_str})"
        pg_connector.execute(sql)
        pg_connector.conn.commit()
        if log_func:
            log_func(f"  SCHEMA: Таблица {table_name} создана ({len(ss_columns)} колонок)")
        return

    # Таблица существует — проверяем схему
    existing_info = pg_connector.get_table_columns_info(table_name)
    existing_cols = [col[0].lower() for col in existing_info]
    existing_set = set(existing_cols)

    if existing_set == desired_set:
        if log_func:
            log_func(f"  SCHEMA: Таблица {table_name} OK ({len(existing_cols)} колонок, схема совпадает)")
        return

    # Добавляем недостающие колонки (без DROP)
    missing = desired_set - existing_set
    extra = existing_set - desired_set

    if missing:
        for col in ss_columns:
            col_name = col[0].lower()
            if col_name not in existing_set:
                pg_connector.execute(f"ALTER TABLE {pg_connector.schema}.{table_name} ADD COLUMN {col_name} text")
                if log_func:
                    log_func(f"  SCHEMA: Добавлена колонка: {col_name}")
        pg_connector.conn.commit()

    if extra and log_func:
        log_func(f"  SCHEMA: Лишние колонки в PG (игнорируются): {extra}")

    if log_func:
        log_func(f"  SCHEMA: Таблица {table_name} обновлена ({len(existing_cols)} → {len(desired_set)} колонок)")


# --- Обратная совместимость (старые имена) ---

def ensure_pg_table_schema(pg_connector, table_name, ss_columns, log_func=None):
    """Обёртка над sync_target_schema (обратная совместимость)."""
    sync_target_schema(pg_connector, table_name, ss_columns, log_func=log_func)


def create_pg_table_from_ss(pg_connector, table_name, ss_columns, log_func=None):
    """Обёртка над sync_target_schema (обратная совместимость)."""
    sync_target_schema(pg_connector, table_name, ss_columns, log_func=log_func)
