"""Typed configuration for OpenPath.

All settings are validated with pydantic-settings and loaded from the
environment (see :mod:`openpath.env`). No secrets are ever serialized to disk
together with the rest of the config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "LLMConfig",
    "BrowserConfig",
    "Credentials",
    "LoginSelectors",
    "RunConfig",
    "load_settings",
]


class LLMConfig(BaseModel):
    """Connection to an OpenAI-compatible chat/completions endpoint."""

    base_url: str = "https://integrate.api.nvidia.com/v1"
    api_key: SecretStr = SecretStr("")
    model: str = "meta/llama-3.1-8b-instruct"
    timeout: int = 30
    max_steps: int = 8

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }


class BrowserConfig(BaseModel):
    """Playwright browser options."""

    headless: bool = True
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    viewport: tuple[int, int] = (1280, 720)
    video_on_failure: bool = True
    har_on_failure: bool = True
    trace: Literal["on", "off", "retain-on-failure"] = "retain-on-failure"
    screenshot_threshold: float = 0.1  # pixelmatch per-pixel tolerance


class LoginSelectors(BaseModel):
    """Selectors used by the deterministic auto-login helper."""

    username: str = 'input[name="username"]'
    password: str = 'input[name="password"]'
    submit: str = 'button[type="submit"]'


class Credentials(BaseModel):
    """Test-account credentials. Either all set, or all unset (no-login app)."""

    username: str | None = None
    password: SecretStr | None = None

    @property
    def has_login(self) -> bool:
        return bool(self.username) and bool(self.password)


class RunConfig(BaseModel):
    """High-level settings for a single ``openpath run`` invocation."""

    mode: Literal["codebase", "url"]
    codebase_path: Path | None = None
    url: str | None = None
    creds_env_prefix: str = ""
    output_dir: Path = Path("testruns")
    use_ai_runner: bool = True
    ai_tests_dir: Path | None = None  # directory with *.md natural-language tests
    server_port: int | None = None  # explicit override; else pick free port
    server_start_timeout: float = 30.0
    health_path: str = "/"
    headed: bool = False


class _EnvSettings(BaseSettings):
    """Backing settings read straight from environment variables."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    base_url: str | None = Field(default=None, alias="BASE_URL")
    test_username: str | None = Field(default=None, alias="TEST_USERNAME")
    test_password: SecretStr | None = Field(default=None, alias="TEST_PASSWORD")
    login_selector_user: str = Field(
        default='input[name="username"]', alias="LOGIN_SELECTOR_USER"
    )
    login_selector_pass: str = Field(
        default='input[name="password"]', alias="LOGIN_SELECTOR_PASS"
    )
    login_selector_submit: str = Field(
        default='button[type="submit"]', alias="LOGIN_SELECTOR_SUBMIT"
    )
    llm_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1", alias="LLM_BASE_URL"
    )
    llm_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_API_KEY")
    llm_model: str = Field(default="meta/llama-3.1-8b-instruct", alias="LLM_MODEL")
    llm_max_steps: int = Field(default=8, alias="LLM_MAX_STEPS")
    llm_timeout: int = Field(default=30, alias="LLM_TIMEOUT")


def load_settings(env_prefix: str = "") -> tuple[
    str | None, Credentials, LoginSelectors, LLMConfig, BrowserConfig
]:
    """Load and return the environment-derived settings tuple.

    ``env_prefix`` allows multiple apps under test to use distinct credential
    variables (e.g. ``TEST_USERNAME`` for the default and
    ``PROD_USERNAME`` with ``--creds-env-prefix PROD``).
    """

    def _env(key: str) -> str:
        full = f"{env_prefix}{key}" if env_prefix else key
        import os

        return os.environ.get(full) or os.environ.get(key) or ""

    username = _env("TEST_USERNAME") or None
    password_raw = _env("TEST_PASSWORD") or None
    password = SecretStr(password_raw) if password_raw else None

    credentials = Credentials(username=username, password=password)

    selectors = LoginSelectors(
        username=_env("LOGIN_SELECTOR_USER") or 'input[name="username"]',
        password=_env("LOGIN_SELECTOR_PASS") or 'input[name="password"]',
        submit=_env("LOGIN_SELECTOR_SUBMIT") or 'button[type="submit"]',
    )

    base_url = _env("BASE_URL") or None

    import openpath.env as env

    llm = LLMConfig(
        base_url=_env("LLM_BASE_URL") or "https://integrate.api.nvidia.com/v1",
        api_key=SecretStr(_env("LLM_API_KEY")),
        model=_env("LLM_MODEL") or "meta/llama-3.1-8b-instruct",
        timeout=env.as_int("LLM_TIMEOUT", 30),
        max_steps=env.as_int("LLM_MAX_STEPS", 8),
    )

    browser = BrowserConfig()
    return base_url, credentials, selectors, llm, browser
