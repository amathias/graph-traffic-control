"""Public-release safety scan.

``HACKATHON_RULES.md`` requires a public repository with an Apache 2.0 licence and no committed
secrets, private data, or unlicensed assets. ``AGENTS.md`` additionally forbids committing
``.env`` files, private keys, runtime receipts, and private evidence.

This scans **git-tracked content only**. Untracked scratch files are not what gets published, and
including them would produce noise that trains people to ignore the scan.

Findings are severity-ranked. ``blocker`` findings fail the scan; ``warning`` findings are
reported and do not. The distinction matters: a scan that fails on everything gets bypassed.

Run with ``gtc-safety-scan``. Exit code 0 means publishable, 1 means blockers were found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

BLOCKER = "blocker"
WARNING = "warning"

#: Content patterns that must never appear in a public repository.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "A private key is committed.",
    ),
    (
        "aws-access-key-id",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "An AWS access key id is committed.",
    ),
    (
        "aws-secret-access-key",
        re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}"),
        "An AWS secret access key is committed.",
    ),
    (
        "github-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        "A GitHub token is committed.",
    ),
    (
        "slack-token",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
        "A Slack token is committed.",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "A JSON Web Token is committed.",
    ),
    (
        "bearer-literal",
        re.compile(r"(?i)authorization\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._-]{16,}"),
        "A literal bearer credential is committed.",
    ),
    (
        "datahub-token-value",
        re.compile(r"(?i)\bDATAHUB_TOKEN\s*=\s*(?!$|\s|[\"']\s*[\"']|\$\{)\S+"),
        "DATAHUB_TOKEN is committed with a value. It must be supplied by the environment.",
    ),
]

#: Paths that must never be tracked at all.
FORBIDDEN_PATH_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "env-file",
        re.compile(r"(^|/)\.env(\.|$)(?!example)"),
        "An environment file is tracked. Only .env.example belongs in the repository.",
        BLOCKER,
    ),
    (
        "key-material",
        re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$"),
        "Key material is tracked.",
        BLOCKER,
    ),
    (
        "runtime-state",
        re.compile(r"(^|/)demo/state/"),
        "Runtime state is tracked. It is disposable output, not source.",
        BLOCKER,
    ),
    (
        "runtime-receipt",
        re.compile(r"(^|/)receipts?/.*\.json$"),
        "A runtime receipt is tracked. Receipts are private evidence, not source.",
        BLOCKER,
    ),
    (
        "database",
        re.compile(r"\.(sqlite|sqlite3|db)$"),
        "A database file is tracked.",
        BLOCKER,
    ),
    (
        "virtualenv",
        re.compile(r"(^|/)\.venv/"),
        "A virtual environment is tracked.",
        BLOCKER,
    ),
]

#: Extensions whose contents are not scanned for text patterns.
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".whl", ".mp4"}
)

#: Files exempt from content scanning, with the reason. Kept deliberately short and specific:
#: a broad exemption list is how a real finding gets hidden.
CONTENT_EXEMPT = {
    # This module defines the patterns it searches for.
    "src/graph_traffic_control/release/safety_scan.py",
    "tests/test_release_checks.py",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    path: str
    detail: str
    line: int | None = None

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{self.severity.upper():7}] {self.rule:24} {where}\n            {self.detail}"


def tracked_files(root: Path) -> list[str]:
    """Every file git tracks. Untracked scratch files are not what gets published."""
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(root), "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.strip()}")
    return sorted(line for line in result.stdout.splitlines() if line.strip())


def scan_paths(paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        for rule, pattern, detail, severity in FORBIDDEN_PATH_PATTERNS:
            if pattern.search(path):
                findings.append(Finding(severity, rule, path, detail))
    return findings


def scan_contents(root: Path, paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if path in CONTENT_EXEMPT or Path(path).suffix.lower() in BINARY_SUFFIXES:
            continue
        full = root / path
        try:
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern, detail in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(Finding(BLOCKER, rule, path, detail, number))
    return findings


def check_licence(root: Path, paths: list[str]) -> list[Finding]:
    """The rules require a visible, detectable Apache 2.0 licence at the repository top level."""
    if "LICENSE" not in paths:
        return [
            Finding(
                BLOCKER,
                "licence-missing",
                "LICENSE",
                "No LICENSE at the repository root. The rules require a visible Apache 2.0 file.",
            )
        ]
    body = (root / "LICENSE").read_text(encoding="utf-8", errors="replace")
    if "Apache License" not in body or "Version 2.0" not in body:
        return [
            Finding(
                BLOCKER,
                "licence-not-apache-2",
                "LICENSE",
                "LICENSE is present but is not the Apache License 2.0.",
            )
        ]
    return []


def check_gitignore(root: Path, paths: list[str]) -> list[Finding]:
    """A passing scan today is worth little if the next run can commit a secret."""
    if ".gitignore" not in paths:
        return [
            Finding(
                BLOCKER,
                "gitignore-missing",
                ".gitignore",
                "No .gitignore, so nothing prevents committing .env or runtime state.",
            )
        ]
    body = (root / ".gitignore").read_text(encoding="utf-8")
    findings = []
    for needed, why in (
        (".env", "environment files could be committed"),
        ("demo/state", "runtime state and receipts could be committed"),
    ):
        if needed not in body:
            findings.append(
                Finding(
                    BLOCKER,
                    "gitignore-incomplete",
                    ".gitignore",
                    f"{needed!r} is not ignored, so {why}.",
                )
            )
    return findings


def check_local_paths(root: Path, paths: list[str]) -> list[Finding]:
    """Absolute developer paths leak a machine layout and break for everyone else."""
    pattern = re.compile(r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9._-]+")
    findings = []
    for path in paths:
        if path in CONTENT_EXEMPT or Path(path).suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                findings.append(
                    Finding(
                        WARNING,
                        "absolute-local-path",
                        path,
                        "An absolute developer path is committed.",
                        number,
                    )
                )
    return findings


def scan(root: Path | None = None) -> list[Finding]:
    root = root or REPO_ROOT
    paths = tracked_files(root)
    return [
        *scan_paths(paths),
        *scan_contents(root, paths),
        *check_licence(root, paths),
        *check_gitignore(root, paths),
        *check_local_paths(root, paths),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan git-tracked content for anything that must not be published."
    )
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as blockers."
    )
    args = parser.parse_args(argv)

    try:
        findings = scan()
    except RuntimeError as exc:
        print(f"Safety scan failed: {exc}", file=sys.stderr)
        return 2

    blockers = [f for f in findings if f.severity == BLOCKER]
    warnings = [f for f in findings if f.severity == WARNING]

    for finding in [*blockers, *warnings]:
        print(finding.render())

    print(
        f"\nSafety scan: {len(blockers)} blocker(s), {len(warnings)} warning(s) "
        f"across {len(tracked_files(REPO_ROOT))} tracked files."
    )
    if blockers or (args.strict and warnings):
        print("NOT safe to publish.", file=sys.stderr)
        return 1
    print("Safe to publish.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
