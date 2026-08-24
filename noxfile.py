"""
noxfile.py — Build pipeline for the recipes project.

Commands:
    nox                  # run the full pipeline (lint → typecheck → test → docker)
    nox -s lint          # just lint + format check
    nox -s typecheck     # just mypy
    nox -s test          # just pytest
    nox -s docker        # just build the Docker image
    nox -s fmt           # auto-fix formatting in place (not part of default pipeline)
"""

import os

import nox

# Use uv for all venv creation — fast, deterministic
nox.options.default_venv_backend = "uv"

# Sessions that run when you call bare `nox`
nox.options.sessions = ["lint", "typecheck", "test", "docker"]

IMAGE_NAME = os.environ.get("IMAGE_NAME", "recipes")
IMAGE_TAG = os.environ.get("IMAGE_TAG", "latest")


def _install(session: nox.Session) -> None:
    """Install the package + dev dependencies via uv."""
    session.run_install(
        "uv",
        "sync",
        "--group",
        "dev",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


# ---------------------------------------------------------------------------
# lint — ruff check + ruff format (read-only, fails on violations)
# ---------------------------------------------------------------------------
@nox.session(python="3.12")
def lint(session: nox.Session) -> None:
    """Lint with ruff and verify formatting."""
    _install(session)
    session.run("ruff", "check", "src", "tests", "noxfile.py")
    session.run("ruff", "format", "--check", "src", "tests", "noxfile.py")


# ---------------------------------------------------------------------------
# fmt — auto-fix formatting (run manually, not in default pipeline)
# ---------------------------------------------------------------------------
@nox.session(python="3.12")
def fmt(session: nox.Session) -> None:
    """Auto-fix lint violations and reformat code in place."""
    _install(session)
    session.run("ruff", "check", "--fix", "src", "tests", "noxfile.py")
    session.run("ruff", "format", "src", "tests", "noxfile.py")


# ---------------------------------------------------------------------------
# typecheck — mypy strict
# ---------------------------------------------------------------------------
@nox.session(python="3.12")
def typecheck(session: nox.Session) -> None:
    """Type-check with mypy (strict mode)."""
    _install(session)
    session.run("mypy", "src/recipes")


# ---------------------------------------------------------------------------
# test — pytest with coverage
# ---------------------------------------------------------------------------
@nox.session(python="3.12")
def test(session: nox.Session) -> None:
    """Run pytest with coverage. Fails if coverage < 70%."""
    _install(session)
    session.run("pytest", *session.posargs)


# ---------------------------------------------------------------------------
# docker — build the image (only runs if lint/typecheck/test passed)
# ---------------------------------------------------------------------------
@nox.session(python=False)  # no venv needed, just shell out to docker
def docker(session: nox.Session) -> None:
    """Build the Docker image."""
    tag = f"{IMAGE_NAME}:{IMAGE_TAG}"
    session.run(
        "docker",
        "build",
        "-t",
        tag,
        ".",
        external=True,
    )
    session.log(f"Image built: {tag}")
    session.log("To run:  docker compose up -d")
