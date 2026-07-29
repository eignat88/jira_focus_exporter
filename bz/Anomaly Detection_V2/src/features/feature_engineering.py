"""
Feature Engineering для WMS Anomaly Detection
Создаёт 6 признаков для ML-моделей из mart данных.

Признаки:
1. scan_interval - средний интервал между операциями (сек)
2. error_streak - доля операций с потерями (waste_rate)
3. scans_per_hour - количество операций в час
4. error_rate - доля потерянных единиц
5. competitor_ratio - доля конкурентных маркировочных кодов
6. time_since_last_scan - дней с последней операции
"""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from datetime import datetime, timedelta
import os


# Database connection - environment-aware
IS_DOCKER = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == '1'
DB_HOST = os.environ.get('DB_HOST', 'host.docker.internal' if IS_DOCKER else 'localhost')
DB_CONN = f"postgresql://postgres:123@{DB_HOST}:5432/wms_analysis"

# Маппинг operation_type (из WMS_ManualOperationTable)
OPERATION_TYPE_MAP = {
    '0': 'Не определено',
    '1': 'Регистрация',
    '2': 'Печать этикеток',
    '3': 'Комплектация',
    '4': 'Проверка/контроль',
    '6': 'Сканирование',
    '7': 'Упаковка',
    '9': 'Отгрузка',
    '10': 'Пополнение',
    '14': 'Маркировка',
    '16': 'Перемещение',
    '37': 'Приемка',
    '45': 'Инвентаризация',
    '60': 'Возврат'
}


def get_connection():
    return create_engine(DB_CONN)


def load_data():
    """Загрузка данных из mart."""
    engine = get_connection()
    
    daily_ops = pd.read_sql(
        "SELECT * FROM mart.daily_operations WHERE operation_date >= '2020-01-01'",
        engine
    )
    
    picking = pd.read_sql(
        "SELECT * FROM mart.picking_efficiency",
        engine
    )
    
    marking = pd.read_sql(
        "SELECT * FROM mart.marking_statistics",
        engine
    )
    
    return daily_ops, picking, marking


def create_employee_features(daily_ops: pd.DataFrame) -> pd.DataFrame:
    """
    Признаки на уровне сотрудника.
    
    1. scan_interval - средний интервал между операциями (сек)
    2. scans_per_hour - количество операций в час
    3. time_since_last_scan - дней с последней операции
    """
    df = daily_ops.copy()
    
    # Группировка по сотруднику
    employee_agg = df.groupby(['employee_id', 'employee_name']).agg(
        total_operations=('total_operations', 'sum'),
        total_duration=('total_duration', 'sum'),
        avg_duration=('avg_duration', 'mean'),
        days_active=('operation_date', 'nunique'),
        first_operation=('operation_date', 'min'),
        last_operation=('operation_date', 'max'),
        operation_count=('total_operations', 'count')
    ).reset_index()
    
    # 1. scan_interval - средний интервал между операциями (сек)
    # Формула: (часы_работы * 3600) / кол_операций
    # Если N операций за 8 часов, интервал = (8*3600)/N сек
    working_hours_per_day = 8
    employee_agg['total_hours'] = employee_agg['days_active'] * working_hours_per_day
    employee_agg['scan_interval'] = np.where(
        employee_agg['total_operations'] > 0,
        (employee_agg['total_hours'] * 3600) / employee_agg['total_operations'],
        0
    )
    
    # 2. scans_per_hour - операций в час
    employee_agg['scans_per_hour'] = np.where(
        employee_agg['total_hours'] > 0,
        employee_agg['total_operations'] / employee_agg['total_hours'],
        0
    )
    
    # 3. time_since_last_scan - дней с последней операции
    reference_date = pd.Timestamp('2025-08-05')  # Максимальная дата в данных
    employee_agg['last_operation'] = pd.to_datetime(employee_agg['last_operation'])
    employee_agg['time_since_last_scan'] = (
        reference_date - employee_agg['last_operation']
    ).dt.days
    
    return employee_agg


def create_route_features(picking: pd.DataFrame) -> pd.DataFrame:
    """
    Признаки на уровне маршрута сборки.
    
    4. error_rate - доля потерянных единиц (waste_rate)
    """
    df = picking.copy()
    
    # Группировка по маршруту
    route_agg = df.groupby('picking_route_id').agg(
        total_picked=('total_picked', 'sum'),
        total_waste=('total_waste', 'sum'),
        operation_count=('operation_count', 'sum'),
        item_count=('item_id', 'nunique'),
        avg_waste_rate=('waste_rate', 'mean')
    ).reset_index()
    
    # 4. error_rate - доля потерянных единиц
    route_agg['error_rate'] = np.where(
        route_agg['total_picked'] > 0,
        route_agg['total_waste'] / route_agg['total_picked'],
        0
    )
    
    # Дополнительные признаки для анализа
    route_agg['picked_per_operation'] = np.where(
        route_agg['operation_count'] > 0,
        route_agg['total_picked'] / route_agg['operation_count'],
        0
    )
    
    return route_agg


def create_item_features(picking: pd.DataFrame, marking: pd.DataFrame) -> pd.DataFrame:
    """
    Признаки на уровне товара.
    
    5. competitor_ratio - доля конкурентных маркировочных кодов
    """
    # Агрегация по товару из picking
    item_picking = picking.groupby('item_id').agg(
        total_picked=('total_picked', 'sum'),
        total_waste=('total_waste', 'sum'),
        route_count=('picking_route_id', 'nunique'),
        avg_waste_rate=('waste_rate', 'mean')
    ).reset_index()
    
    item_picking['error_rate'] = np.where(
        item_picking['total_picked'] > 0,
        item_picking['total_waste'] / item_picking['total_picked'],
        0
    )
    
    # Агрегация по товару из marking
    if not marking.empty:
        item_marking = marking.groupby('item_id').agg(
            mark_usage_count=('usage_count', 'sum'),
            mark_total_picked=('total_picked', 'sum'),
            unique_marks=('mark_code', 'nunique')
        ).reset_index()
        
        # 5. competitor_ratio - доля конкурентных кодов
        # Конкурентные коды = коды с низким использованием
        item_marking['competitor_ratio'] = np.where(
            item_marking['mark_total_picked'] > 0,
            item_marking['mark_usage_count'] / item_marking['mark_total_picked'],
            0
        )
        
        # Объединение
        item_features = item_picking.merge(item_marking, on='item_id', how='left')
    else:
        item_features = item_picking
        item_features['competitor_ratio'] = 0
        item_features['unique_marks'] = 0
    
    item_features = item_features.fillna(0)
    
    return item_features


def create_anomaly_labels(employee_features: pd.DataFrame, 
                          route_features: pd.DataFrame,
                          item_features: pd.DataFrame) -> dict:
    """
    Создание меток аномалий на основе правил.
    """
    labels = {}
    
    # Аномалии сотрудников
    emp_labels = employee_features.copy()
    emp_labels['is_anomaly'] = 0
    
    # Слишком быстрое сканирование (< 2 сек)
    emp_labels.loc[emp_labels['scan_interval'] < 2, 'is_anomaly'] = 1
    
    # Слишком медленное сканирование (> 300 сек)
    emp_labels.loc[emp_labels['scan_interval'] > 300, 'is_anomaly'] = 1
    
    # Нет активности > 30 дней
    emp_labels.loc[emp_labels['time_since_last_scan'] > 30, 'is_anomaly'] = 1
    
    labels['employee'] = emp_labels
    
    # Аномалии маршрутов
    route_labels = route_features.copy()
    route_labels['is_anomaly'] = 0
    
    # Высокий error_rate (> 30%)
    route_labels.loc[route_labels['error_rate'] > 0.3, 'is_anomaly'] = 1
    
    labels['route'] = route_labels
    
    # Аномалии товаров
    item_labels = item_features.copy()
    item_labels['is_anomaly'] = 0
    
    # Высокий error_rate (> 50%)
    item_labels.loc[item_labels['error_rate'] > 0.5, 'is_anomaly'] = 1
    
    labels['item'] = item_labels
    
    return labels


def save_features(employee_features: pd.DataFrame,
                  route_features: pd.DataFrame,
                  item_features: pd.DataFrame,
                  labels: dict):
    """Сохранение признаков в mart."""
    engine = get_connection()
    
    # Сохранение признаков сотрудников
    employee_features.to_sql(
        'feature_employee',
        engine,
        schema='mart',
        if_exists='replace',
        index=False
    )
    
    # Сохранение признаков маршрутов
    route_features.to_sql(
        'feature_route',
        engine,
        schema='mart',
        if_exists='replace',
        index=False
    )
    
    # Сохранение признаков товаров
    item_features.to_sql(
        'feature_item',
        engine,
        schema='mart',
        if_exists='replace',
        index=False
    )
    
    # Сохранение меток аномалий
    for name, df in labels.items():
        df.to_sql(
            f'label_{name}',
            engine,
            schema='mart',
            if_exists='replace',
            index=False
        )
    
    print("Features saved to mart schema")


def run_feature_engineering():
    """Основной пайплайн feature engineering."""
    print("Loading data...")
    daily_ops, picking, marking = load_data()
    
    print(f"  daily_operations: {len(daily_ops)} rows")
    print(f"  picking_efficiency: {len(picking)} rows")
    print(f"  marking_statistics: {len(marking)} rows")
    
    print("\nCreating employee features...")
    employee_features = create_employee_features(daily_ops)
    print(f"  {len(employee_features)} employees")
    print(f"  Features: scan_interval, scans_per_hour, time_since_last_scan")
    
    print("\nCreating route features...")
    route_features = create_route_features(picking)
    print(f"  {len(route_features)} routes")
    print(f"  Features: error_rate")
    
    print("\nCreating item features...")
    item_features = create_item_features(picking, marking)
    print(f"  {len(item_features)} items")
    print(f"  Features: competitor_ratio")
    
    print("\nCreating anomaly labels...")
    labels = create_anomaly_labels(employee_features, route_features, item_features)
    for name, df in labels.items():
        anomaly_count = df['is_anomaly'].sum()
        print(f"  {name}: {anomaly_count} anomalies ({anomaly_count/len(df)*100:.1f}%)")
    
    print("\nSaving features...")
    save_features(employee_features, route_features, item_features, labels)
    
    print("\nFeature Engineering complete!")
    
    return employee_features, route_features, item_features, labels


if __name__ == "__main__":
    run_feature_engineering()
