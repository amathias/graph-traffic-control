"""Real local artifact mutation.

Proposals rewrite disposable SQL files under ``APP_STATE_DIR/artifacts``. These are genuine file
edits producing genuine diffs, which is what ``PROJECT_BRIEF.md`` means by "real local artifact
changes"; nothing here touches a production system.

Every write is bounded twice: the proposal's ``artifact_path`` is validated as relative and
non-traversing at model level, and the resolved path is checked for containment inside the
artifact root before any write. A rollback copy is taken so a failed validation can restore the
file exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from graph_traffic_control.context.namespace import require_contained_path
from graph_traffic_control.domain.models import ChangeAction

ARTIFACTS_DIRNAME = "artifacts"


class ExecutionError(RuntimeError):
    """Raised when a change cannot be applied."""


@dataclass(frozen=True)
class ExecutionResult:
    artifact_path: Path
    before: str
    after: str
    changed: bool

    @property
    def diff_summary(self) -> str:
        if not self.changed:
            return "no textual change"
        before_lines = self.before.splitlines()
        after_lines = self.after.splitlines()
        changed = sum(1 for a, b in zip(before_lines, after_lines, strict=False) if a != b)
        delta = abs(len(after_lines) - len(before_lines))
        return f"{changed + delta} line(s) changed"


class ArtifactExecutor:
    """Applies a :class:`ChangeAction` to a disposable local SQL artifact."""

    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / ARTIFACTS_DIRNAME

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, artifact_path: str) -> Path:
        candidate = self._root / artifact_path
        return require_contained_path(
            candidate.parent, self._root, operation="Artifact write"
        ) / candidate.name

    def read(self, artifact_path: str) -> str:
        path = self.resolve(artifact_path)
        if not path.is_file():
            raise ExecutionError(f"Artifact not found: {path}")
        return path.read_text(encoding="utf-8")

    def apply(self, action: ChangeAction) -> ExecutionResult:
        path = self.resolve(action.artifact_path)
        if not path.is_file():
            raise ExecutionError(f"Artifact not found: {path}")

        before = path.read_text(encoding="utf-8")

        if action.kind == "rename_column":
            assert action.field_path and action.new_field_path
            if action.field_path not in before:
                raise ExecutionError(
                    f"Column {action.field_path!r} does not appear in {action.artifact_path}. "
                    "The proposal is based on stale content."
                )
            after = before.replace(action.field_path, action.new_field_path)
        elif action.kind in {"redefine_metric", "update_model"}:
            marker = f"-- coordinated-by: graph-traffic-control ({action.kind})"
            body = "\n".join(line for line in before.splitlines() if not line.startswith(marker))
            after = f"{marker}\n{body}".rstrip() + "\n"
        else:  # pragma: no cover - Literal type makes this unreachable
            raise ExecutionError(f"Unsupported action kind: {action.kind}")

        path.write_text(after, encoding="utf-8")
        return ExecutionResult(
            artifact_path=path, before=before, after=after, changed=before != after
        )

    def rollback(self, result: ExecutionResult) -> None:
        """Restore the artifact to its pre-execution content."""
        result.artifact_path.write_text(result.before, encoding="utf-8")
