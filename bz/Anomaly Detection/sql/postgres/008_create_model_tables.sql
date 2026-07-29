-- =====================================================
-- Создание таблиц для хранения результатов моделей
-- Isolation Forest для обнаружения аномалий
-- =====================================================

-- Таблица для хранения предсказаний модели
CREATE TABLE IF NOT EXISTS mart.model_isolation_forest_predictions (
    entity_id TEXT,
    entity_type TEXT,  -- 'employee', 'route', 'item'
    is_anomaly_predicted INT,
    anomaly_score NUMERIC,
    feature_1 NUMERIC,
    feature_2 NUMERIC,
    feature_3 NUMERIC,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Таблица для хранения метрик моделей
CREATE TABLE IF NOT EXISTS mart.model_metrics (
    model_name TEXT,
    entity_type TEXT,
    precision_score NUMERIC,
    recall_score NUMERIC,
    f1_score NUMERIC,
    anomaly_count INT,
    total_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Индексы для ускорения запросов
CREATE INDEX IF NOT EXISTS idx_predictions_entity ON mart.model_isolation_forest_predictions(entity_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_predictions_anomaly ON mart.model_isolation_forest_predictions(is_anomaly_predicted);
CREATE INDEX IF NOT EXISTS idx_metrics_model ON mart.model_metrics(model_name, entity_type);

-- Комментарии к таблицам
COMMENT ON TABLE mart.model_isolation_forest_predictions IS 'Предсказания модели Isolation Forest для обнаружения аномалий';
COMMENT ON TABLE mart.model_metrics IS 'Метрики производительности моделей машинного обучения';
