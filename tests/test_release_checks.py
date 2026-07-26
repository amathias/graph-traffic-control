"""Public-release safety scanning and archive verification.

These guard the pre-submission audit in ``HACKATHON_RULES.md``: a public repository, an Apache
2.0 licence, and no committed secrets or private evidence.

Two things are tested. First, the scanner **detects** what it claims to (asserted against
synthetic repositories, so a real secret is never needed to prove the scanner works). Second,
**this repository currently passes**, which is the check that actually gates publication.

The full archive verification builds distributions and creates a virtual environment, which is
too slow for every suite run. Its pure helpers are covered here; the end-to-end run is a release
command (``gtc-archive-verify``).
"""

from __future__ import annotations

import subprocess

import pytest

from graph_traffic_control.release import archive, safety_scan

APACHE_HEADER = "Apache License\nVersion 2.0, January 2004\n"


@pytest.fixture
def repo(tmp_path):
    """A minimal, clean git repository the scanner can be pointed at."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "LICENSE").write_text(APACHE_HEADER, encoding="utf-8")
    (root / ".gitignore").write_text(".env\ndemo/state/\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    return root


def commit_all(root, force=False):
    """Stage and commit.

    ``force`` bypasses .gitignore, which is how a secret realistically lands in a repository:
    someone runs `git add -f` on a file the ignore rules were already protecting them from.
    Without it, the scanner would have nothing to find and the test would prove nothing.
    """
    subprocess.run(
        ["git", "add", "-A"] + (["-f"] if force else []), cwd=root, check=True
    )
    subprocess.run(["git", "commit", "-qm", "test"], cwd=root, check=True)


def severities(findings, rule):
    return [f.severity for f in findings if f.rule == rule]


class TestScannerDetectsWhatItClaimsTo:
    def test_a_clean_repository_passes(self, repo):
        commit_all(repo)
        assert safety_scan.scan(repo) == []

    def test_a_committed_private_key_is_a_blocker(self, repo):
        (repo / "id_rsa.txt").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n", encoding="utf-8"
        )
        commit_all(repo)
        assert safety_scan.BLOCKER in severities(safety_scan.scan(repo), "private-key")

    @pytest.mark.parametrize(
        "rule,content",
        [
            ("aws-access-key-id", "key = AKIA" + "A" * 16),
            ("github-token", "token = ghp_" + "a" * 36),
            ("slack-token", "token = xoxb-1234567890-abcdefghij"),
            ("jwt", "auth = eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2Q"),
            ("bearer-literal", 'Authorization: "Bearer abcdefghij0123456789"'),
        ],
    )
    def test_credential_shapes_are_blockers(self, repo, rule, content):
        (repo / "config.txt").write_text(content + "\n", encoding="utf-8")
        commit_all(repo)
        assert safety_scan.BLOCKER in severities(safety_scan.scan(repo), rule)

    def test_a_populated_datahub_token_is_a_blocker(self, repo):
        (repo / "settings.env.txt").write_text(
            "DATAHUB_TOKEN=real-secret-value\n", encoding="utf-8"
        )
        commit_all(repo)
        assert safety_scan.BLOCKER in severities(
            safety_scan.scan(repo), "datahub-token-value"
        )

    def test_an_empty_datahub_token_is_fine(self, repo):
        """`.env.example` legitimately names the variable. Flagging it would train people to
        ignore the scan."""
        (repo / "env.example.txt").write_text("DATAHUB_TOKEN=\n", encoding="utf-8")
        commit_all(repo)
        assert severities(safety_scan.scan(repo), "datahub-token-value") == []

    @pytest.mark.parametrize(
        "rule,path",
        [
            ("env-file", ".env"),
            ("env-file", "config/.env.production"),
            ("key-material", "deploy.pem"),
            ("runtime-state", "demo/state/transactions.sqlite"),
            ("runtime-receipt", "receipts/commit-x.json"),
            ("database", "data.sqlite"),
        ],
    )
    def test_forbidden_paths_are_blockers(self, repo, rule, path):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        # Force-added past .gitignore, which is how these files actually get committed.
        commit_all(repo, force=True)
        assert safety_scan.BLOCKER in severities(safety_scan.scan(repo), rule)

    def test_a_dot_env_example_is_allowed(self, repo):
        (repo / ".env.example").write_text("DATAHUB_TOKEN=\n", encoding="utf-8")
        commit_all(repo)
        assert severities(safety_scan.scan(repo), "env-file") == []

    def test_a_missing_licence_is_a_blocker(self, repo):
        (repo / "LICENSE").unlink()
        commit_all(repo)
        assert severities(safety_scan.scan(repo), "licence-missing") == [safety_scan.BLOCKER]

    def test_a_non_apache_licence_is_a_blocker(self, repo):
        (repo / "LICENSE").write_text("MIT License\n", encoding="utf-8")
        commit_all(repo)
        assert severities(safety_scan.scan(repo), "licence-not-apache-2") == [
            safety_scan.BLOCKER
        ]

    def test_an_incomplete_gitignore_is_a_blocker(self, repo):
        """Passing today is worth little if tomorrow's commit can add a secret."""
        (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
        commit_all(repo)
        assert severities(safety_scan.scan(repo), "gitignore-incomplete")

    def test_an_absolute_developer_path_is_a_warning_not_a_blocker(self, repo):
        (repo / "notes.md").write_text(
            "see C:/Users/someone/project/file.txt\n", encoding="utf-8"
        )
        commit_all(repo)
        assert severities(safety_scan.scan(repo), "absolute-local-path") == [
            safety_scan.WARNING
        ]

    def test_untracked_files_are_not_scanned(self, repo):
        """Only tracked content gets published; scanning scratch files trains people to ignore
        the scan."""
        commit_all(repo)
        (repo / "scratch.txt").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8"
        )
        assert safety_scan.scan(repo) == []


class TestThisRepositoryIsPublishable:
    """The check that actually gates making the repository public."""

    def test_no_blockers(self):
        blockers = [f for f in safety_scan.scan() if f.severity == safety_scan.BLOCKER]
        assert blockers == [], "\n".join(f.render() for f in blockers)

    def test_no_warnings(self):
        warnings = [f for f in safety_scan.scan() if f.severity == safety_scan.WARNING]
        assert warnings == [], "\n".join(f.render() for f in warnings)

    def test_the_scan_exits_zero(self, capsys):
        assert safety_scan.main([]) == 0
        assert "Safe to publish" in capsys.readouterr().out


class TestArchiveMemberRules:
    @pytest.mark.parametrize(
        "member",
        [
            ".env",
            "config/.env.production",
            "demo/state/transactions.sqlite",
            "graph_traffic_control/receipts/x.json",
            "data.sqlite",
            "deploy.pem",
            ".venv/pyvenv.cfg",
        ],
    )
    def test_forbidden_members_are_caught(self, member):
        assert archive.forbidden_members([member]) == [member]

    @pytest.mark.parametrize(
        "member",
        [
            ".env.example",
            "graph_traffic_control/web/index.html",
            "graph_traffic_control/receipts.py",
            "README.md",
        ],
    )
    def test_legitimate_members_are_not_caught(self, member):
        """`.env.example` and `receipts.py` are published on purpose. A check that flags them
        is a check people learn to override."""
        assert archive.forbidden_members([member]) == []

    def test_the_judge_console_is_a_required_wheel_member(self):
        """The only non-Python runtime asset, so the only one a packaging change can silently
        drop while the source-tree tests stay green."""
        assert "graph_traffic_control/web/index.html" in archive.REQUIRED_WHEEL_MEMBERS

    def test_every_console_script_declared_in_pyproject_is_verified(self):
        import tomllib

        from graph_traffic_control.config import REPO_ROOT

        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            declared = set(tomllib.load(handle)["project"]["scripts"])
        assert declared == set(archive.CONSOLE_SCRIPTS), (
            "a console script was added without adding it to archive verification"
        )
