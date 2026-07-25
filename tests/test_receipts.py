"""Receipts must be complete evidence and must never carry a secret."""

from __future__ import annotations

import json

from graph_traffic_control.demo.agents import proposal_a, proposal_c
from graph_traffic_control.receipts import (
    REDACTED,
    ReceiptWriter,
    sanitize,
    token_fingerprint,
)
from graph_traffic_control.txn.coordinator import Coordinator


class TestSanitize:
    def test_secret_bearing_keys_are_redacted(self):
        payload = {"datahub_token": "abc123", "Authorization": "Bearer xyz", "name": "fine"}
        clean = sanitize(payload)
        assert clean["datahub_token"] == REDACTED
        assert clean["Authorization"] == REDACTED
        assert clean["name"] == "fine"

    def test_literal_secret_values_are_redacted_anywhere(self):
        clean = sanitize({"detail": "failed with tok-secret in the body"}, ("tok-secret",))
        assert "tok-secret" not in clean["detail"]
        assert REDACTED in clean["detail"]

    def test_nested_structures_are_walked(self):
        payload = {"a": [{"api_key": "k"}, {"b": {"password": "p"}}]}
        clean = sanitize(payload)
        assert clean["a"][0]["api_key"] == REDACTED
        assert clean["a"][1]["b"]["password"] == REDACTED

    def test_empty_secret_does_not_redact_everything(self):
        clean = sanitize({"detail": "ordinary text"}, ("",))
        assert clean["detail"] == "ordinary text"

    def test_fingerprint_keys_survive_redaction(self):
        """A one-way digest is not a secret, and is the evidence that ties receipts together."""
        clean = sanitize({"prepared_token_fingerprint": "abc123", "token": "raw"})
        assert clean["prepared_token_fingerprint"] == "abc123"
        assert clean["token"] == REDACTED

    def test_non_string_scalars_pass_through(self):
        assert sanitize({"count": 3, "ok": True, "none": None}) == {
            "count": 3,
            "ok": True,
            "none": None,
        }


class TestReceiptContents:
    def test_proposal_receipt_records_impact_and_conflicts(
        self, coordinator: Coordinator, seeded_settings, versions
    ):
        writer = ReceiptWriter(seeded_settings.state_dir)
        a = proposal_a(versions)
        outcome = coordinator.prepare(a)

        path = writer.proposal_receipt(
            proposal=a,
            impact=outcome.impact,
            conflicts=outcome.conflicts,
            state=outcome.state,
            context_source="fixture",
            snapshot_fingerprint=outcome.token.snapshot_fingerprint,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload["kind"] == "proposal"
        assert payload["proposal_id"] == a.proposal_id
        assert payload["context_source"] == "fixture"
        assert payload["impact"]["blast_radius"] >= 2

    def test_lease_receipt_records_expiry_and_token(
        self, coordinator, seeded_settings, versions
    ):
        writer = ReceiptWriter(seeded_settings.state_dir)
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        path = writer.lease_receipt(outcome.lease, outcome.token)
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload["kind"] == "lease"
        assert payload["expires_at"] > payload["granted_at"]
        assert payload["prepared_token_fingerprint"] == token_fingerprint(outcome.token.token)

    def test_lease_receipt_never_stores_the_raw_capability_token(
        self, coordinator, seeded_settings, versions
    ):
        """Holding a prepared token permits a commit, so it must not sit in a file."""
        writer = ReceiptWriter(seeded_settings.state_dir)
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        text = writer.lease_receipt(outcome.lease, outcome.token).read_text(encoding="utf-8")
        assert outcome.token.token not in text

    def test_commit_receipt_records_both_fingerprints_and_drift_flag(
        self, coordinator, seeded_settings, versions, store
    ):
        writer = ReceiptWriter(seeded_settings.state_dir)
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        result = coordinator.commit(c, outcome.token)

        path = writer.commit_receipt(
            proposal=c,
            final_state=result.state,
            events=store.list_events(c.proposal_id),
            context_source="fixture",
            prepare_fingerprint=result.prepare_fingerprint,
            commit_fingerprint=result.commit_fingerprint,
            artifact_diff=result.artifact_diff,
            validation=result.validation,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload["final_state"] == "COMMITTED"
        assert payload["drift_detected"] is False
        assert payload["graph_fingerprint_at_prepare"]
        assert len(payload["events"]) >= 5

    def test_receipts_are_deterministic_json(self, seeded_settings, versions, coordinator):
        writer = ReceiptWriter(seeded_settings.state_dir)
        c = proposal_c(versions)
        outcome = coordinator.prepare(c)
        first = writer.lease_receipt(outcome.lease, outcome.token).read_text(encoding="utf-8")
        second = writer.lease_receipt(outcome.lease, outcome.token).read_text(encoding="utf-8")
        assert first == second


class TestNoSecretsReachDisk:
    def test_token_never_appears_in_a_written_receipt(
        self, coordinator, seeded_settings, versions
    ):
        secret = "super-secret-datahub-token"  # noqa: S105 - deliberately fake
        writer = ReceiptWriter(seeded_settings.state_dir, secrets=(secret,))
        a = proposal_a(versions)
        a.evidence.append(f"context fetched with {secret}")

        outcome = coordinator.prepare(a)
        path = writer.proposal_receipt(
            proposal=a,
            impact=outcome.impact,
            conflicts=outcome.conflicts,
            state=outcome.state,
            context_source="fixture",
            snapshot_fingerprint="abc",
        )

        text = path.read_text(encoding="utf-8")
        assert secret not in text
        assert REDACTED in text

    def test_receipts_live_under_the_state_directory(self, seeded_settings):
        writer = ReceiptWriter(seeded_settings.state_dir)
        assert writer.directory.parent == seeded_settings.state_dir
