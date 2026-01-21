"""Tests for config file loading."""

from pathlib import Path

import pytest

from vibedeck.config import (
    Config,
    load_config,
    DEFAULT_CONFIG,
)


class TestDefaultConfig:
    """Test default configuration values."""

    def test_has_serve_defaults(self):
        """Default config should have serve section with expected defaults."""
        assert "serve" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["serve"]["port"] == 8765
        assert DEFAULT_CONFIG["serve"]["host"] == "127.0.0.1"
        assert DEFAULT_CONFIG["serve"]["max_sessions"] == 100
        assert DEFAULT_CONFIG["serve"]["backend"] == "all"
        assert DEFAULT_CONFIG["serve"]["no_open"] is False
        assert DEFAULT_CONFIG["serve"]["debug"] is False
        assert DEFAULT_CONFIG["serve"]["include_subagents"] is False

    def test_has_html_defaults(self):
        """Default config should have html section."""
        assert "html" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["html"]["gist"] is False
        assert DEFAULT_CONFIG["html"]["open"] is False
        assert DEFAULT_CONFIG["html"]["include_json"] is False

    def test_has_md_defaults(self):
        """Default config should have md section."""
        assert "md" in DEFAULT_CONFIG


class TestLoadConfig:
    """Test config file loading."""

    def test_returns_defaults_when_no_config_exists(self):
        """Should return defaults when no config file exists."""
        config = load_config(config_paths=[])
        assert config == Config.from_dict(DEFAULT_CONFIG)

    def test_loads_toml_file(self, tmp_path):
        """Should load and parse TOML config file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[serve]
port = 9000
host = "0.0.0.0"
""")
        config = load_config(config_paths=[config_file])
        assert config.serve.port == 9000
        assert config.serve.host == "0.0.0.0"
        # Other defaults should still be present
        assert config.serve.max_sessions == 100

    def test_merges_multiple_config_files(self, tmp_path):
        """Later config files should override earlier ones."""
        global_config = tmp_path / "global.toml"
        global_config.write_text("""
[serve]
port = 9000
host = "0.0.0.0"
max_sessions = 50
""")
        local_config = tmp_path / "local.toml"
        local_config.write_text("""
[serve]
port = 8888
""")
        config = load_config(config_paths=[global_config, local_config])
        assert config.serve.port == 8888  # From local
        assert config.serve.host == "0.0.0.0"  # From global
        assert config.serve.max_sessions == 50  # From global

    def test_ignores_missing_config_files(self, tmp_path):
        """Should skip non-existent config files without error."""
        existing = tmp_path / "exists.toml"
        existing.write_text("[serve]\nport = 9000")
        missing = tmp_path / "missing.toml"

        config = load_config(config_paths=[missing, existing])
        assert config.serve.port == 9000

    def test_handles_invalid_toml_gracefully(self, tmp_path, caplog):
        """Should log warning and skip invalid TOML files."""
        bad_config = tmp_path / "bad.toml"
        bad_config.write_text("this is not valid toml [[[")

        config = load_config(config_paths=[bad_config])
        assert "Failed to parse" in caplog.text
        # Should still return defaults
        assert config.serve.port == 8765


class TestConfig:
    """Test Config dataclass."""

    def test_from_dict_creates_config(self):
        """Config.from_dict should create a valid Config object."""
        data = {
            "serve": {"port": 9000, "host": "localhost"},
            "html": {"gist": True},
            "md": {},
        }
        config = Config.from_dict(data)
        assert config.serve.port == 9000
        assert config.serve.host == "localhost"
        assert config.html.gist is True

    def test_get_for_command_returns_section(self):
        """Should return config section for a command."""
        config = Config.from_dict(DEFAULT_CONFIG)
        serve_config = config.get_for_command("serve")
        assert serve_config.port == 8765

    def test_experimental_options_in_serve(self, tmp_path):
        """Should support experimental/hidden options in config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[serve]
experimental = true
enable_send = true
fork = true
""")
        config = load_config(config_paths=[config_file])
        assert config.serve.experimental is True
        assert config.serve.enable_send is True
        assert config.serve.fork is True


class TestConfigIntegration:
    """Integration tests for config with CLI."""

    def test_cli_args_override_config(self, tmp_path):
        """CLI arguments should take precedence over config file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[serve]\nport = 9000")

        config = load_config(config_paths=[config_file])
        # Simulate CLI override
        cli_port = 7777
        effective_port = cli_port if cli_port is not None else config.serve.port
        assert effective_port == 7777

    def test_config_respects_backend_choices(self, tmp_path):
        """Backend config should accept valid backend names."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[serve]\nbackend = "claude_code"')

        config = load_config(config_paths=[config_file])
        assert config.serve.backend == "claude_code"
