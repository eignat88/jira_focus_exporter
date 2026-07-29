"""Анализ пустых колонок перед загрузкой данных."""

import io


def analyze_columns(ss_connector, table_name, sample_size=1000, log_func=None):
    """
    Анализирует колонки таблицы и определяет:
    - какие колонки пустые (все значения NULL или пустая строка)
    - какие колонки содержат только дефолтные значения
    - рекомендации по загрузке
    """
    columns = ss_connector.get_table_columns(table_name)
    col_names = [col[0] for col in columns]

    if log_func:
        log_func(f"  Анализ колонок: {len(col_names)} всего")

    # Получаем выборку данных
    sql = f"SELECT TOP ({sample_size}) * FROM {table_name}"
    cursor = ss_connector.execute(sql)
    rows = cursor.fetchall()
    cursor.close()

    if not rows:
        if log_func:
            log_func(f"  Таблица пуста")
        return {"columns": col_names, "empty": col_names, "keep": []}

    # Анализируем каждую колонку
    col_stats = {}
    for i, col_name in enumerate(col_names):
        values = [row[i] for row in rows]
        non_null = [v for v in values if v is not None and str(v).strip() != ""]
        unique = set(str(v) for v in non_null)

        stats = {
            "total": len(values),
            "non_null": len(non_null),
            "null_pct": (len(values) - len(non_null)) / len(values) * 100 if values else 0,
            "unique_count": len(unique),
            "sample_values": list(unique)[:5]
        }
        col_stats[col_name] = stats

    # Определяем пустые колонки (>95% NULL или все дефолтные)
    empty_cols = []
    keep_cols = []
    for col_name, stats in col_stats.items():
        if stats["null_pct"] > 95:
            empty_cols.append(col_name)
        elif stats["unique_count"] <= 1 and stats["non_null"] > 0:
            # Все значения одинаковые (дефолтные)
            empty_cols.append(col_name)
        else:
            keep_cols.append(col_name)

    if log_func:
        log_func(f"  Пустых колонок: {len(empty_cols)}")
        if empty_cols:
            log_func(f"  Пропуск: {', '.join(empty_cols[:10])}{'...' if len(empty_cols) > 10 else ''}")
        log_func(f"  Колонок для загрузки: {len(keep_cols)}")

    return {
        "columns": col_names,
        "empty": empty_cols,
        "keep": keep_cols,
        "stats": col_stats
    }


def suggest_columns(analysis_result, auto_exclude=False, null_threshold=95, min_unique=2):
    """На основании анализа предлагает список колонок для загрузки.

    Args:
        analysis_result: результат analyze_columns()
        auto_exclude: True — исключает пустые колонки автоматически.
                      False — возвращает ВСЕ колонки.
        null_threshold: порог NULL% для исключения (по умолчанию 95)
        min_unique: минимальное количество уникальных значений
    """
    if not auto_exclude:
        # Возвращаем все колонки
        return list(analysis_result["columns"])

    # Исключаем пустые колонки
    keep = []
    for col_name, stats in analysis_result["stats"].items():
        if stats["null_pct"] <= null_threshold and stats["unique_count"] >= min_unique:
            keep.append(col_name)
    return keep


def format_analysis_report(analysis_result, table_name):
    """Форматирует отчёт об анализе колонок."""
    lines = []
    lines.append(f"=== Анализ колонок: {table_name} ===")
    lines.append(f"Всего колонок: {len(analysis_result['columns'])}")
    lines.append(f"Пустых (>95% NULL): {len(analysis_result['empty'])}")
    lines.append(f"Для загрузки: {len(analysis_result['keep'])}")
    lines.append("")

    if analysis_result['empty']:
        lines.append("Пропускаемые колонки:")
        for col in analysis_result['empty']:
            stats = analysis_result['stats'][col]
            lines.append(f"  {col}: {stats['null_pct']:.1f}% NULL, {stats['unique_count']} unique")

    lines.append("")
    lines.append("Колонки для загрузки:")
    for col in analysis_result['keep']:
        stats = analysis_result['stats'][col]
        lines.append(f"  {col}: {stats['non_null']}/{stats['total']} non-null, {stats['unique_count']} unique")

    return "\n".join(lines)
