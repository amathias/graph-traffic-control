"""Namespace isolation tests.

These back the coordinator integration gate "namespace and reset isolation tests pass".
The guard must fail closed: anything it cannot positively prove to be inside the ``traffic.``
allocation has to raise.
"""

from __future__ import annotations

import pytest

from graph_traffic_control.context.namespace import (
    Namespace,
    NamespaceViolation,
    require_contained_path,
)

NAMESPACE = Namespace(
    urn_prefix="traffic.",
    project_tag="project-graph-traffic-control",
    domain="Demo / Graph Traffic Control",
)

OURS = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.fct_revenue,PROD)"

# Real entity prefixes belonging to the other four submissions, from COORDINATOR_PLAN.md.
OTHER_PROJECTS = [
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.fct_revenue,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.fct_revenue,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,forgetme.fct_revenue,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.fct_revenue,PROD)",
]


class TestEntityNameExtraction:
    def test_dataset_name(self):
        assert NAMESPACE.entity_name(OURS) == "traffic.fct_revenue"

    def test_dashboard_name(self):
        urn = "urn:li:dashboard:(looker,traffic.dash_exec_revenue)"
        assert NAMESPACE.entity_name(urn) == "traffic.dash_exec_revenue"

    def test_tag_name_is_flat(self):
        urn = "urn:li:tag:project-graph-traffic-control"
        assert NAMESPACE.entity_name(urn) == "project-graph-traffic-control"

    def test_schema_field_recurses_into_parent_dataset(self):
        urn = f"urn:li:schemaField:({OURS},gross_revenue)"
        assert NAMESPACE.entity_name(urn) == "traffic.fct_revenue"

    def test_platform_urn_containing_commas_does_not_break_parsing(self):
        # The platform component is itself a URN; splitting must respect nesting.
        urn = "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.stg_sales,PROD)"
        assert NAMESPACE.entity_name(urn) == "traffic.stg_sales"

    def test_data_job_wrapping_a_data_flow_urn(self):
        flow = "urn:li:dataFlow:(airflow,traffic.revenue_dag,prod)"
        urn = f"urn:li:dataJob:({flow},traffic.rebuild_revenue)"
        assert NAMESPACE.entity_name(urn) == "traffic.rebuild_revenue"


class TestFailClosed:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "not-a-urn",
            "urn:li:dataset:",
            "urn:li:dataset:(urn:li:dataPlatform:duckdb",  # unbalanced
            "urn:li:dataset:urn:li:dataPlatform:duckdb,traffic.x,PROD",  # missing parens
            "urn:li:dataset:(urn:li:dataPlatform:duckdb)",  # too few components
            "urn:li:dataset:(urn:li:dataPlatform:duckdb,,PROD)",  # empty name
        ],
    )
    def test_malformed_urns_raise(self, bad):
        with pytest.raises(NamespaceViolation):
            NAMESPACE.entity_name(bad)

    def test_unknown_entity_type_raises_rather_than_guessing(self):
        with pytest.raises(NamespaceViolation, match="Unsupported entity type"):
            NAMESPACE.entity_name("urn:li:someFutureEntity:(traffic.thing,PROD)")

    def test_tag_urn_with_tuple_body_raises(self):
        with pytest.raises(NamespaceViolation):
            NAMESPACE.entity_name("urn:li:tag:(traffic.x,PROD)")

    def test_non_string_raises(self):
        with pytest.raises(NamespaceViolation):
            NAMESPACE.entity_name(None)  # type: ignore[arg-type]

    def test_contains_never_raises_on_malformed_input(self):
        assert NAMESPACE.contains("not-a-urn") is False
        assert NAMESPACE.contains("") is False


class TestRequire:
    def test_in_namespace_urn_passes_through(self):
        assert NAMESPACE.require(OURS, operation="Commit") == OURS

    @pytest.mark.parametrize("foreign", OTHER_PROJECTS)
    def test_other_projects_entities_are_refused(self, foreign):
        with pytest.raises(NamespaceViolation, match="outside"):
            NAMESPACE.require(foreign, operation="Commit")

    def test_violation_message_names_the_operation(self):
        with pytest.raises(NamespaceViolation, match="Reset refused|Reset"):
            NAMESPACE.require(OTHER_PROJECTS[0], operation="Reset")

    def test_prefix_must_match_at_the_start_not_anywhere(self):
        sneaky = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.traffic.fct_revenue,PROD)"
        with pytest.raises(NamespaceViolation):
            NAMESPACE.require(sneaky, operation="Commit")

    def test_require_all_raises_before_any_partial_work(self):
        urns = [OURS, OTHER_PROJECTS[0], OURS]
        with pytest.raises(NamespaceViolation):
            NAMESPACE.require_all(urns, operation="Writeback")

    def test_require_all_returns_every_urn_when_all_pass(self):
        assert NAMESPACE.require_all([OURS, OURS], operation="Writeback") == [OURS, OURS]

    def test_schema_field_of_a_foreign_dataset_is_refused(self):
        foreign_field = f"urn:li:schemaField:({OTHER_PROJECTS[0]},gross_revenue)"
        with pytest.raises(NamespaceViolation):
            NAMESPACE.require(foreign_field, operation="Column rename")


class TestRequireTag:
    def test_project_tag_passes(self):
        urn = "urn:li:tag:project-graph-traffic-control"
        assert NAMESPACE.require_tag(urn, operation="Writeback") == urn

    def test_another_projects_tag_is_refused(self):
        with pytest.raises(NamespaceViolation, match="not this project's tag"):
            NAMESPACE.require_tag("urn:li:tag:project-lineage-fuzzer", operation="Writeback")


class TestPathContainment:
    def test_child_path_is_allowed(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        child = root / "proposals.sqlite"
        child.write_text("x", encoding="utf-8")
        assert require_contained_path(child, root, operation="Reset") == child.resolve()

    def test_root_itself_is_allowed(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        assert require_contained_path(root, root, operation="Reset") == root.resolve()

    def test_parent_escape_is_refused(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        outside = tmp_path / "someone-elses-data"
        outside.mkdir()
        with pytest.raises(NamespaceViolation, match="outside"):
            require_contained_path(outside, root, operation="Reset")

    def test_dot_dot_traversal_is_refused(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        with pytest.raises(NamespaceViolation, match="outside"):
            require_contained_path(root / ".." / "elsewhere", root, operation="Reset")
