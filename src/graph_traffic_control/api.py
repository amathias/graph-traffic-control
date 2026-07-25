"""FastAPI application.

Phase 0 exposes only the two endpoints required by the portfolio shared contract in
``../COORDINATOR_PLAN.md``:

``GET /api/health``     proves the process is alive.
``GET /api/readiness``  verifies required local state and DataHub connectivity, without
                        mutating shared state.

The proposal API, transaction lifecycle, and SSE stream arrive in later phases.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from graph_traffic_control import __version__
from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.seed import collect_urns, load_fixture_graph

DATAHUB_PROBE_TIMEOUT_SECONDS = 3.0

# Settings arrive by dependency injection so tests can override them without patching module
# globals or clearing caches.
SettingsDep = Annotated[Settings, Depends(get_settings)]

app = FastAPI(
    title="Graph Traffic Control",
    version=__version__,
    description="Transactional coordination for autonomous data agents, powered by DataHub.",
)


def _check_fixture_graph(settings: Settings, namespace: Namespace) -> dict[str, Any]:
    """Fixture graph is present, parseable, and entirely inside this project's allocation."""
    try:
        graph = load_fixture_graph(settings)
    except FileNotFoundError as exc:
        return {"ok": False, "detail": str(exc)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "detail": f"Fixture graph is not valid JSON: {exc}"}

    try:
        urns = collect_urns(graph)
    except KeyError as exc:
        return {"ok": False, "detail": f"Fixture graph is missing key {exc}"}

    try:
        namespace.require_all(urns, operation="Readiness check")
    except NamespaceViolation as exc:
        return {"ok": False, "detail": str(exc)}

    return {
        "ok": True,
        "entities": len(urns),
        "edges": len(graph.get("edges", [])),
    }


def _check_state_dir(settings: Settings) -> dict[str, Any]:
    """State directory exists and is writable. Writes and removes a probe file only."""
    state_dir = settings.state_dir
    if not state_dir.is_dir():
        return {
            "ok": False,
            "detail": f"{state_dir} does not exist. Run `gtc-seed` first.",
        }
    probe = state_dir / ".readiness-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return {"ok": False, "detail": f"{state_dir} is not writable: {exc}"}
    return {"ok": True, "path": str(state_dir)}


def _check_datahub(settings: Settings) -> dict[str, Any]:
    """Read-only reachability probe against DataHub GMS.

    Never mutates shared state and never raises. When the coordinator has not yet supplied
    connection details, this reports ``not_configured`` rather than failing: phases 0-4 and 6
    are fixture-backed by design and must stay runnable without DataHub.
    """
    if not settings.datahub_configured:
        return {
            "ok": True,
            "status": "not_configured",
            "detail": "DATAHUB_GMS_URL/DATAHUB_TOKEN unset. Running fixture-backed.",
        }

    url = settings.datahub_gms_url.rstrip("/") + "/health"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {settings.datahub_token}")
    try:
        with urllib.request.urlopen(request, timeout=DATAHUB_PROBE_TIMEOUT_SECONDS) as response:
            reachable = 200 <= response.status < 300
            return {
                "ok": reachable,
                "status": "reachable" if reachable else "unhealthy",
                "http_status": response.status,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": "unreachable", "detail": str(exc)}


@app.get("/api/health")
def health(settings: SettingsDep) -> dict[str, Any]:
    return {
        "status": "ok",
        "project": settings.project_slug,
        "version": __version__,
        "env": settings.app_env,
    }


@app.get("/api/readiness")
def readiness(settings: SettingsDep) -> JSONResponse:
    namespace = Namespace.from_settings(settings)

    checks = {
        "fixture_graph": _check_fixture_graph(settings, namespace),
        "state_dir": _check_state_dir(settings),
        "datahub": _check_datahub(settings),
    }
    ready = all(check["ok"] for check in checks.values())

    body = {
        "ready": ready,
        "project": settings.project_slug,
        "version": __version__,
        "namespace": {
            "urn_prefix": namespace.urn_prefix,
            "domain": namespace.domain,
            "tag": namespace.project_tag,
        },
        "checks": checks,
    }
    return JSONResponse(content=body, status_code=200 if ready else 503)


def run() -> None:
    """Console-script entrypoint: `gtc-api`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "graph_traffic_control.api:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level="info",
    )
