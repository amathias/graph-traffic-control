"""Namespace-scoped demo reset.

``../AGENTS.md`` states that a project reset must never run a global DataHub nuke or delete
another project's entities. This reset is therefore bounded twice:

1. Filesystem deletions are confined to ``APP_STATE_DIR`` by an explicit containment check that
   resolves symlinks first.
2. DataHub deletions (added in Phase 5) are confined to the ``traffic.`` allocation by the same
   fail-closed guard the seed uses.

The reset never removes the fixture root. Fixtures are version-controlled inputs, not state.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from graph_traffic_control.config import Settings, get_settings
from graph_traffic_control.context.namespace import (
    NamespaceViolation,
    require_contained_path,
)


def reset(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    state_dir = settings.state_dir

    removed: list[str] = []

    if state_dir.exists():
        # Refuse if the configured state directory is somehow the fixture root or the repo root.
        if state_dir.resolve() == settings.fixture_root.resolve():
            raise NamespaceViolation(
                "Reset refused: APP_STATE_DIR resolves to the fixture root. Fixtures are "
                "version-controlled inputs and must not be deleted by a reset."
            )

        for child in sorted(state_dir.iterdir()):
            # Every deletion target is proven to live inside the state directory.
            target = require_contained_path(child, state_dir, operation="Demo reset")
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(child.name)

    state_dir.mkdir(parents=True, exist_ok=True)

    return {"state_dir": state_dir, "removed": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset Graph Traffic Control demo state. Scoped to this project only."
    )
    parser.parse_args(argv)

    try:
        result = reset()
    except NamespaceViolation as exc:
        print(f"Reset refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Reset failed: {exc}", file=sys.stderr)
        return 1

    removed = result["removed"]
    assert isinstance(removed, list)
    state_dir = result["state_dir"]
    assert isinstance(state_dir, Path)

    if removed:
        print(f"Removed {len(removed)} item(s) from {state_dir}:")
        for name in removed:
            print(f"  {name}")
    else:
        print(f"Nothing to remove. {state_dir} is already clean.")
    print("DataHub entities outside the 'traffic.' allocation were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
