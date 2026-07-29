"""
Centralized configuration for ETL pipeline.

Single source of truth for all settings:
- config.yaml: ETL structure, tables, parameters
- .env / environment: secrets and overrides
- CLI: temporary overrides for run

Priority: CLI > ENV > YAML > safe defaults
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
import yaml


# Local default password for development
LOCAL_DEFAULT_DB_PASSWORD = "123"


def _load_dotenv():
    """Load .env file if it exists (simple parser, no external dependencies)."""
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"
    
    if not env_file.exists():
        env_file = project_root / ".env"
    
    if not env_file.exists():
        return {}
    
    env_vars = {}
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                env_vars[key] = value
                if key not in os.environ:
                    os.environ[key] = value
    return env_vars


_dotenv_vars = _load_dotenv()


@dataclass(frozen=True)
class DatabaseConfig:
    """PostgreSQL connection configuration."""
    host: str = "localhost"
    port: int = 5432
    database: str = "wms_analysis"
    schema: str = "raw_ax"
    user: str = "postgres"
    password: str = ""
    password_source: str = "unknown"
    
    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class SourceConfig:
    """SQL Server source configuration."""
    server: str = "SWS-DB-T1"
    database: str = "AX63_WMS_TEST"
    driver: str = "ODBC Driver 17 for SQL Server"


@dataclass(frozen=True)
class ParallelConfig:
    """Parallel loading configuration."""
    enabled: bool = True
    workers: int = 4
    fetch_size: int = 5000
    commit_size: int = 50000
    queue_size: int = 16


@dataclass(frozen=True)
class TableConfig:
    """Configuration for a single table load."""
    name: str
    load_mode: Optional[str] = None  # Override global mode
    date_filter: Optional[str] = None
    columns: Optional[List[str]] = None
    paginate_by: Optional[str] = None
    incremental_field: Optional[str] = None
    conflict_strategy: str = "DO NOTHING"  # DO NOTHING | DO UPDATE | ERROR


@dataclass(frozen=True)
class ETLConfig:
    """ETL processing configuration."""
    batch_size: int = 100000
    query_timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    load_mode: str = "full"
    auto_exclude_columns: bool = False
    null_threshold: float = 0.95
    parallel: Optional[ParallelConfig] = None
    tables: List[TableConfig] = field(default_factory=list)


@dataclass(frozen=True)
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    log_dir: str = "logs"
    json_format: bool = True
    console_output: bool = True
    file_output: bool = True


@dataclass(frozen=True)
class Settings:
    """Application settings container."""
    environment: str = "local"
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    source: SourceConfig = field(default_factory=SourceConfig)
    etl: ETLConfig = field(default_factory=ETLConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _detect_environment() -> str:
    """Detect the current runtime environment."""
    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "1":
        return "docker"
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return "ci"
    return "local"


def _load_yaml_config() -> dict:
    """Load config.yaml from ETL package directory."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        return {}
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_database_password(environment: str) -> tuple[str, str]:
    """
    Get database password with priority:
    1. Environment variable DB_PASSWORD
    2. .env file
    3. Local default (only for local environment)
    
    Returns (password, password_source)
    """
    # Priority 1: Environment variable
    env_password = os.environ.get("DB_PASSWORD")
    if env_password:
        return env_password, "environment"
    
    # Priority 2: .env file
    dotenv_password = _dotenv_vars.get("DB_PASSWORD")
    if dotenv_password:
        return dotenv_password, ".env"
    
    # Priority 3: Local default (only for local environment)
    if environment == "local":
        return LOCAL_DEFAULT_DB_PASSWORD, "local_default"
    
    # Non-local environments require explicit password
    return "", "missing"


def load_settings() -> Settings:
    """
    Load settings with priority: ENV > YAML > defaults.
    
    Environment variables:
        DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SCHEMA
        SS_SERVER, SS_DATABASE, SS_DRIVER
        ETL_BATCH_SIZE, ETL_QUERY_TIMEOUT, ETL_LOAD_MODE
        LOG_LEVEL, LOG_DIR
    """
    env = _detect_environment()
    yaml_config = _load_yaml_config()
    
    # Get database password
    db_password, password_source = _get_database_password(env)
    
    # Validate password for non-local environments
    if not db_password and env != "local":
        raise ValueError(
            "DB_PASSWORD must be provided for non-local environment. "
            "Set DB_PASSWORD environment variable or add it to .env file."
        )
    
    # Database config: ENV overrides YAML
    db_config = DatabaseConfig(
        host=os.environ.get("DB_HOST", yaml_config.get("target", {}).get("host", "localhost")),
        port=int(os.environ.get("DB_PORT", yaml_config.get("target", {}).get("port", 5432))),
        database=os.environ.get("DB_NAME", yaml_config.get("target", {}).get("database", "wms_analysis")),
        schema=os.environ.get("DB_SCHEMA", yaml_config.get("target", {}).get("schema", "raw_ax")),
        user=os.environ.get("DB_USER", yaml_config.get("target", {}).get("user", "postgres")),
        password=db_password,
        password_source=password_source,
    )
    
    # Source config: ENV overrides YAML
    source_config = SourceConfig(
        server=os.environ.get("SS_SERVER", yaml_config.get("source", {}).get("server", "SWS-DB-T1")),
        database=os.environ.get("SS_DATABASE", yaml_config.get("source", {}).get("database", "AX63_WMS_TEST")),
        driver=os.environ.get("SS_DRIVER", yaml_config.get("source", {}).get("driver", "ODBC Driver 17 for SQL Server")),
    )
    
    # ETL config: ENV overrides YAML
    yaml_etl = yaml_config.get("etl", {})
    parallel_yaml = yaml_etl.get("parallel", {})
    
    parallel_config = ParallelConfig(
        enabled=os.environ.get("ETL_PARALLEL_ENABLED", parallel_yaml.get("enabled", True)),
        workers=int(os.environ.get("ETL_PARALLEL_WORKERS", parallel_yaml.get("workers", 4))),
        fetch_size=int(os.environ.get("ETL_PARALLEL_FETCH_SIZE", parallel_yaml.get("fetch_size", 5000))),
        commit_size=int(os.environ.get("ETL_PARALLEL_COMMIT_SIZE", parallel_yaml.get("commit_size", 50000))),
        queue_size=int(os.environ.get("ETL_PARALLEL_QUEUE_SIZE", parallel_yaml.get("queue_size", 16))),
    )
    
    # Parse tables from YAML
    tables_yaml = yaml_config.get("tables", [])
    tables = []
    for t in tables_yaml:
        if isinstance(t, str):
            tables.append(TableConfig(name=t))
        elif isinstance(t, dict):
            tables.append(TableConfig(
                name=t["name"],
                load_mode=t.get("load_mode"),
                date_filter=t.get("date_filter"),
                columns=t.get("columns"),
                paginate_by=t.get("paginate_by"),
                incremental_field=t.get("incremental_field"),
                conflict_strategy=t.get("conflict_strategy", "DO NOTHING"),
            ))
    
    etl_config = ETLConfig(
        batch_size=int(os.environ.get("ETL_BATCH_SIZE", yaml_etl.get("batch_size", 100000))),
        query_timeout=int(os.environ.get("ETL_QUERY_TIMEOUT", yaml_etl.get("query_timeout", 30))),
        max_retries=int(os.environ.get("ETL_MAX_RETRIES", yaml_etl.get("max_retries", 3))),
        retry_delay=float(os.environ.get("ETL_RETRY_DELAY", yaml_etl.get("retry_delay", 1.0))),
        load_mode=os.environ.get("ETL_LOAD_MODE", yaml_etl.get("load_mode", "full")),
        auto_exclude_columns=os.environ.get("ETL_AUTO_EXCLUDE_COLUMNS", yaml_etl.get("auto_exclude_columns", False)),
        null_threshold=float(os.environ.get("ETL_NULL_THRESHOLD", yaml_etl.get("null_threshold", 0.95))),
        parallel=parallel_config,
        tables=tables,
    )
    
    logging_config = LoggingConfig(
        level=os.environ.get("LOG_LEVEL", yaml_etl.get("logging", {}).get("level", "INFO")),
        log_dir=os.environ.get("LOG_DIR", yaml_etl.get("logging", {}).get("log_dir", "logs")),
        json_format=os.environ.get("LOG_JSON", yaml_etl.get("logging", {}).get("json_format", True)),
        console_output=os.environ.get("LOG_CONSOLE", yaml_etl.get("logging", {}).get("console_output", True)),
        file_output=os.environ.get("LOG_FILE", yaml_etl.get("logging", {}).get("file_output", True)),
    )
    
    return Settings(
        environment=env,
        db=db_config,
        source=source_config,
        etl=etl_config,
        logging=logging_config,
    )


# Module-level singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get cached settings instance (lazy-loaded)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
