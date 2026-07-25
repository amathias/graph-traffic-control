"""Application settings bound to the portfolio shared environment contract.

Variable names come from ``../COORDINATOR_PLAN.md`` and must not be renamed without a
coordinator proposal. Namespace values come from ``COORDINATOR_HANDOFF.md`` and are the
inputs to the fail-closed guard in :mod:`graph_traffic_control.context.namespace`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_slug: str = Field(default="graph-traffic-control", alias="PROJECT_SLUG")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8105, alias="APP_PORT")
    app_public_url: str = Field(default="http://127.0.0.1:8105", alias="APP_PUBLIC_URL")
    app_state_dir: Path = Field(default=Path("demo/state"), alias="APP_STATE_DIR")

    datahub_gms_url: str = Field(default="", alias="DATAHUB_GMS_URL")
    datahub_mcp_url: str = Field(default="", alias="DATAHUB_MCP_URL")
    datahub_token: str = Field(default="", alias="DATAHUB_TOKEN")

    datahub_domain: str = Field(default="Demo / Graph Traffic Control", alias="DATAHUB_DOMAIN")
    datahub_project_tag: str = Field(
        default="project-graph-traffic-control", alias="DATAHUB_PROJECT_TAG"
    )
    datahub_urn_prefix: str = Field(default="traffic.", alias="DATAHUB_URN_PREFIX")

    demo_fixture_root: Path = Field(
        default=Path("demo/fixtures/graph-traffic-control"), alias="DEMO_FIXTURE_ROOT"
    )

    @property
    def state_dir(self) -> Path:
        """Absolute state directory. Relative values resolve against the repository root."""
        return self._absolute(self.app_state_dir)

    @property
    def fixture_root(self) -> Path:
        """Absolute fixture root. Relative values resolve against the repository root."""
        return self._absolute(self.demo_fixture_root)

    @property
    def datahub_configured(self) -> bool:
        """True when the coordinator has supplied live DataHub connection details."""
        return bool(self.datahub_gms_url and self.datahub_token)

    def _absolute(self, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
