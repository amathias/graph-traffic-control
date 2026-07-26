"""Archive verification: build the distributions and prove they are usable and clean.

``HACKATHON_RULES.md`` requires that the project installs and runs consistently from a clean
checkout, and the coordinator's handoff format requires an immutable artifact identifier. Neither
is worth much unless someone has actually installed the artifact — a build that silently omits a
file is discovered by whoever installs it, which should not be a judge.

Four things are checked:

1. **Both distributions build.** sdist and wheel.
2. **The wheel carries everything the app needs at runtime**, notably the judge console HTML.
   A missing data file is the classic packaging failure: tests pass from the source tree and the
   installed package serves a 500.
3. **Neither distribution carries anything it must not** — no ``.env``, no runtime state, no
   receipts, no database, no key material.
4. **A clean install works.** The wheel is installed into a fresh virtual environment with no
   access to this project's source tree, and its console scripts are executed there.

Run with ``gtc-archive-verify``. Exit code 0 means the artifact is fit to hand over.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Files the installed wheel must contain. The console HTML is listed explicitly because it is
#: the only non-Python runtime asset, and therefore the only one a packaging change can drop
#: without any test in the source tree noticing.
REQUIRED_WHEEL_MEMBERS = (
    "graph_traffic_control/api.py",
    "graph_traffic_control/web/index.html",
    "graph_traffic_control/demo/datahub_state.py",
    "graph_traffic_control/release/safety_scan.py",
)

#: Distribution members that must not ship. Matched precisely rather than by substring: an
#: earlier substring form flagged ``.env.example``, which is documentation the project is
#: supposed to publish. A check that cries wolf on a legitimate file gets switched off.
FORBIDDEN_MEMBER_PATTERNS = (
    re.compile(r"(^|/)\.env(\.|$)(?!example)"),
    re.compile(r"(^|/)demo/state/"),
    re.compile(r"(^|/)receipts?/"),
    re.compile(r"\.(sqlite|sqlite3|db)$"),
    re.compile(r"\.(pem|key|p12|pfx|jks)$"),
    re.compile(r"(^|/)\.venv/"),
)

CONSOLE_SCRIPTS = (
    "gtc-api",
    "gtc-seed",
    "gtc-reset",
    "gtc-demo",
    "gtc-datahub-seed",
    "gtc-datahub-reset",
    "gtc-datahub-capture",
    "gtc-datahub-restore",
    "gtc-safety-scan",
    "gtc-archive-verify",
)


@dataclass
class Result:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        return ok

    @property
    def ok(self) -> bool:
        return all(ok for _name, ok, _detail in self.checks)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def build_distributions(root: Path, outdir: Path) -> tuple[Path, Path]:
    """Build sdist and wheel with the project's declared backend."""
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "build", "--outdir", str(outdir), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(outdir.glob("*.whl"))
    sdists = sorted(outdir.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise RuntimeError(f"build produced {len(sdists)} sdist(s) and {len(wheels)} wheel(s)")
    return sdists[-1], wheels[-1]


def wheel_members(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return sorted(archive.namelist())


def sdist_members(sdist: Path) -> list[str]:
    with tarfile.open(sdist, "r:gz") as archive:
        # Strip the leading `name-version/` component so paths compare like repo paths.
        return sorted(name.split("/", 1)[-1] for name in archive.getnames())


def forbidden_members(members: list[str]) -> list[str]:
    return sorted(
        member
        for member in members
        if any(pattern.search(member) for pattern in FORBIDDEN_MEMBER_PATTERNS)
    )


def verify_clean_install(wheel: Path, result: Result) -> None:
    """Install the wheel into a fresh environment and run it there.

    The working directory is a temporary one, deliberately not the repository: running from the
    source tree would let the checkout satisfy imports and data-file lookups that the wheel
    itself might be missing, which is exactly the failure being tested for.
    """
    with tempfile.TemporaryDirectory(prefix="gtc-archive-") as tmp:
        workdir = Path(tmp)
        venv = workdir / "venv"
        subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True
        )
        bindir = venv / ("Scripts" if sys.platform == "win32" else "bin")
        python = bindir / ("python.exe" if sys.platform == "win32" else "python")

        install = subprocess.run(  # noqa: S603 - fixed argv
            [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
            capture_output=True,
            text=True,
            check=False,
        )
        if not result.record(
            "clean install succeeds",
            install.returncode == 0,
            install.stderr.strip()[-400:],
        ):
            return

        # The console HTML must be readable from the *installed* package, not the source tree.
        probe = subprocess.run(  # noqa: S603 - fixed argv
            [
                str(python),
                "-c",
                "from graph_traffic_control.api import UI_INDEX;"
                "assert UI_INDEX.is_file(), UI_INDEX;"
                "assert 'Graph Traffic Control' in UI_INDEX.read_text(encoding='utf-8');"
                "print('ui-ok')",
            ],
            capture_output=True,
            text=True,
            cwd=workdir,
            check=False,
        )
        result.record(
            "installed package serves the judge console",
            probe.returncode == 0 and "ui-ok" in probe.stdout,
            probe.stderr.strip()[-400:],
        )

        missing = [
            name
            for name in CONSOLE_SCRIPTS
            if not (bindir / name).exists() and not (bindir / f"{name}.exe").exists()
        ]
        result.record(
            "every console script is installed",
            not missing,
            f"missing: {missing}" if missing else "",
        )

        # Seed entirely inside the temporary directory. This proves the installed package is
        # usable without the repository present. The fixture is a repository input rather than
        # packaged data, so its path is passed in explicitly.
        env_state = workdir / "state"
        fixture_root = REPO_ROOT / "demo" / "fixtures" / "graph-traffic-control"
        program = (
            "import os, sys;"
            f"os.environ['APP_STATE_DIR']={str(env_state)!r};"
            f"os.environ['DEMO_FIXTURE_ROOT']={str(fixture_root)!r};"
            "from graph_traffic_control.demo.seed import main;"
            "sys.exit(main([]))"
        )
        seeded = subprocess.run(  # noqa: S603 - fixed argv
            [str(python), "-c", program],
            capture_output=True,
            text=True,
            cwd=workdir,
            check=False,
        )
        result.record(
            "packaged seed runs outside the source tree",
            seeded.returncode == 0,
            seeded.stderr.strip()[-400:],
        )


def verify(root: Path | None = None, outdir: Path | None = None) -> Result:
    root = root or REPO_ROOT
    result = Result()

    with tempfile.TemporaryDirectory(prefix="gtc-dist-") as tmp:
        target = outdir or Path(tmp)
        target.mkdir(parents=True, exist_ok=True)

        try:
            sdist, wheel = build_distributions(root, target)
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            result.record("distributions build", False, str(detail)[-600:])
            return result
        result.record("distributions build", True, f"{sdist.name}, {wheel.name}")

        result.artifacts = {
            sdist.name: sha256_of(sdist),
            wheel.name: sha256_of(wheel),
        }

        members = wheel_members(wheel)
        missing = [m for m in REQUIRED_WHEEL_MEMBERS if m not in members]
        result.record(
            "wheel carries every required runtime file",
            not missing,
            f"missing: {missing}" if missing else "",
        )

        for label, found in (
            ("wheel", forbidden_members(members)),
            ("sdist", forbidden_members(sdist_members(sdist))),
        ):
            result.record(
                f"{label} carries nothing it must not",
                not found,
                f"found: {found}" if found else "",
            )

        verify_clean_install(wheel, result)

        if outdir is None:
            # Copy nothing out; the temporary directory is about to disappear. The digests
            # above are still reported so a coordinator can rebuild and compare.
            pass
        else:
            shutil.copy2(sdist, target / sdist.name)
            shutil.copy2(wheel, target / wheel.name)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the distributions and verify they install and run cleanly."
    )
    parser.add_argument(
        "--outdir",
        help="Keep the built distributions in this directory instead of a temporary one.",
    )
    args = parser.parse_args(argv)

    result = verify(outdir=Path(args.outdir) if args.outdir else None)

    for name, ok, detail in result.checks:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}")
        if detail:
            print(f"       {detail}")

    if result.artifacts:
        print("\nArtifact digests (sha256):")
        for name, digest in sorted(result.artifacts.items()):
            print(f"  {digest}  {name}")

    if not result.ok:
        print("\nArchive verification FAILED.", file=sys.stderr)
        return 1
    print("\nArchive verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
