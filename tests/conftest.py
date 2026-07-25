from __future__ import annotations

import pytest

from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.fixture import FixtureContextProvider
from graph_traffic_control.context.namespace import Namespace
from graph_traffic_control.demo.seed import ARTIFACT_BY_URN, seed
from graph_traffic_control.domain.clock import ManualClock
from graph_traffic_control.execute.targets import ArtifactExecutor
from graph_traffic_control.txn.coordinator import Coordinator
from graph_traffic_control.txn.store import TransactionStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointed at an isolated temporary state directory.

    Tests never touch the real state directory, and never require DataHub.
    """
    return Settings(APP_STATE_DIR=tmp_path / "state", APP_ENV="test")


@pytest.fixture
def seeded_settings(settings) -> Settings:
    seed(settings)
    return settings


@pytest.fixture
def namespace(settings) -> Namespace:
    return Namespace.from_settings(settings)


@pytest.fixture
def clock() -> ManualClock:
    """Manual clock so lease expiry and drift are tested without sleeping."""
    return ManualClock()


@pytest.fixture
def provider(seeded_settings, namespace, clock) -> FixtureContextProvider:
    return FixtureContextProvider(seeded_settings.fixture_root, namespace, clock)


@pytest.fixture
def store(seeded_settings) -> TransactionStore:
    store = TransactionStore(seeded_settings.db_path)
    yield store
    store.close()


@pytest.fixture
def executor(seeded_settings) -> ArtifactExecutor:
    return ArtifactExecutor(seeded_settings.state_dir)


@pytest.fixture
def coordinator(store, provider, namespace, clock, executor) -> Coordinator:
    return Coordinator(
        store=store,
        provider=provider,
        namespace=namespace,
        clock=clock,
        executor=executor,
        downstream_artifacts=ARTIFACT_BY_URN,
    )


@pytest.fixture
def snapshot(provider):
    return provider.snapshot()


@pytest.fixture
def versions(snapshot):
    """Correct expected-version fingerprints, so proposals are fresh unless a test says so."""
    return {urn: entity.version_fingerprint() for urn, entity in snapshot.entities.items()}


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is cached; clear it so env changes in one test cannot leak into another."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
