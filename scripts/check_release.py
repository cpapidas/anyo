"""Reject an accidental release with an unselected license or mismatched tag."""

import argparse
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    if args.tag != f"v{project['version']}":
        parser.error(f"release tag must be v{project['version']}")
    if not project.get("license") or not project.get("license-files"):
        parser.error("the project owner must select a license and declare its files before release")
    for pattern in project["license-files"]:
        if not any(path.is_file() for path in root.glob(pattern)):
            parser.error(f"no license file matches {pattern!r}")
    print(f"Release metadata ready for {args.tag}")


if __name__ == "__main__":
    main()
