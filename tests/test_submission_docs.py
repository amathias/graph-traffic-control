"""The submission package: public URLs, working links, and no leftover placeholders.

Judges may score this project from the description, the repository, and the video alone — the
rules say so explicitly. That makes a dead relative link or an unreplaced `TODO` a scoring defect,
not a cosmetic one, and it makes the public URLs load-bearing: they are how a judge reaches the
running application at all.

These are cheap assertions against expensive failures. A broken link in a judged repository is
found by the judge, and by then it is too late to fix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

APP_URL = "https://traffic.datahub-hackathon.aaronmathias.com"
REPO_URL = "https://github.com/amathias/graph-traffic-control"

#: Documents a judge or the coordinator actually reads.
SUBMISSION_DOCS = [
    "README.md",
    "SUBMISSION.md",
    "COORDINATOR_HANDOFF.md",
    "docs/SUBMISSION.md",
    "docs/LIMITATIONS.md",
    "docs/DEMO_RUNBOOK.md",
]

#: Markers that mean "someone meant to come back to this". `placeholder` itself is excluded: the
#: handoff legitimately instructs a reader to replace placeholder values in a deployment request.
PLACEHOLDER_PATTERN = re.compile(
    r"\bTODO\b|\bTBD\b|\bFIXME\b|\bXXXX\b|example\.com|<your-|YOUR_URL|coming soon",
    re.IGNORECASE,
)

#: Relative links to repository files, e.g. `](./docs/DECISIONS.md)`.
RELATIVE_LINK_PATTERN = re.compile(r"\]\((\.{0,2}/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)\)")


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _flat(name: str) -> str:
    """Document text with wrapping and emphasis removed, so a re-wrap cannot break an assertion."""
    return re.sub(r"\s+", " ", _read(name).replace("*", ""))


class TestThePublicUrlsArePresent:
    """A judge must be able to reach the running app and the source from the submission copy."""

    @pytest.mark.parametrize("document", ["README.md", "SUBMISSION.md", "docs/SUBMISSION.md"])
    def test_the_app_url_is_published(self, document):
        assert APP_URL in _read(document)

    @pytest.mark.parametrize("document", ["README.md", "SUBMISSION.md", "docs/SUBMISSION.md"])
    def test_the_repository_url_is_published(self, document):
        assert REPO_URL in _read(document)

    def test_the_handoff_records_both_urls(self):
        text = _read("COORDINATOR_HANDOFF.md")
        assert APP_URL in text
        assert REPO_URL in text

    def test_the_urls_are_https(self):
        assert APP_URL.startswith("https://")
        assert REPO_URL.startswith("https://")


class TestNoLeftoverPlaceholders:
    @pytest.mark.parametrize("document", SUBMISSION_DOCS)
    def test_the_document_has_no_placeholder_markers(self, document):
        found = sorted({m.group(0) for m in PLACEHOLDER_PATTERN.finditer(_read(document))})
        assert not found, f"{document} still contains placeholder marker(s): {found}"


class TestEveryRelativeLinkResolves:
    @pytest.mark.parametrize("document", SUBMISSION_DOCS)
    def test_links_point_at_files_that_exist(self, document):
        base = (REPO_ROOT / document).parent
        broken = []
        for match in RELATIVE_LINK_PATTERN.finditer(_read(document)):
            target = match.group(1)
            resolved = (REPO_ROOT / target[2:]) if target.startswith("./") else (base / target)
            if not resolved.exists():
                broken.append(target)
        assert not broken, f"{document} links to missing file(s): {sorted(set(broken))}"


class TestTheLiveGateIsRecordedTruthfully:
    """The docs must not still describe the gate as outstanding now that it has passed."""

    def test_the_handoff_records_the_deployed_product(self):
        assert "5ea880f61122f052210d014906fe5eab2c356851" in _read("COORDINATOR_HANDOFF.md")

    def test_the_handoff_records_the_final_receipt_digest(self):
        digest = "621e022bc1253990be5fe328da8186ecc6be2d675d8242514d3ef81866db8782"
        assert digest in _read("COORDINATOR_HANDOFF.md")

    @pytest.mark.parametrize("document", ["README.md", "docs/LIMITATIONS.md"])
    def test_no_document_still_claims_the_seed_was_never_applied(self, document):
        text = _read(document)
        assert "There are no live receipts" not in text
        assert "No plan in this repository has been applied to a live instance" not in text

    def test_the_runbook_does_not_tell_anyone_to_reseed(self):
        """The instance is already seeded; a capture taken now would poison the restore."""
        assert "Do not run `gtc-datahub-capture`" in _flat("docs/DEMO_RUNBOOK.md")

    def test_the_workspace_still_disclaims_its_own_connection(self):
        """The gate was the coordinator's. This workspace has never connected, and must say so."""
        assert (
            "No connection to the shared instance has ever been made from this workspace"
            in _flat("README.md")
        )
