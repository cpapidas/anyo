"""Check the exact release artifacts, including version, license, and resources."""

import argparse
import email
import re
import tarfile
import zipfile
from pathlib import Path

RESOURCES = {
    "anyo/py.typed",
    "anyo/_prompt_envelope.txt",
    "anyo/integrations/omo/_bridge.mjs",
    "anyo/ui/assets/index.html",
    "anyo/ui/assets/login.html",
    "anyo/ui/assets/ui.css",
    "anyo/ui/assets/ui.js",
}


def check_release(tag: str, directory: Path) -> None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+a[1-9]\d*", tag):
        raise ValueError("release tag must identify an alpha, for example v0.1.0a1")
    version = tag[1:]
    stem = f"anyo-{version}"
    expected = {f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"}
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("release directory must contain exactly the matching wheel and sdist")

    def metadata(data: bytes) -> None:
        values = email.message_from_bytes(data)
        if (values["Name"], values["Version"], values["License-Expression"]) != (
            "anyo",
            version,
            "MIT",
        ):
            raise ValueError("artifact name, version, or MIT license metadata does not match")
        if "LICENSE" not in values.get_all("License-File", []):
            raise ValueError("artifact does not declare LICENSE")

    with zipfile.ZipFile(directory / f"{stem}-py3-none-any.whl") as wheel:
        metadata(wheel.read(f"anyo-{version}.dist-info/METADATA"))
        if not RESOURCES <= set(wheel.namelist()):
            raise ValueError("wheel is missing required runtime resources")
        license_text = wheel.read(f"anyo-{version}.dist-info/licenses/LICENSE")
    with tarfile.open(directory / f"{stem}.tar.gz") as sdist:
        names = set(sdist.getnames())
        if not {f"{stem}/src/{name}" for name in RESOURCES} <= names:
            raise ValueError("sdist is missing required runtime resources")
        for filename in ("PKG-INFO", "LICENSE"):
            stream = sdist.extractfile(f"{stem}/{filename}")
            if stream is None:
                raise ValueError(f"sdist is missing {filename}")
            data = stream.read()
            if filename == "PKG-INFO":
                metadata(data)
            elif data != license_text:
                raise ValueError("wheel and sdist licenses differ")
    if b"MIT License" not in license_text:
        raise ValueError("artifact license text is not MIT")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    check_release(args.tag, args.directory)
    print(f"Verified release artifacts for {args.tag}")


if __name__ == "__main__":
    main()
