"""Command-line entrypoints for DataHub capture, seed, reset, and restore.

Seed, reset, and restore default to **planning only**. A plan is written to
``APP_STATE_DIR/datahub`` where it can be inspected and diffed, and nothing reaches DataHub until
``--apply`` is passed with live credentials present. That default is deliberate: these commands
act on an instance shared with four other submissions, so "run it and see" must not be the path of
least resistance.

Every command guards the complete plan before writing it, so an out-of-allocation entity is
refused at planning time rather than part-way through an apply.

First-time seeding
------------------
The first time this project is seeded, the whole ``traffic.`` namespace is missing. Capture still
has to run first — but there is nothing to read, so it must be told that absence is the expected
answer::

    gtc-datahub-capture --allow-absent     # records every allocated URN as absent
    gtc-datahub-seed --apply               # creates exactly those URNs
    ...
    gtc-datahub-restore --apply            # soft-deletes them again, and proves it

Without ``--allow-absent``, a missing entity is still a hard failure. That distinction is the
point: "the namespace does not exist yet" and "half this project's rows have disappeared" look
identical to a reader, and only the operator knows which one is true.
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
    NAMESPACE_SCOPE,
    RESET_PLAN_FILENAME,
    RESTORE_PLAN_FILENAME,
    SEED_PLAN_FILENAME,
    PlanError,
    apply_plan,
    capture_state,
    load_capture,
    reset_plan,
    restore_plan,
    seed_plan,
    verify_absent,
    verify_capture,
    write_capture,
    write_plan,
)
from graph_traffic_control.demo.seed import load_fixture_graph, load_manifest

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

#: Errors that mean "this run cannot be completed", as opposed to "this run was refused as unsafe".
_FAILURES = (PlanError, McpError, FileNotFoundError, KeyError, json.JSONDecodeError)


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


def _run(
    build,
    action: str,
    argv: list[str] | None,
    description: str,
    after_apply=None,
) -> int:
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

    followup: str | None = None
    try:
        plan, filename = build(settings, namespace, args)
        result = write_plan(plan, settings, filename)
        applied = apply_plan(plan, namespace, settings) if args.apply else None
        if applied is not None and after_apply is not None:
            # Runs before anything is reported. A post-apply check that cannot be performed, or
            # that fails, must not arrive after a success line has already been printed.
            followup = after_apply(settings, namespace)
    except NamespaceViolation as exc:
        print(f"{action} refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except _FAILURES as exc:
        print(f"{action} failed: {exc}", file=sys.stderr)
        return EXIT_FAILED

    _report(action, result, applied)
    if followup:
        print(f"  {followup}")
    print(f"Entities outside the {namespace.urn_prefix!r} allocation were not touched.")
    return EXIT_OK


def seed_main(argv: list[str] | None = None) -> int:
    """`gtc-datahub-seed`: plan (and optionally apply) the complete traffic. graph."""

    def build(settings, namespace, _args):
        graph = load_fixture_graph(settings)
        allocated = _allocated(settings)
        # The plan must cover the manifest exactly. A seed that created a different set than the
        # one capture recorded would leave the difference with nothing to restore it to.
        return seed_plan(graph, namespace, settings, allocated), SEED_PLAN_FILENAME

    return _run(
        build,
        "DataHub seed",
        argv,
        "Plan the complete traffic. graph: entities, schemas, ownership, domain, tag, "
        "marker, and lineage. Namespace-guarded, deterministic, and exactly the allocation.",
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
    """`gtc-datahub-restore`: plan (and optionally apply) a restore from the pre-seed capture.

    Present entities go back to their captured values; entities captured as absent are
    soft-deleted. When any were absent, ``--apply`` re-reads them afterwards and refuses to report
    success unless every one is verifiably gone.
    """
    absent_urns: list[str] = []

    def build(settings, namespace, args):
        capture = load_capture(settings)
        allocated = _allocated(settings)
        _, absent = verify_capture(capture, namespace, allocated)
        absent_urns[:] = absent
        return (
            restore_plan(capture, namespace, settings, allocated, scope=args.scope),
            RESTORE_PLAN_FILENAME,
        )

    def after_apply(settings, namespace) -> str | None:
        if not absent_urns:
            return None
        if not settings.live_mode:
            raise PlanError(
                "Restore applied soft deletes for entities captured as absent, but "
                "DATAHUB_MCP_URL and DATAHUB_TOKEN are not both set, so their absence cannot be "
                "re-read and proved. Set them and re-run: an unverified 'returned to absent' is "
                "the claim this project does not make."
            )
        client = McpClient(settings.datahub_mcp_url, settings.datahub_token)
        try:
            result = verify_absent(client, namespace, absent_urns)
        finally:
            client.close()
        return (
            f"verified absent: {result['checked']} initially-absent entities re-read and "
            "confirmed removed"
        )

    return _run(
        build,
        "DataHub restore",
        argv,
        "Plan a restore of the allocated entities to their captured pre-seed state. Entities "
        "captured as absent are soft-deleted and their absence is verified after --apply.",
        after_apply=after_apply,
    )


def capture_main(argv: list[str] | None = None) -> int:
    """`gtc-datahub-capture`: record the allocated entities' current state, before seeding.

    Read-only. Requires live credentials, because there is nothing to capture without them, and
    an empty capture file would make a later restore silently do nothing.
    """
    parser = argparse.ArgumentParser(
        description="Capture the current DataHub state of this project's allocated entities."
    )
    parser.add_argument(
        "--allow-absent",
        action="store_true",
        help=(
            "Record allocated entities that are missing or soft-deleted as absent, rather than "
            "failing. Use for a first-time seed, when the whole traffic. namespace is expected "
            "to be missing. Restore will soft-delete exactly these entities and verify it."
        ),
    )
    args = parser.parse_args(argv)

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
        capture = capture_state(
            client, namespace, _allocated(settings), allow_absent=args.allow_absent
        )
    except NamespaceViolation as exc:
        print(f"Capture refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except _FAILURES as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    finally:
        client.close()

    path = write_capture(capture, settings)
    print(
        f"Captured {capture['entity_count']} present and {capture['absent_count']} absent "
        f"entities to {path}"
    )
    if capture["absent_count"]:
        print(
            "  Absence recorded deliberately (--allow-absent). `gtc-datahub-restore` will "
            "soft-delete these entities and verify they are gone."
        )
    return EXIT_OK
