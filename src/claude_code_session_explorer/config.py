"""Configuration file support for CLI options.

Load configuration from a TOML file specified via --config.
CLI arguments always take precedence over config file values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Python 3.11+ has tomllib in stdlib, earlier versions need tomli
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)


@dataclass
class ServeConfig:
    """Configuration for the serve command."""

    port: int = 8765
    host: str = "127.0.0.1"
    no_open: bool = False
    debug: bool = False
    max_sessions: int = 100
    backend: str = "all"
    include_subagents: bool = False
    # Experimental/hidden options
    experimental: bool = False
    enable_send: bool = False
    dangerously_skip_permissions: bool = False
    fork: bool = False
    default_send_backend: str | None = None
    enable_thinking: bool = False
    # Summary options
    summary_log: str | None = None  # Path to JSONL log file
    summarize_after_idle_for: int | None = None  # Seconds of idle before re-summarizing
    idle_summary_model: str = "haiku"  # Model to use for idle summarization
    summary_after_long_running: int | None = None  # Summarize if CLI runs longer than N seconds
    summary_prompt: str | None = None  # Custom prompt template
    summary_prompt_file: str | None = None  # Path to prompt template file
    summary_log_keys: list[str] | None = None  # Keys to include in JSONL log


@dataclass
class HtmlConfig:
    """Configuration for the html export command."""

    output: str | None = None
    output_auto: bool = False
    repo: str | None = None
    gist: bool = False
    open: bool = False
    include_json: bool = False


@dataclass
class MdConfig:
    """Configuration for the md export command."""

    output: str | None = None


@dataclass
class Config:
    """Top-level configuration container."""

    serve: ServeConfig = field(default_factory=ServeConfig)
    html: HtmlConfig = field(default_factory=HtmlConfig)
    md: MdConfig = field(default_factory=MdConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create a Config from a dictionary (e.g., parsed TOML).

        Args:
            data: Dictionary with section keys (serve, html, md).

        Returns:
            Config instance with values from dict, defaults for missing.
        """
        serve_data = data.get("serve", {})
        html_data = data.get("html", {})
        md_data = data.get("md", {})

        return cls(
            serve=ServeConfig(**{k: v for k, v in serve_data.items() if hasattr(ServeConfig, k)}),
            html=HtmlConfig(**{k: v for k, v in html_data.items() if hasattr(HtmlConfig, k)}),
            md=MdConfig(**{k: v for k, v in md_data.items() if hasattr(MdConfig, k)}),
        )

    def get_for_command(self, command: str) -> ServeConfig | HtmlConfig | MdConfig:
        """Get the config section for a specific command.

        Args:
            command: Command name (serve, html, md).

        Returns:
            The config section for that command.

        Raises:
            KeyError: If command is not recognized.
        """
        sections = {
            "serve": self.serve,
            "html": self.html,
            "md": self.md,
        }
        if command not in sections:
            raise KeyError(f"Unknown command: {command}")
        return sections[command]


# Default configuration values
DEFAULT_CONFIG: dict[str, Any] = {
    "serve": {
        "port": 8765,
        "host": "127.0.0.1",
        "no_open": False,
        "debug": False,
        "max_sessions": 100,
        "backend": "all",
        "include_subagents": False,
        "experimental": False,
        "enable_send": False,
        "dangerously_skip_permissions": False,
        "fork": False,
        "default_send_backend": None,
        "enable_thinking": False,
        "summary_log": None,
        "summarize_after_idle_for": None,
        "idle_summary_model": "haiku",
        "summary_after_long_running": None,
        "summary_prompt": None,
        "summary_prompt_file": None,
        "summary_log_keys": None,
    },
    "html": {
        "output": None,
        "output_auto": False,
        "repo": None,
        "gist": False,
        "open": False,
        "include_json": False,
    },
    "md": {
        "output": None,
    },
}




def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Args:
        base: Base dictionary (lower priority).
        override: Override dictionary (higher priority).

    Returns:
        Merged dictionary with override values taking precedence.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_paths: list[Path] | None = None) -> Config:
    """Load configuration from TOML files.

    Loads and merges config files in order of priority (later files override
    earlier ones). Starts with DEFAULT_CONFIG as the base.

    Args:
        config_paths: List of paths to check. If None, uses get_config_paths().

    Returns:
        Config instance with merged values.
    """
    if config_paths is None:
        config_paths = get_config_paths()

    # Start with defaults
    merged: dict[str, Any] = _deep_merge({}, DEFAULT_CONFIG)

    # Load and merge each config file
    for path in config_paths:
        if not path.exists():
            continue

        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            merged = _deep_merge(merged, data)
            logger.debug(f"Loaded config from {path}")
        except Exception as e:
            logger.warning(f"Failed to parse config file {path}: {e}")

    return Config.from_dict(merged)
