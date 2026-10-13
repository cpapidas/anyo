"""Release retries must keep a version attached to exactly one source commit."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "prepare_release", Path(__file__).resolve().parents[1] / "scripts/prepare_release.py"
)
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.name", "Release test")
    git(tmp_path, "config", "user.email", "release@example.invalid")
    (tmp_path / "pyproject.toml").write_text('[tool.anyo.release]\nseries = "0.1.0"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "Initial")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def test_retry_keeps_same_tag_and_commit(repository: tuple[Path, str]) -> None:
    root, commit = repository
    tag = release.reserve_tag(root, 17, commit)
    first_tag_object = git(root, "rev-parse", tag)
    assert tag == "v0.1.0a17"
    assert release.reserve_tag(root, 17, commit) == tag
    assert git(root, "rev-parse", tag) == first_tag_object
    assert git(root, "rev-parse", f"{tag}^{{commit}}") == commit


def test_next_run_gets_new_version(repository: tuple[Path, str]) -> None:
    root, commit = repository
    first = release.reserve_tag(root, 1, commit)
    git(root, "commit", "--allow-empty", "--quiet", "-m", "Next merge")
    second_commit = git(root, "rev-parse", "HEAD")
    second = release.reserve_tag(root, 2, second_commit)
    assert first == "v0.1.0a1"
    assert second == "v0.1.0a2"
    assert git(root, "rev-parse", f"{first}^{{commit}}") == commit
    assert git(root, "rev-parse", f"{second}^{{commit}}") == second_commit


def test_cannot_reassign_existing_version(repository: tuple[Path, str]) -> None:
    root, commit = repository
    tag = release.reserve_tag(root, 1, commit)
    git(root, "commit", "--allow-empty", "--quiet", "-m", "Next merge")
    with pytest.raises(ValueError, match="another commit"):
        release.reserve_tag(root, 1, git(root, "rev-parse", "HEAD"))
    assert git(root, "rev-parse", f"{tag}^{{commit}}") == commit


def test_rejects_wrong_checkout(repository: tuple[Path, str]) -> None:
    root, _ = repository
    with pytest.raises(ValueError, match="checkout does not match"):
        release.reserve_tag(root, 1, "0" * 40)
    assert not git(root, "tag", "--list")


def test_rejects_modified_source(repository: tuple[Path, str]) -> None:
    root, commit = repository
    with (root / "pyproject.toml").open("a") as stream:
        stream.write("# changed\n")
    with pytest.raises(ValueError, match="modified tracked"):
        release.reserve_tag(root, 1, commit)
    assert not git(root, "tag", "--list")


@pytest.mark.parametrize("number", [0, -1])
def test_rejects_invalid_run_number(repository: tuple[Path, str], number: int) -> None:
    root, commit = repository
    with pytest.raises(ValueError, match="positive"):
        release.reserve_tag(root, number, commit)
    assert not git(root, "tag", "--list")
