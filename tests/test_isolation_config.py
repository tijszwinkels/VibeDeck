"""Tests for isolation and auth config sections."""

from pathlib import Path

import pytest

from vibedeck.config import Config, IsolationConfig, AuthConfig, load_config


class TestIsolationConfig:
    """Test IsolationConfig dataclass."""

    def test_defaults(self):
        """Should have sensible defaults."""
        config = IsolationConfig()
        assert config.users_dir == ""
        assert config.docker_image == "claude-sandbox"
        assert config.docker_runtime == "runsc"
        assert config.memory == "2g"
        assert config.cpus == "1"
        assert config.env_file is None

    def test_custom_values(self):
        """Should accept custom values."""
        config = IsolationConfig(
            users_dir="/opt/users",
            docker_image="my-sandbox",
            docker_runtime="runc",
            memory="4g",
            cpus="2",
            env_file="/opt/.env",
        )
        assert config.users_dir == "/opt/users"
        assert config.docker_image == "my-sandbox"


class TestAuthConfig:
    """Test AuthConfig dataclass."""

    def test_defaults(self):
        """Should have sensible defaults."""
        config = AuthConfig()
        assert config.client_id == ""
        assert config.client_secret == ""
        assert config.scope == "openid profile email"
        assert config.id_claim == "sub"
        assert config.session_secret == ""

    def test_is_enabled_when_client_id_set(self):
        """Auth should be considered enabled when client_id is non-empty."""
        config = AuthConfig(client_id="my-client")
        assert bool(config.client_id) is True

    def test_is_disabled_when_empty(self):
        """Auth should be considered disabled when client_id is empty."""
        config = AuthConfig()
        assert bool(config.client_id) is False


class TestConfigWithIsolation:
    """Test Config loading with isolation and auth sections."""

    def test_config_has_isolation_section(self):
        """Config should have isolation field."""
        config = Config()
        assert isinstance(config.isolation, IsolationConfig)

    def test_config_has_auth_section(self):
        """Config should have auth field."""
        config = Config()
        assert isinstance(config.auth, AuthConfig)

    def test_loads_isolation_from_toml(self, tmp_path):
        """Should load [isolation] section from TOML file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[isolation]
users_dir = "/opt/vibedeck/users"
docker_image = "my-sandbox"
docker_runtime = "runc"
memory = "4g"
cpus = "2"
env_file = "/opt/.env"
""")
        config = load_config(config_paths=[config_file])
        assert config.isolation.users_dir == "/opt/vibedeck/users"
        assert config.isolation.docker_image == "my-sandbox"
        assert config.isolation.docker_runtime == "runc"
        assert config.isolation.memory == "4g"
        assert config.isolation.cpus == "2"
        assert config.isolation.env_file == "/opt/.env"

    def test_loads_auth_from_toml(self, tmp_path):
        """Should load [auth] section from TOML file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[auth]
client_id = "my-github-client"
client_secret = "gh-secret-123"
authorize_url = "https://github.com/login/oauth/authorize"
token_url = "https://github.com/login/oauth/access_token"
userinfo_url = "https://api.github.com/user"
scope = "user:email"
id_claim = "id"
session_secret = "super-secret"
""")
        config = load_config(config_paths=[config_file])
        assert config.auth.client_id == "my-github-client"
        assert config.auth.client_secret == "gh-secret-123"
        assert config.auth.authorize_url == "https://github.com/login/oauth/authorize"
        assert config.auth.scope == "user:email"
        assert config.auth.id_claim == "id"
        assert config.auth.session_secret == "super-secret"

    def test_loads_oidc_server_metadata_url(self, tmp_path):
        """Should support server_metadata_url for OIDC discovery."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[auth]
client_id = "vibedeck"
client_secret = "kc-secret"
server_metadata_url = "https://keycloak.example.com/realms/myrealm/.well-known/openid-configuration"
session_secret = "secret"
""")
        config = load_config(config_paths=[config_file])
        assert config.auth.server_metadata_url == "https://keycloak.example.com/realms/myrealm/.well-known/openid-configuration"
        # Individual URLs should be None by default
        assert config.auth.authorize_url is None

    def test_isolation_backend_in_serve_config(self, tmp_path):
        """Should allow backend = 'isolation' in serve config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[serve]
backend = "isolation"
""")
        config = load_config(config_paths=[config_file])
        assert config.serve.backend == "isolation"

    def test_missing_sections_use_defaults(self, tmp_path):
        """Config without isolation/auth sections should use defaults."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[serve]\nport = 9000")
        config = load_config(config_paths=[config_file])
        assert config.isolation.users_dir == ""
        assert config.auth.client_id == ""
