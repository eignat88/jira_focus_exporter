"""Unit tests for configuration settings."""

import os
import pytest
from unittest.mock import patch

# Add ETL module to path
import sys
import os
etl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ax_to_postgres_etl')
sys.path.insert(0, etl_dir)


class TestDatabaseConfig:
    """Tests for DatabaseConfig password handling."""
    
    def test_password_source_environment(self):
        """Scenario 1: Password from environment variable."""
        from ax_to_postgres_etl.configs.settings import _get_database_password
        
        with patch.dict(os.environ, {"DB_PASSWORD": "env_password"}):
            password, source = _get_database_password("local")
            assert password == "env_password"
            assert source == "environment"
    
    def test_password_source_dotenv(self):
        """Scenario 2: Password from .env file."""
        from ax_to_postgres_etl.configs.settings import _dotenv_vars
        
        # Simulate .env file content
        with patch.dict(_dotenv_vars, {"DB_PASSWORD": "dotenv_password"}):
            with patch.dict(os.environ, {}, clear=True):
                # Remove DB_PASSWORD from env
                os.environ.pop("DB_PASSWORD", None)
                from ax_to_postgres_etl.configs.settings import _get_database_password
                password, source = _get_database_password("local")
                assert password == "dotenv_password"
                assert source == ".env"
    
    def test_password_source_local_default(self):
        """Scenario 3: Local default when no ENV and no .env."""
        from ax_to_postgres_etl.configs.settings import _get_database_password, LOCAL_DEFAULT_DB_PASSWORD
        
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DB_PASSWORD", None)
            password, source = _get_database_password("local")
            assert password == LOCAL_DEFAULT_DB_PASSWORD
            assert source == "local_default"
    
    def test_password_missing_ci_environment(self):
        """Scenario 4: CI without password raises error."""
        from ax_to_postgres_etl.configs.settings import _get_database_password
        
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DB_PASSWORD", None)
            password, source = _get_database_password("ci")
            assert password == ""
            assert source == "missing"
    
    def test_password_missing_docker_environment(self):
        """Scenario 5: Docker without password raises error."""
        from ax_to_postgres_etl.configs.settings import _get_database_password
        
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DB_PASSWORD", None)
            password, source = _get_database_password("docker")
            assert password == ""
            assert source == "missing"
    
    def test_env_override_local_default(self):
        """Scenario 6: DB_PASSWORD overrides local default."""
        from ax_to_postgres_etl.configs.settings import _get_database_password
        
        with patch.dict(os.environ, {"DB_PASSWORD": "override_password"}):
            password, source = _get_database_password("local")
            assert password == "override_password"
            assert source == "environment"


class TestSettingsLoad:
    """Tests for settings loading."""
    
    def test_local_environment_loads_without_error(self):
        """Local environment should load without requiring DB_PASSWORD."""
        from ax_to_postgres_etl.configs.settings import load_settings, reset_settings
        
        reset_settings()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DB_PASSWORD", None)
            # Should not raise for local environment
            settings = load_settings()
            assert settings.environment == "local"
            assert settings.db.password == "123"  # LOCAL_DEFAULT_DB_PASSWORD
            assert settings.db.password_source == "local_default"
    
    def test_ci_environment_requires_password(self):
        """CI environment should require DB_PASSWORD."""
        from ax_to_postgres_etl.configs.settings import load_settings, reset_settings
        
        reset_settings()
        with patch.dict(os.environ, {"CI": "true"}, clear=True):
            os.environ.pop("DB_PASSWORD", None)
            with pytest.raises(ValueError, match="DB_PASSWORD must be provided"):
                load_settings()
    
    def test_docker_environment_requires_password(self):
        """Docker environment should require DB_PASSWORD."""
        from ax_to_postgres_etl.configs.settings import load_settings, reset_settings
        
        reset_settings()
        with patch.dict(os.environ, {"DOCKER_CONTAINER": "1"}, clear=True):
            os.environ.pop("DB_PASSWORD", None)
            with pytest.raises(ValueError, match="DB_PASSWORD must be provided"):
                load_settings()
    
    def test_password_source_available(self):
        """Password source should be available for logging."""
        from ax_to_postgres_etl.configs.settings import load_settings, reset_settings
        
        reset_settings()
        settings = load_settings()
        # Verify password source is available
        assert settings.db.password_source in ["environment", ".env", "local_default", "missing"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
