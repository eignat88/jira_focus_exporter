"""
Скрипт для обучения модели Isolation Forest для обнаружения аномалий в WMS
Обучает модели на трех наборах данных: сотрудники, маршруты, товары
Сохраняет предсказания и метрики в PostgreSQL
"""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix, precision_score, recall_score, f1_score
import warnings
import json
from datetime import datetime

warnings.filterwarnings('ignore')


class IsolationForestTrainer:
    """Класс для обучения модели Isolation Forest"""
    
    def __init__(self, db_config):
        """
        Инициализация тренера
        
        Args:
            db_config: словарь с параметрами подключения к БД
        """
        self.db_config = db_config
        self.engine = self._create_engine()
        self.models = {}
        self.scalers = {}
        self.results = {}
        
    def _create_engine(self):
        """Создание движка SQLAlchemy для подключения к PostgreSQL"""
        conn_str = (
            f"postgresql://{self.db_config['user']}:{self.db_config['password']}"
            f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
        )
        return create_engine(conn_str)
    
    def load_data(self, table_name):
        """
        Загрузка данных из таблицы mart schema
        
        Args:
            table_name: имя таблицы
            
        Returns:
            DataFrame с данными
        """
        query = f"SELECT * FROM mart.{table_name}"
        df = pd.read_sql(query, self.engine)
        print(f"Загружено {len(df)} строк из mart.{table_name}")
        return df
    
    def load_labels(self):
        """
        загрузка размеченных данных (меток аномалий)
        
        Returns:
            DataFrame с метками
        """
        query = "SELECT employee_id, is_anomaly FROM mart.label_employee"
        df = pd.read_sql(query, self.engine)
        print(f"Загружено {len(df)} меток аномалий")
        return df
    
    def prepare_features_employee(self, df):
        """
        Подготовка признаков для анализа сотрудников
        
        Args:
            df: исходный DataFrame
            
        Returns:
            X: матрица признаков
            feature_cols: список имен признаков
        """
        feature_cols = ['scan_interval', 'scans_per_hour', 'time_since_last_scan']
        
        # Выбор признаков и обработка пропусков
        X = df[feature_cols].copy()
        X = X.fillna(0)  # Заполняем NaN нулями
        
        return X, feature_cols
    
    def prepare_features_route(self, df):
        """
        Подготовка признаков для анализа маршрутов
        
        Args:
            df: исходный DataFrame
            
        Returns:
            X: матрица признаков
            feature_cols: список имен признаков
        """
        feature_cols = ['error_rate', 'picked_per_operation', 'avg_waste_rate']
        
        # Выбор признаков и обработка пропусков
        X = df[feature_cols].copy()
        X = X.fillna(0)
        
        # Удаление бесконечных значений
        X = X.replace([np.inf, -np.inf], 0)
        
        return X, feature_cols
    
    def prepare_features_item(self, df):
        """
        Подготовка признаков для анализа товаров
        
        Args:
            df: исходный DataFrame
            
        Returns:
            X: матрица признаков
            feature_cols: список имен признаков
        """
        # Используем признаки с реальными данными
        feature_cols = ['total_picked', 'route_count', 'total_waste']
        
        # Выбор признаков и обработка пропусков
        X = df[feature_cols].copy()
        X = X.fillna(0)
        
        # Удаление бесконечных значений
        X = X.replace([np.inf, -np.inf], 0)
        
        return X, feature_cols
    
    def train_isolation_forest(self, X, entity_type, contamination=0.1):
        """
        Обучение модели Isolation Forest
        
        Args:
            X: матрица признаков
            entity_type: тип сущности ('employee', 'route', 'item')
            contamination: ожидаемая доля аномалий
            
        Returns:
            model: обученная модель
            scaler: объект масштабирования
            y_pred: предсказания (-1 = аномалия, 1 = норма)
            anomaly_score: оценки аномальности
        """
        print(f"\nОбучение модели для {entity_type}...")
        print(f"Размер данных: {X.shape}")
        print(f"Contamination: {contamination}")
        
        # Масштабирование признаков
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        # Обучение модели Isolation Forest
        iso_forest = IsolationForest(
            contamination=contamination,
            n_estimators=100,
            random_state=42,
            n_jobs=-1,
            verbose=0
        )
        
        # Предсказание аномалий
        y_pred = iso_forest.fit_predict(X_scaled)
        
        # Получение оценок аномальности (чем ниже, тем аномальнее)
        anomaly_score = iso_forest.decision_function(X_scaled)
        
        print(f"Обнаружено аномалий: {(y_pred == -1).sum()} из {len(y_pred)}")
        
        return iso_forest, scaler, y_pred, anomaly_score
    
    def evaluate_model(self, y_true, y_pred, entity_type):
        """
        Оценка производительности модели
        
        Args:
            y_true: истинные метки
            y_pred: предсказания модели
            entity_type: тип сущности
            
        Returns:
            metrics: словарь с метриками
        """
        # Преобразование предсказаний: -1 -> 1 (аномалия), 1 -> 0 (норма)
        y_pred_binary = (y_pred == -1).astype(int)
        
        # Вычисление метрик
        precision = precision_score(y_true, y_pred_binary, zero_division=0)
        recall = recall_score(y_true, y_pred_binary, zero_division=0)
        f1 = f1_score(y_true, y_pred_binary, zero_division=0)
        
        # Матрица ошибок
        cm = confusion_matrix(y_true, y_pred_binary)
        
        metrics = {
            'entity_type': entity_type,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'anomaly_count': int(y_pred_binary.sum()),
            'total_count': len(y_true),
            'confusion_matrix': cm.tolist()
        }
        
        print(f"\nМетрики для {entity_type}:")
        print(f"Precision: {precision:.4f}")
        print(f"Recall: {recall:.4f}")
        print(f"F1-Score: {f1:.4f}")
        print(f"Аномалий: {metrics['anomaly_count']} из {metrics['total_count']}")
        
        return metrics
    
    def save_predictions_to_db(self, entity_ids, entity_type, y_pred, anomaly_score, features):
        """
        Сохранение предсказаний в PostgreSQL
        
        Args:
            entity_ids: идентификаторы сущностей
            entity_type: тип сущности
            y_pred: предсказания модели
            anomaly_score: оценки аномальности
            features: DataFrame с признаками
        """
        # Преобразование предсказаний
        y_pred_binary = (y_pred == -1).astype(int)
        
        # Создание DataFrame для сохранения
        predictions_df = pd.DataFrame({
            'entity_id': entity_ids,
            'entity_type': entity_type,
            'is_anomaly_predicted': y_pred_binary,
            'anomaly_score': anomaly_score,
            'feature_1': features.iloc[:, 0] if len(features.columns) > 0 else 0,
            'feature_2': features.iloc[:, 1] if len(features.columns) > 1 else 0,
            'feature_3': features.iloc[:, 2] if len(features.columns) > 2 else 0,
            'created_at': datetime.now()
        })
        
        # Сохранение в БД
        predictions_df.to_sql(
            'model_isolation_forest_predictions',
            self.engine,
            schema='mart',
            if_exists='append',
            index=False
        )
        
        print(f"Сохранено {len(predictions_df)} предсказаний для {entity_type}")
    
    def save_metrics_to_db(self, metrics):
        """
        Сохранение метрик в PostgreSQL
        
        Args:
            metrics: словарь с метриками
        """
        metrics_df = pd.DataFrame([{
            'model_name': 'IsolationForest',
            'entity_type': metrics['entity_type'],
            'precision_score': metrics['precision'],
            'recall_score': metrics['recall'],
            'f1_score': metrics['f1'],
            'anomaly_count': metrics['anomaly_count'],
            'total_count': metrics['total_count'],
            'created_at': datetime.now()
        }])
        
        metrics_df.to_sql(
            'model_metrics',
            self.engine,
            schema='mart',
            if_exists='append',
            index=False
        )
        
        print(f"Сохранены метрики для {metrics['entity_type']}")
    
    def run_employee_analysis(self):
        """Полный цикл анализа сотрудников"""
        print("\n" + "="*60)
        print("АНАЛИЗ СОТРУДНИКОВ")
        print("="*60)
        
        # Загрузка данных
        df = self.load_data('feature_employee')
        labels = self.load_labels()
        
        # Подготовка признаков
        X, feature_cols = self.prepare_features_employee(df)
        
        # Обучение модели
        model, scaler, y_pred, anomaly_score = self.train_isolation_forest(
            X, 'employee', contamination=0.1
        )
        
        # Сохранение модели и скейлера
        self.models['employee'] = model
        self.scalers['employee'] = scaler
        
        # Оценка модели (если есть метки)
        if len(labels) > 0:
            # Удаление дубликатов в метках (оставляем первую запись)
            labels_unique = labels.drop_duplicates(subset='employee_id', keep='first')
            
            # Объединение данных
            df_merged = df.merge(labels_unique, left_on='employee_id', right_on='employee_id', how='left')
            y_true = df_merged['is_anomaly'].fillna(0).astype(int).values
            
            metrics = self.evaluate_model(y_true, y_pred, 'employee')
            self.results['employee'] = metrics
            
            # Сохранение метрик
            self.save_metrics_to_db(metrics)
        
        # Сохранение предсказаний
        self.save_predictions_to_db(
            df['employee_id'].astype(str),
            'employee',
            y_pred,
            anomaly_score,
            X
        )
        
        return y_pred, anomaly_score
    
    def run_route_analysis(self):
        """Полный цикл анализа маршрутов"""
        print("\n" + "="*60)
        print("АНАЛИЗ МАРШРУТОВ")
        print("="*60)
        
        # Загрузка данных
        df = self.load_data('feature_route')
        
        # Подготовка признаков
        X, feature_cols = self.prepare_features_route(df)
        
        # Обучение модели
        model, scaler, y_pred, anomaly_score = self.train_isolation_forest(
            X, 'route', contamination=0.1
        )
        
        # Сохранение модели и скейлера
        self.models['route'] = model
        self.scalers['route'] = scaler
        
        # Оценка модели (без меток - используем статистику)
        y_pred_binary = (y_pred == -1).astype(int)
        metrics = {
            'entity_type': 'route',
            'precision': 0.0,  # Нет меток для оценки
            'recall': 0.0,
            'f1': 0.0,
            'anomaly_count': int(y_pred_binary.sum()),
            'total_count': len(df),
            'confusion_matrix': [[0, 0], [0, 0]]
        }
        self.results['route'] = metrics
        self.save_metrics_to_db(metrics)
        
        # Сохранение предсказаний
        self.save_predictions_to_db(
            df['picking_route_id'].astype(str),
            'route',
            y_pred,
            anomaly_score,
            X
        )
        
        return y_pred, anomaly_score
    
    def run_item_analysis(self):
        """Полный цикл анализа товаров"""
        print("\n" + "="*60)
        print("АНАЛИЗ ТОВАРОВ")
        print("="*60)
        
        # Загрузка данных
        df = self.load_data('feature_item')
        
        # Подготовка признаков
        X, feature_cols = self.prepare_features_item(df)
        
        # Обучение модели
        model, scaler, y_pred, anomaly_score = self.train_isolation_forest(
            X, 'item', contamination=0.1
        )
        
        # Сохранение модели и скейлера
        self.models['item'] = model
        self.scalers['item'] = scaler
        
        # Оценка модели (без меток - используем статистику)
        y_pred_binary = (y_pred == -1).astype(int)
        metrics = {
            'entity_type': 'item',
            'precision': 0.0,  # Нет меток для оценки
            'recall': 0.0,
            'f1': 0.0,
            'anomaly_count': int(y_pred_binary.sum()),
            'total_count': len(df),
            'confusion_matrix': [[0, 0], [0, 0]]
        }
        self.results['item'] = metrics
        self.save_metrics_to_db(metrics)
        
        # Сохранение предсказаний
        self.save_predictions_to_db(
            df['item_id'].astype(str),
            'item',
            y_pred,
            anomaly_score,
            X
        )
        
        return y_pred, anomaly_score
    
    def run_full_pipeline(self):
        """Запуск полного пайплайна обучения"""
        print("="*60)
        print("ЗАПУСК ОБУЧЕНИЯ ISOLATION FOREST")
        print(f"Время начала: {datetime.now()}")
        print("="*60)
        
        # Анализ сотрудников
        self.run_employee_analysis()
        
        # Анализ маршрутов
        self.run_route_analysis()
        
        # Анализ товаров
        self.run_item_analysis()
        
        # Итоговый отчет
        self.print_summary()
        
        print("\n" + "="*60)
        print("ОБУЧЕНИЕ ЗАВЕРШЕНО")
        print(f"Время окончания: {datetime.now()}")
        print("="*60)
        
        return self.models, self.results
    
    def print_summary(self):
        """Печать итогового отчета"""
        print("\n" + "="*60)
        print("ИТОГОВЫЙ ОТЧЕТ")
        print("="*60)
        
        for entity_type, metrics in self.results.items():
            print(f"\n{entity_type.upper()}:")
            print(f"  Precision: {metrics['precision']:.4f}")
            print(f"  Recall: {metrics['recall']:.4f}")
            print(f"  F1-Score: {metrics['f1']:.4f}")
            print(f"  Аномалий: {metrics['anomaly_count']} из {metrics['total_count']} "
                  f"({metrics['anomaly_count']/metrics['total_count']*100:.2f}%)")


def main():
    """Основная функция"""
    import os
    
    # Определение окружения
    IS_DOCKER = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == '1'
    
    # Конфигурация базы данных
    db_config = {
        'host': os.environ.get('DB_HOST', 'host.docker.internal' if IS_DOCKER else 'localhost'),
        'port': 5432,
        'database': 'wms_analysis',
        'user': 'postgres',
        'password': '123'
    }
    
    print(f"Подключение к БД: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    
    # Создание тренера и запуск обучения
    trainer = IsolationForestTrainer(db_config)
    models, results = trainer.run_full_pipeline()
    
    return models, results


if __name__ == '__main__':
    main()
