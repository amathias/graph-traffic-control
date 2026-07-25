from __future__ import annotations

import pytest

from graph_traffic_control.config import Settings, get_settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointed at an isolated temporary state directory.

    Tests never touch the real state directory, and never require DataHub.
    """
    return Settings(
        APP_STATE_DIR=tmp_path / "state",
        APP_ENV="test",
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is cached; clear it so env changes in one test cannot leak into another."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
