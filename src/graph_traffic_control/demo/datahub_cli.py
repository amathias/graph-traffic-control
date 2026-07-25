"""Command-line entrypoints for DataHub seed, reset, and restore.

All three default to **planning only**. A plan is written to ``APP_STATE_DIR/datahub`` where it
can be inspected and diffed, and nothing reaches DataHub until ``--apply`` is passed with live
credentials present. That default is deliberate: these commands act on an instance shared with
four other submissions, so "run it and see" must not be the path of least resistance.

Every command guards the complete plan before writing it, so an out-of-allocation entity is
refused at planning time rather than part-way through an apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.mcp_client import McpClient, McpError
from graph_traffic_control.context.namespace import Namespace, NamespaceViolation
from graph_traffic_control.demo.datahub_state import (
    CAPTURE_FILENAME,
    DATAHUB_STATE_DIRNAME,
    NAMESPACE_SCOPE,
    RESET_PLAN_FILENAME,
    RESTORE_PLAN_FILENAME,
    SEED_PLAN_FILENAME,
    PlanError,
    apply_plan,
    capture_state,
    reset_plan,
    restore_plan,
    seed_plan,
    write_plan,
)
from graph_traffic_control.demo.seed import load_fixture_graph, load_manifest

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


def _allocated(settings: Settings) -> list[str]:
    manifest = load_manifest(settings)
    if manifest is None:
        raise PlanError("No seed manifest. Run `gtc-seed` first.")
    return list(manifest.get("entities", []))


def _report(action: str, result: dict[str, Any], applied: dict[str, Any] | None) -> None:
    print(f"{action}: {result['operation_count']} operations over "
          f"{result['entity_count']} entities")
    print(f"  plan fingerprint: {result['fingerprint']}")
    for path in result["paths"]:
        print(f"  wrote {path}")
    if applied is None:
        print("  NOT applied. Re-run with --apply and live credentials to write to DataHub.")
    else:
        print(f"  applied {applied['applied']} operations to DataHub")


def _run(build, action: str, argv: list[str] | None, description: str) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the plan to DataHub. Requires DATAHUB_GMS_URL and DATAHUB_TOKEN.",
    )
    parser.add_argument(
        "--scope",
        default=NAMESPACE_SCOPE,
        help=f"Operation scope. Only {NAMESPACE_SCOPE!r} is permitted.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    namespace = Namespace.from_settings(settings)

    try:
        plan, filename = build(settings, namespace, args)
        result = write_plan(plan, settings, filename)
        applied = apply_plan(plan, namespace, settings) if args.apply else None
    except NamespaceViolation as exc:
        print(f"{action} refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (PlanError, McpError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"{action} failed: {exc}", file=sys.stderr)
        return EXIT_FAILED

    _report(action, result, applied)
    print(f"Entities outside the {namespace.urn_prefix!r} allocation were not touched.")
    return EXIT_OK


def seed_main(argv: list[str] | None = None) -> int:
    """`gtc-datahub-seed`: plan (and optionally apply) the complete traffic. graph."""

    def build(settings, namespace, _args):
        graph = load_fixture_graph(settings)
        return seed_plan(graph, namespace, settings), SEED_PLAN_FILENAME

    return _run(
        build,
        "DataHub seed",
        argv,
        "Plan the complete traffic. graph: entities, schemas, ownership, domain, tag, "
        "marker, and lineage. Namespace-guarded and deterministic.",
    )


def reset_main(argv: list[str] | None = None) -> int:
    """`gtc-datahub-reset`: plan (and optionally apply) a namespace-scoped removal."""

    def build(settings, namespace, args):
        return (
            reset_plan(_allocated(settings), namespace, settings, scope=args.scope),
            RESET_PLAN_FILENAME,
        )

    return _run(
        build,
        "DataHub reset",
        argv,
        "Plan removal of THIS PROJECT'S entities only. A global refresh is refused: the "
        "instance is shared with four other submissions.",
    )


def restore_main(argv: list[str] | None = None) -> int:
    """`gtc-datahub-restore`: plan (and optionally apply) a restore from the pre-seed capture."""

    def build(settings, namespace, _args):
        path = settings.state_dir / DATAHUB_STATE_DIRNAME / CAPTURE_FILENAME
        if not path.is_file():
            raise PlanError(
                f"No capture at {path}. Run `gtc-datahub-capture` before seeding, so there is "
                "something to restore to."
            )
        capture = json.loads(path.read_text(encoding="utf-8"))
        return restore_plan(capture, namespace, settings), RESTORE_PLAN_FILENAME

    return _run(
        build,
        "DataHub restore",
        argv,
        "Plan a restore of the allocated entities to their captured pre-seed state.",
    )


def capture_main(argv: list[str] | None = None) -> int:
    """`gtc-datahub-capture`: record the allocated entities' current state, before seeding.

    Read-only. Requires live credentials, because there is nothing to capture without them, and
    an empty capture file would make a later restore silently do nothing.
    """
    parser = argparse.ArgumentParser(
        description="Capture the current DataHub state of this project's allocated entities."
    )
    parser.parse_args(argv)

    settings = get_settings()
    namespace = Namespace.from_settings(settings)

    if not settings.live_mode:
        print(
            "Capture refused: DATAHUB_MCP_URL and DATAHUB_TOKEN are not both set. An empty "
            "capture would make a later restore silently do nothing.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    client = McpClient(settings.datahub_mcp_url, settings.datahub_token)
    try:
        capture = capture_state(client, namespace, _allocated(settings))
    except NamespaceViolation as exc:
        print(f"Capture refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (PlanError, McpError) as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    finally:
        client.close()

    directory = settings.state_dir / DATAHUB_STATE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CAPTURE_FILENAME
    path.write_text(
        json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Captured {capture['entity_count']} entities to {path}")
    return EXIT_OK
