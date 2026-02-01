"""Unit tests for the middleware module."""
import pytest
import time
from unittest.mock import MagicMock, patch
import os

from grais.middleware import (
    get_cors_origins,
    setup_logging,
)


class TestGetCorsOrigins:
    def test_from_environment(self):
        """Test CORS origins from environment variable."""
        with patch.dict(os.environ, {"CORS_ORIGINS": "https://example.com,https://api.example.com"}):
            origins = get_cors_origins()
            assert "https://example.com" in origins
            assert "https://api.example.com" in origins

    def test_development_defaults(self):
        """Test development mode has localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "development", "CORS_ORIGINS": ""}, clear=True):
            origins = get_cors_origins()
            assert any("localhost" in origin for origin in origins)

    def test_production_no_defaults(self):
        """Test production mode has no defaults without explicit config."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "CORS_ORIGINS": ""}, clear=True):
            origins = get_cors_origins()
            assert origins == []


class TestSetupLogging:
    def test_setup_logging_text_format(self):
        """Test logging setup with text format."""
        # Should not raise
        setup_logging(level="INFO", json_format=False)

    def test_setup_logging_json_format(self):
        """Test logging setup with JSON format."""
        # Should not raise
        setup_logging(level="INFO", json_format=True)

    def test_setup_logging_levels(self):
        """Test different logging levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            setup_logging(level=level, json_format=False)
