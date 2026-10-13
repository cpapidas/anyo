"""Prepare missing PyPI files or verify an Anyo alpha's published file hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tarfile
import time
import zipfile
from email.parser import BytesParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def distribution_files(version: str, directory: Path) -> dict[str, Path]:
    """Require exactly the two alpha artifacts and matching MIT package metadata."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+a[1-9][0-9]*", version):
        raise ValueError("Expected an alpha version such as 0.1.0a1")
    names = {f"anyo-{version}-py3-none-any.whl", f"anyo-{version}.tar.gz"}
    entries = {path.name: path for path in directory.iterdir()}
    if set(entries) != names or any(
        not path.is_file() or path.is_symlink() for path in entries.values()
    ):
        raise ValueError("Distribution directory must contain exactly the expected wheel and sdist")
    for name, path in entries.items():
        try:
            if name.endswith(".whl"):
                with zipfile.ZipFile(path) as wheel:
                    member = f"anyo-{version}.dist-info/METADATA"
                    if wheel.namelist().count(member) != 1:
                        raise ValueError(f"Expected one METADATA record in {name}")
                    data = wheel.read(member)
            else:
                with tarfile.open(path, "r:gz") as sdist:
                    member = f"anyo-{version}/PKG-INFO"
                    matches = [item for item in sdist.getmembers() if item.name == member]
                    if len(matches) != 1 or not matches[0].isfile():
                        raise ValueError(f"Expected one regular PKG-INFO record in {name}")
                    stream = sdist.extractfile(matches[0])
                    if stream is None:
                        raise ValueError(f"Cannot read PKG-INFO in {name}")
                    with stream:
                        data = stream.read()
        except (tarfile.TarError, zipfile.BadZipFile, KeyError, EOFError) as error:
            raise ValueError(f"Cannot read distribution metadata in {name}") from error
        metadata = BytesParser().parsebytes(data)
        for field, expected in (
            ("Name", "anyo"),
            ("Version", version),
            ("License-Expression", "MIT"),
        ):
            if metadata.get_all(field) != [expected]:
                raise ValueError(f"Expected {field}: {expected} in {name}")
    return entries


def published_hashes(version: str) -> dict[str, str]:
    """Read PyPI's release hashes; only HTTP 404 means the release is absent."""
    request = Request(
        f"https://pypi.org/pypi/anyo/{version}/json", headers={"Accept": "application/json"}
    )
    try:
        with urlopen(request, timeout=30) as response:
            document = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return {}
        raise RuntimeError(f"PyPI lookup failed with HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError("PyPI lookup failed; publication state is unknown") from error
    except (ValueError, UnicodeError) as error:
        raise ValueError("PyPI returned invalid JSON") from error
    if not isinstance(document, dict):
        raise ValueError("PyPI returned an invalid release record")
    info = document.get("info")
    rows = document.get("urls")
    if (
        not isinstance(info, dict)
        or info.get("name") != "anyo"
        or info.get("version") != version
        or not isinstance(rows, list)
    ):
        raise ValueError("PyPI returned an invalid release record")
    hashes: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("PyPI returned an invalid file record")
        name = row.get("filename")
        digests = row.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if (
            not isinstance(name, str)
            or name in hashes
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("PyPI returned an invalid or duplicate file record")
        hashes[name] = digest
    return hashes


def missing_files(local: dict[str, Path], remote: dict[str, str]) -> list[Path]:
    """Reject unexpected files or conflicting hashes before returning missing files."""
    if unexpected := remote.keys() - local.keys():
        raise ValueError(f"Unexpected files on PyPI: {', '.join(sorted(unexpected))}")
    missing = []
    for name, path in sorted(local.items()):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if name not in remote:
            missing.append(path)
        elif remote[name] != digest:
            raise ValueError(f"SHA256 mismatch for published file {name}")
    return missing


def stage_upload(local: dict[str, Path], missing: list[Path], directory: Path) -> None:
    """Replace previously staged expected files, preserving the source artifacts."""
    if directory.is_symlink() or any(
        path.resolve().is_relative_to(directory.resolve())
        or directory.resolve().is_relative_to(path.parent.resolve())
        for path in local.values()
    ):
        raise ValueError("Upload directory must be separate from the distribution directory")
    directory.mkdir(parents=True, exist_ok=True)
    existing = list(directory.iterdir())
    if any(path.name not in local or not path.is_file() or path.is_symlink() for path in existing):
        raise ValueError("Upload directory contains unexpected files; use an empty directory")
    for path in existing:
        path.unlink()
    for path in missing:
        shutil.copyfile(path, directory / path.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("distdir", type=Path)
    parser.add_argument("--upload-dir", type=Path, default=Path("upload"))
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        help="Wait for missing PyPI files when verifying (default: 0; requires --verify)",
    )
    args = parser.parse_args(argv)
    if args.wait_seconds is not None:
        if not args.verify:
            parser.error("--wait-seconds requires --verify")
        if not math.isfinite(args.wait_seconds) or args.wait_seconds < 0:
            parser.error("--wait-seconds must be a finite nonnegative number")
    try:
        local = distribution_files(args.version, args.distdir)
        deadline = time.monotonic() + (args.wait_seconds or 0.0)
        while True:
            missing = missing_files(local, published_hashes(args.version))
            if not args.verify or not missing:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(
                    f"Missing files on PyPI: {', '.join(path.name for path in missing)}"
                )
            time.sleep(min(2.0, remaining))
        if args.verify:
            print(f"Verified both PyPI artifacts for anyo {args.version}")
        else:
            stage_upload(local, missing, args.upload_dir)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with Path(output).open("a", encoding="utf-8") as stream:
                    stream.write(f"needs_upload={str(bool(missing)).lower()}\n")
            print(f"Prepared {len(missing)} missing artifact(s) for anyo {args.version}")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Publication check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
