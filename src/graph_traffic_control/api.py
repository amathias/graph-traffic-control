"""FastAPI application.

Endpoints:

``GET  /api/health``        process liveness.
``GET  /api/readiness``     strong, non-mutating readiness (see :mod:`readiness`).
``GET  /api/graph``         the project's graph snapshot and its fingerprint.
``POST /api/proposals``     submit and prepare a structured change proposal.
``POST /api/proposals/{id}/approve``  record approval for a high-blast-radius change.
``POST /api/proposals/{id}/commit``   commit a prepared proposal.
``POST /api/proposals/{id}/abort``    abort a proposal.
``GET  /api/proposals``     current proposals and states.
``GET  /api/events``        the append-only transaction audit log.
``GET  /api/leases``        live leases and their remaining time.

The service never performs a DataHub writeback from a request path unless the proposal committed
and live mode is configured, and the writeback is always the reversible capture/write/re-read/
restore cycle in :mod:`graph_traffic_control.writeback`.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from graph_traffic_control import __version__
from graph_traffic_control import readiness as readiness_module
from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.datahub import DataHubContextProvider
from graph_traffic_control.context.fixture import FixtureContextProvider
from graph_traffic_control.context.mcp_client import McpClient
from graph_traffic_control.context.namespace import (
    Namespace,
    NamespaceViolation,
    require_contained_path,
)
from graph_traffic_control.context.provider import ContextProvider, ContextReadError
from graph_traffic_control.demo.reset import reset
from graph_traffic_control.demo.scenario import ScenarioRunner
from graph_traffic_control.demo.seed import ARTIFACT_BY_URN, load_manifest, seed
from graph_traffic_control.domain.clock import SystemClock
from graph_traffic_control.domain.models import ChangeProposal
from graph_traffic_control.domain.states import IllegalTransition
from graph_traffic_control.execute.targets import ArtifactExecutor
from graph_traffic_control.receipts import RECEIPTS_DIRNAME, ReceiptWriter
from graph_traffic_control.txn.coordinator import Coordinator, CoordinatorError
from graph_traffic_control.txn.store import TransactionStore
from graph_traffic_control.writeback.datahub import ReversibleDescriptionWriteback


def _interactive_docs_enabled(app_env: str) -> bool:
    return app_env.casefold() in {"development", "local", "test"}


_docs_enabled = _interactive_docs_enabled(os.getenv("APP_ENV", "local"))
app = FastAPI(
    title="Graph Traffic Control",
    version=__version__,
    description="Transactional coordination for autonomous data agents, powered by DataHub.",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

SettingsDep = Annotated[Settings, Depends(get_settings)]
PUBLIC_READ_ONLY_ENVIRONMENTS = {"hackathon", "production"}
PUBLIC_DEMO_COOLDOWN_SECONDS = 30

_demo_lock = Lock()
_demo_running = False
_demo_last_finished = 0.0

#: The judge console ships inside the package. It is a single self-contained document with no
#: external stylesheet, script, or font, so it renders identically offline and on a locked-down
#: reviewer machine.
UI_INDEX = Path(__file__).resolve().parent / "web" / "index.html"


def allocated_urns(settings: Settings) -> list[str]:
    manifest = load_manifest(settings)
    return list(manifest.get("entities", [])) if manifest else []


def build_provider(settings: Settings, namespace: Namespace) -> ContextProvider:
    """Live DataHub provider when credentials exist, recorded fixture otherwise (ADR-001)."""
    if settings.live_mode:
        client = McpClient(settings.datahub_mcp_url, settings.datahub_token)
        return DataHubContextProvider(client, namespace, allocated_urns(settings))
    return FixtureContextProvider(settings.fixture_root, namespace)


def _direct_mutations_enabled(settings: Settings) -> bool:
    return settings.app_env.casefold() not in PUBLIC_READ_ONLY_ENVIRONMENTS


def require_direct_mutations(settings: SettingsDep) -> None:
    """Keep the hosted API observable while reserving direct mutations for trusted runtimes."""
    if not _direct_mutations_enabled(settings):
        raise HTTPException(
            status_code=403,
            detail=(
                "Direct proposal mutations are disabled on the public deployment. "
                "Use the fixed, isolated judge scenario."
            ),
        )


def _begin_demo(settings: Settings) -> bool:
    """Acquire single-flight admission and return whether public controls apply."""
    global _demo_running

    public_controls = settings.app_env.casefold() in PUBLIC_READ_ONLY_ENVIRONMENTS
    now = time.monotonic()
    with _demo_lock:
        if _demo_running:
            raise HTTPException(
                status_code=429,
                detail="The public demo is already running. Try again shortly.",
                headers={"Retry-After": "1"},
            )
        remaining = (
            PUBLIC_DEMO_COOLDOWN_SECONDS - (now - _demo_last_finished)
            if public_controls
            else 0
        )
        if public_controls and remaining > 0:
            retry_after = max(1, math.ceil(remaining))
            raise HTTPException(
                status_code=429,
                detail="The public demo is cooling down. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        _demo_running = True
    return public_controls


def _finish_demo(public_controls: bool) -> None:
    global _demo_last_finished, _demo_running

    with _demo_lock:
        if public_controls:
            _demo_last_finished = time.monotonic()
        _demo_running = False


def _reset_demo_limiter_for_tests() -> None:
    """Restore limiter state between isolated TestClient cases."""
    global _demo_last_finished, _demo_running

    with _demo_lock:
        _demo_running = False
        _demo_last_finished = 0.0


class Runtime:
    """Per-request coordinator wiring."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.namespace = Namespace.from_settings(settings)
        self.clock = SystemClock()
        self.store = TransactionStore(settings.db_path)
        self.executor = ArtifactExecutor(settings.state_dir)
        self.provider = build_provider(settings, self.namespace)
        self.receipts = ReceiptWriter(settings.state_dir, secrets=(settings.datahub_token,))
        self.coordinator = Coordinator(
            store=self.store,
            provider=self.provider,
            namespace=self.namespace,
            clock=self.clock,
            executor=self.executor,
            downstream_artifacts=ARTIFACT_BY_URN,
        )

    def writeback(self) -> ReversibleDescriptionWriteback | None:
        if not self.settings.live_mode:
            return None
        client = McpClient(self.settings.datahub_mcp_url, self.settings.datahub_token)
        return ReversibleDescriptionWriteback(
            client,
            self.namespace,
            self.clock,
            operation=self.settings.datahub_description_operation,
        )

    def close(self) -> None:
        self.store.close()


def get_runtime(settings: SettingsDep) -> Any:
    runtime = Runtime(settings)
    try:
        yield runtime
    finally:
        runtime.close()


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


# --------------------------------------------------------------------------------------
# Contract endpoints
# --------------------------------------------------------------------------------------


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
    result = readiness_module.evaluate(settings, namespace, allocated_urns(settings))
    body = {
        "ready": result["ready"],
        "project": settings.project_slug,
        "version": __version__,
        "mode": result["mode"],
        "namespace": {
            "urn_prefix": namespace.urn_prefix,
            "domain": namespace.domain,
            "tag": namespace.project_tag,
        },
        "direct_mutations_enabled": _direct_mutations_enabled(settings),
        "demo_cooldown_seconds": (
            PUBLIC_DEMO_COOLDOWN_SECONDS
            if settings.app_env.casefold() in PUBLIC_READ_ONLY_ENVIRONMENTS
            else 0
        ),
        "checks": result["checks"],
    }
    return JSONResponse(content=body, status_code=200 if result["ready"] else 503)


# --------------------------------------------------------------------------------------
# Coordination endpoints
# --------------------------------------------------------------------------------------


@app.get("/api/graph")
def graph(runtime: RuntimeDep) -> dict[str, Any]:
    # 503, never an empty graph: a caller that cannot tell "unreadable" from "no dependencies"
    # would draw exactly the wrong conclusion.
    try:
        snapshot = runtime.provider.snapshot()
    except ContextReadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {
        "source": runtime.provider.source,
        "fingerprint": snapshot.fingerprint(),
        "captured_at": snapshot.captured_at.isoformat(),
        "entities": [entity.model_dump(mode="json") for entity in snapshot.entities.values()],
        "edges": [edge.model_dump(mode="json") for edge in snapshot.edges],
    }


@app.post("/api/proposals", dependencies=[Depends(require_direct_mutations)])
def submit_proposal(proposal: ChangeProposal, runtime: RuntimeDep) -> dict[str, Any]:
    try:
        outcome = runtime.coordinator.prepare(proposal)
    except NamespaceViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except CoordinatorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    snapshot_fingerprint = outcome.token.snapshot_fingerprint if outcome.token else ""
    runtime.receipts.proposal_receipt(
        proposal=proposal,
        impact=outcome.impact,
        conflicts=outcome.conflicts,
        state=outcome.state,
        context_source=runtime.provider.source,
        snapshot_fingerprint=snapshot_fingerprint,
    )
    if outcome.lease and outcome.token:
        runtime.receipts.lease_receipt(outcome.lease, outcome.token)

    return {
        "proposal_id": outcome.proposal_id,
        "state": outcome.state.value,
        "reason": outcome.reason,
        "impact": outcome.impact.model_dump(mode="json"),
        "conflicts": [c.model_dump(mode="json") for c in outcome.conflicts],
        "prepared_token": outcome.token.token if outcome.token else None,
        "approval_required": outcome.token.approval_required if outcome.token else False,
        "lease_id": outcome.lease.lease_id if outcome.lease else None,
        "context_source": runtime.provider.source,
    }


@app.post(
    "/api/proposals/{proposal_id}/approve",
    dependencies=[Depends(require_direct_mutations)],
)
def approve_proposal(proposal_id: str, token: str, approver: str, runtime: RuntimeDep) -> dict:
    stored = runtime.coordinator.token(token)
    if stored is None or stored.proposal_id != proposal_id:
        raise HTTPException(status_code=404, detail="Unknown prepared token for that proposal")
    approved = runtime.coordinator.approve(stored, approver)
    return {"proposal_id": proposal_id, "approved_by": approved.approved_by}


@app.post(
    "/api/proposals/{proposal_id}/commit",
    dependencies=[Depends(require_direct_mutations)],
)
def commit_proposal(proposal_id: str, token: str, runtime: RuntimeDep) -> dict[str, Any]:
    proposal = runtime.store.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Unknown proposal")
    stored = runtime.coordinator.token(token)
    if stored is None or stored.proposal_id != proposal_id:
        raise HTTPException(status_code=404, detail="Unknown prepared token for that proposal")

    try:
        outcome = runtime.coordinator.commit(proposal, stored, runtime.writeback())
    except CoordinatorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    receipt_path = runtime.receipts.commit_receipt(
        proposal=proposal,
        final_state=outcome.state,
        events=runtime.store.list_events(proposal_id),
        context_source=runtime.provider.source,
        prepare_fingerprint=outcome.prepare_fingerprint,
        commit_fingerprint=outcome.commit_fingerprint,
        artifact_diff=outcome.artifact_diff,
        validation=outcome.validation,
        writeback=outcome.writeback,
        verification=outcome.verification,
        abort_reason=outcome.reason or None,
    )
    # Receipt writing is tracked separately from the commit itself: a commit is not evidenced
    # merely because it happened.
    outcome.verification.receipts.append(receipt_path.name)

    return {
        "proposal_id": outcome.proposal_id,
        "state": outcome.state.value,
        "reason": outcome.reason,
        "drift_detected": outcome.drift_detected,
        "artifact_diff": outcome.artifact_diff,
        "validation": outcome.validation,
        "verification": outcome.verification.model_dump(mode="json"),
        "writeback": (
            outcome.writeback.model_dump(mode="json") if outcome.writeback else None
        ),
    }


@app.post(
    "/api/proposals/{proposal_id}/abort",
    dependencies=[Depends(require_direct_mutations)],
)
def abort_proposal(proposal_id: str, reason: str, runtime: RuntimeDep) -> dict[str, Any]:
    proposal = runtime.store.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Unknown proposal")
    outcome = runtime.coordinator.abort(proposal, reason)
    return {"proposal_id": proposal_id, "state": outcome.state.value, "reason": outcome.reason}


@app.get("/api/proposals")
def list_proposals(runtime: RuntimeDep) -> dict[str, Any]:
    return {
        "proposals": [
            {
                "proposal_id": proposal.proposal_id,
                "agent_id": proposal.agent.agent_id,
                "intent": proposal.intent,
                "state": state.value,
                "read_set": proposal.read_set,
                "write_set": proposal.write_set,
            }
            for proposal, state in runtime.store.list_proposals()
        ]
    }


@app.get("/api/events")
def list_events(runtime: RuntimeDep, proposal_id: str | None = None) -> dict[str, Any]:
    return {
        "events": [
            event.model_dump(mode="json") for event in runtime.store.list_events(proposal_id)
        ]
    }


@app.get("/api/leases")
def list_leases(runtime: RuntimeDep) -> dict[str, Any]:
    manager = runtime.coordinator.leases
    return {
        "now": manager.now().isoformat(),
        "active": [
            {
                **lease.model_dump(mode="json"),
                "seconds_remaining": manager.seconds_remaining(lease.lease_id),
            }
            for lease in manager.active_leases()
        ],
        "expired": [lease.model_dump(mode="json") for lease in manager.expired_leases()],
    }


# --------------------------------------------------------------------------------------
# Judge-facing UI and evidence
# --------------------------------------------------------------------------------------

#: The judge scenario runs in its own state directory. It resets and reseeds on every run so a
#: judge can press the button repeatedly and see the same story; doing that to the live state
#: directory would destroy any proposals submitted through the API alongside it.
JUDGE_STATE_DIRNAME = "judge"


@app.get("/", response_class=HTMLResponse)
def judge_ui() -> HTMLResponse:
    """The project-owned judge console. Self-contained: no external assets are fetched."""
    return HTMLResponse(UI_INDEX.read_text(encoding="utf-8"))


@app.post("/api/demo/run")
def run_demo(settings: SettingsDep) -> dict[str, Any]:
    """Run the deterministic four-agent scenario and return everything a judge needs to see.

    Deterministic by construction: fixed submission order, explicit barriers, injected clock.
    ``AGENTS.md`` forbids letting the demo depend on uncontrolled concurrent timing.
    """
    public_controls = _begin_demo(settings)
    try:
        judge_dir = settings.state_dir / JUDGE_STATE_DIRNAME
        judge_settings = settings.model_copy(update={"app_state_dir": judge_dir})

        reset(judge_settings)
        seed(judge_settings)

        runner = ScenarioRunner(judge_settings)
        try:
            runner.run(echo=lambda *_args, **_kwargs: None)
            return runner.judge_payload()
        finally:
            runner.close()
    finally:
        _finish_demo(public_controls)


@app.get("/api/receipts")
def list_receipts(settings: SettingsDep) -> dict[str, Any]:
    """Index of receipt evidence written by the most recent judge scenario run."""
    directory = (
        settings.state_dir / JUDGE_STATE_DIRNAME / RECEIPTS_DIRNAME
    )
    if not directory.is_dir():
        return {"receipts": []}
    return {"receipts": sorted(p.name for p in directory.glob("*.json"))}


@app.get("/api/receipts/{name}")
def read_receipt(name: str, settings: SettingsDep) -> dict[str, Any]:
    """One receipt, by filename.

    The name is resolved and proven to live inside the receipts directory before it is read, so
    a crafted name cannot walk out of it and serve an arbitrary file over HTTP.
    """
    directory = settings.state_dir / JUDGE_STATE_DIRNAME / RECEIPTS_DIRNAME
    try:
        path = require_contained_path(directory / name, directory, operation="Receipt read")
    except NamespaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if path.suffix != ".json" or not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown receipt")
    return json.loads(path.read_text(encoding="utf-8"))


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
