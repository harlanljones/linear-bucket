"""Environment-based configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from .buckets import DEFAULT_GROUP_NAME

DEFAULT_API_URL = "https://api.linear.app/graphql"
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_RETRIES = 5


class ConfigError(RuntimeError):
    """Raised when the environment does not describe a usable configuration."""


@dataclass(frozen=True)
class Config:
    api_key: str
    api_url: str = DEFAULT_API_URL
    team_key: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    max_retries: int = DEFAULT_MAX_RETRIES
    dry_run: bool = False
    label_group: str = DEFAULT_GROUP_NAME
    bootstrap_labels: bool = False
    cleanup_legacy_prefixes: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env

        api_key = (env.get("LINEAR_API_KEY") or "").strip()
        if not api_key:
            raise ConfigError("LINEAR_API_KEY is required")

        team_key = (env.get("LINEAR_TEAM_KEY") or "").strip() or None

        return cls(
            api_key=api_key,
            api_url=(env.get("LINEAR_API_URL") or "").strip() or DEFAULT_API_URL,
            team_key=team_key,
            page_size=_int_env(env, "LINEAR_PAGE_SIZE", DEFAULT_PAGE_SIZE, 1, 250),
            max_retries=_int_env(env, "LINEAR_MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, 20),
            dry_run=_bool_env(env, "LINEAR_DRY_RUN", False),
            label_group=(env.get("LINEAR_LABEL_GROUP") or "").strip() or DEFAULT_GROUP_NAME,
            bootstrap_labels=_bool_env(env, "LINEAR_BOOTSTRAP_LABELS", False),
            cleanup_legacy_prefixes=_bool_env(env, "LINEAR_CLEANUP_LEGACY_PREFIXES", False),
        )


def _int_env(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")
