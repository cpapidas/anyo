"""Reserve one immutable alpha tag for a GitHub Actions run and source commit."""

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path


def reserve_tag(root: Path, run_number: int, commit: str) -> str:
    if run_number < 1:
        raise ValueError("run number must be positive")
    config = tomllib.loads((root / "pyproject.toml").read_text())
    series = config["tool"]["anyo"]["release"]["series"]
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", series):
        raise ValueError("release series must be a three-part version")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("release commit must be a full Git SHA")

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    if git("rev-parse", "HEAD") != commit:
        raise ValueError("checkout does not match the release commit")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("release checkout has modified tracked files")
    tag = f"v{series}a{run_number}"
    if git("tag", "--list", tag):
        if git("rev-parse", f"{tag}^{{commit}}") != commit:
            raise ValueError(f"{tag} already belongs to another commit; never move release tags")
    else:
        git("tag", "-a", tag, commit, "-m", f"Anyo {tag[1:]}")
    return tag


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_number", type=int)
    parser.add_argument("commit")
    args = parser.parse_args()
    tag = reserve_tag(Path(__file__).resolve().parents[1], args.run_number, args.commit)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.write(f"tag={tag}\nversion={tag[1:]}\n")
    print(tag)


if __name__ == "__main__":
    main()
