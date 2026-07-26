"""The names of the files an operator is told to inspect, and the docs that name them.

A live gate was run against this project with a stale expectation of the capture filename. The
capture path was only ever a constant in the source: no document named it, so "run
``gtc-datahub-capture --allow-absent``, then inspect the capture" left the operator to guess, and
a guess is what happened.

These tests make the artifact names a checked contract in both directions. The constants may not
drift from the files the code actually writes, and the documentation may not drift from the
constants. Renaming an artifact without updating the runbook now fails the suite rather than
surfacing during a live run against a shared instance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_traffic_control.demo.datahub_state import (
    CAPTURE_FILENAME,
    CAPTURE_KIND,
    CAPTURE_VERSION,
    RECIPE_FILENAME,
    RESET_PLAN_FILENAME,
    RESTORE_PLAN_FILENAME,
    SEED_PLAN_FILENAME,
    capture_path,
    capture_state,
    write_capture,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every artifact name an operator is told to look at, and the documents that must name it.
DOCUMENTED_ARTIFACTS = {
    CAPTURE_FILENAME: ("COORDINATOR_HANDOFF.md", "docs/DEMO_RUNBOOK.md", "README.md"),
    SEED_PLAN_FILENAME: ("COORDINATOR_HANDOFF.md", "docs/DEMO_RUNBOOK.md"),
}


class TestTheConstantsAreTheRealFilenames:
    def test_the_capture_filename_is_what_it_has_always_been(self):
        """Pinned deliberately. A rename invalidates every operator's muscle memory."""
        assert CAPTURE_FILENAME == "pre_seed_capture.json"

    def test_the_plan_filenames_are_pinned(self):
        assert SEED_PLAN_FILENAME == "seed_plan.json"
        assert RESET_PLAN_FILENAME == "reset_plan.json"
        assert RESTORE_PLAN_FILENAME == "restore_plan.json"
        assert RECIPE_FILENAME == "ingestion_recipe.yaml"

    def test_capture_path_uses_the_constant_under_the_state_dir(self, seeded_settings):
        path = capture_path(seeded_settings)
        assert path.name == CAPTURE_FILENAME
        assert path.parent == seeded_settings.state_dir / "datahub"

    def test_write_capture_writes_exactly_that_path(self, seeded_settings):
        capture = {
            "kind": CAPTURE_KIND,
            "capture_version": CAPTURE_VERSION,
            "urn_prefix": "traffic.",
            "allocated": [],
            "entities": {},
            "absent": [],
        }
        written = write_capture(capture, seeded_settings)
        assert written == capture_path(seeded_settings)
        assert written.name == CAPTURE_FILENAME
        assert written.is_file()

    def test_the_missing_capture_error_names_the_path_to_create(self, seeded_settings):
        """An operator reading the refusal must learn where the file goes."""
        from graph_traffic_control.demo.datahub_state import PlanError, load_capture

        with pytest.raises(PlanError) as exc:
            load_capture(seeded_settings)
        assert CAPTURE_FILENAME in str(exc.value)
        assert "--allow-absent" in str(exc.value)


class TestTheDocumentationNamesThem:
    @pytest.mark.parametrize(
        ("artifact", "document"),
        [(a, d) for a, docs in DOCUMENTED_ARTIFACTS.items() for d in docs],
    )
    def test_the_document_names_the_artifact(self, artifact, document):
        text = (REPO_ROOT / document).read_text(encoding="utf-8")
        assert artifact in text, (
            f"{document} does not name {artifact}. An operator following it would have to guess "
            f"which file to inspect, which is how the last live gate went wrong."
        )

    def test_the_handoff_documents_the_first_time_seed_order(self):
        text = (REPO_ROOT / "COORDINATOR_HANDOFF.md").read_text(encoding="utf-8")
        assert "gtc-datahub-capture --allow-absent" in text
        assert text.index("gtc-datahub-capture") < text.index("gtc-datahub-seed --apply"), (
            "Capture must be documented before seed: a capture taken after a seed records this "
            "project's own rows as the state to return the shared instance to."
        )

    def test_the_capture_signature_still_offers_allow_absent(self):
        """The documented flag must remain the real one."""
        import inspect

        assert "allow_absent" in inspect.signature(capture_state).parameters
