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

    #: URN of the coordinator-allocated domain. Derived from the domain name by default so the
    #: shared contract stays the single source of truth, but overridable if the shared instance
    #: minted a different id.
    datahub_domain_urn: str = Field(
        default="urn:li:domain:graph-traffic-control", alias="DATAHUB_DOMAIN_URN"
    )

    #: ``operation`` argument for the MCP ``update_description`` tool. **Live-confirmed against
    #: DataHub 1.6.0**, which rejected the previous ``SET`` guess and accepted ``replace``.
    #: Kept configurable so a different server can be accommodated without a code change.
    #: See docs/LIMITATIONS.md.
    datahub_description_operation: str = Field(
        default="replace", alias="DATAHUB_DESCRIPTION_OPERATION"
    )

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

    @property
    def live_mode(self) -> bool:
        """True when this process should read real DataHub context through MCP.

        Requires the token as well as the endpoint: an endpoint without credentials cannot
        perform the authenticated checks readiness demands, so it is not live mode.
        """
        return bool(self.datahub_mcp_url and self.datahub_token)

    @property
    def db_path(self) -> Path:
        """SQLite database holding proposals, leases, and the audit log."""
        return self.state_dir / "transactions.sqlite"

    def _absolute(self, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
