"""Publication retries preserve exact distributions and reject ambiguous state."""

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_published.py"
SPEC = importlib.util.spec_from_file_location("check_published", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)
VERSION = "0.1.0a7"


@pytest.fixture(autouse=True)
def isolate_action_output(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


def write_distribution(path, *, name="anyo", version=VERSION, license="MIT"):
    metadata = (
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nLicense-Expression: {license}\n"
    ).encode()
    if path.name.endswith(".whl"):
        with zipfile.ZipFile(path, "w") as wheel:
            wheel.writestr(f"anyo-{VERSION}.dist-info/METADATA", metadata)
    else:
        with tarfile.open(path, "w:gz") as sdist:
            member = tarfile.TarInfo(f"anyo-{VERSION}/PKG-INFO")
            member.size = len(metadata)
            sdist.addfile(member, io.BytesIO(metadata))


@pytest.fixture
def distributions(tmp_path):
    directory = tmp_path / "dist"
    directory.mkdir()
    for filename in (f"anyo-{VERSION}-py3-none-any.whl", f"anyo-{VERSION}.tar.gz"):
        write_distribution(directory / filename)
    return directory


def hashes(directory):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()
    }


def remote_document(remote):
    return {
        "info": {"name": "anyo", "version": VERSION},
        "urls": [
            {"filename": name, "digests": {"sha256": digest}} for name, digest in remote.items()
        ],
    }


def set_remote(monkeypatch, remote, *, document=None):
    if document is None:
        document = remote_document(remote)

    def request(request, *, timeout):
        assert request.full_url == f"https://pypi.org/pypi/anyo/{VERSION}/json"
        assert timeout == 30
        return io.BytesIO(json.dumps(document).encode())

    monkeypatch.setattr(publication, "urlopen", request)


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0.0, delays=[])

    def sleep(delay):
        assert delay > 0
        state.delays.append(delay)
        state.now += delay

    monkeypatch.setattr(
        publication,
        "time",
        SimpleNamespace(monotonic=lambda: state.now, sleep=sleep),
        raising=False,
    )
    return state


def set_responses(monkeypatch, responses):
    calls = []

    def request(request, *, timeout):
        assert request.full_url == f"https://pypi.org/pypi/anyo/{VERSION}/json"
        assert timeout == 30
        calls.append(request)
        assert responses, "Unexpected extra PyPI request"
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        data = response if isinstance(response, bytes) else json.dumps(response).encode()
        return io.BytesIO(data)

    monkeypatch.setattr(publication, "urlopen", request)
    return calls


def run(distributions, tmp_path, *arguments):
    return publication.main(
        [VERSION, str(distributions), "--upload-dir", str(tmp_path / "upload"), *arguments]
    )


def test_absent_release_stages_both_files_and_emits_output(distributions, tmp_path, monkeypatch):
    def absent(*args, **kwargs):
        raise HTTPError("https://pypi.org", 404, "Not Found", {}, None)

    monkeypatch.setattr(publication, "urlopen", absent)
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert run(distributions, tmp_path) == 0
    assert hashes(tmp_path / "upload") == hashes(distributions)
    assert output.read_text() == "needs_upload=true\n"


@pytest.mark.parametrize("published_suffix", [".whl", ".tar.gz"])
def test_partial_upload_stages_only_missing_file(
    distributions, tmp_path, monkeypatch, published_suffix
):
    remote = {
        name: digest
        for name, digest in hashes(distributions).items()
        if name.endswith(published_suffix)
    }
    set_remote(monkeypatch, remote)
    assert run(distributions, tmp_path) == 0
    staged = hashes(tmp_path / "upload")
    assert set(staged) == hashes(distributions).keys() - remote.keys()
    assert all(staged[name] == hashes(distributions)[name] for name in staged)


def test_complete_retry_clears_previous_staging_and_needs_no_upload(
    distributions, tmp_path, monkeypatch
):
    set_remote(monkeypatch, {})
    assert run(distributions, tmp_path) == 0
    set_remote(monkeypatch, hashes(distributions))
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert run(distributions, tmp_path) == 0
    assert list((tmp_path / "upload").iterdir()) == []
    assert output.read_text() == "needs_upload=false\n"
    assert len(hashes(distributions)) == 2


def test_verify_requires_both_matching_files_without_creating_upload_directory(
    distributions, tmp_path, monkeypatch
):
    set_remote(monkeypatch, hashes(distributions))
    assert run(distributions, tmp_path, "--verify") == 0
    assert not (tmp_path / "upload").exists()
    set_remote(monkeypatch, dict(list(hashes(distributions).items())[:1]))
    assert run(distributions, tmp_path, "--verify") == 1


def test_verify_waits_for_absent_then_partial_publication(
    distributions, tmp_path, monkeypatch, clock, capsys
):
    remote = hashes(distributions)
    calls = set_responses(
        monkeypatch,
        [
            HTTPError("https://pypi.org", 404, "Not Found", {}, None),
            remote_document(dict(list(remote.items())[:1])),
            remote_document(remote),
        ],
    )
    assert run(distributions, tmp_path, "--verify", "--wait-seconds", "5") == 0
    assert "Verified both PyPI artifacts" in capsys.readouterr().out
    assert len(calls) == 3
    assert clock.now == 4
    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("wait_seconds", ["0", "2.5"])
def test_verify_missing_files_fails_at_deadline(
    distributions, tmp_path, monkeypatch, clock, capsys, partial, wait_seconds
):
    remote = dict(list(hashes(distributions).items())[:1]) if partial else {}
    calls = set_responses(monkeypatch, [remote_document(remote)] * 3)
    assert run(distributions, tmp_path, "--verify", "--wait-seconds", wait_seconds) == 1
    assert "Missing files on PyPI" in capsys.readouterr().err
    assert clock.now == float(wait_seconds)
    assert len(calls) == (1 if wait_seconds == "0" else 3)
    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize(
    "problem", ["hash", "unexpected", "http", "network", "timeout", "json", "record"]
)
def test_verify_polling_stops_immediately_on_uncertain_or_conflicting_state(
    distributions, tmp_path, monkeypatch, clock, capsys, problem
):
    remote = hashes(distributions)
    if problem == "hash":
        remote[next(iter(remote))] = "0" * 64
        failure = remote_document(remote)
    elif problem == "unexpected":
        failure = remote_document({**remote, "unexpected.whl": "0" * 64})
    else:
        failure = {
            "http": HTTPError("https://pypi.org", 503, "Unavailable", {}, None),
            "network": URLError("unavailable"),
            "timeout": TimeoutError(),
            "json": b"not JSON",
            "record": {},
        }[problem]
    calls = set_responses(monkeypatch, [remote_document({}), failure])
    assert run(distributions, tmp_path, "--verify", "--wait-seconds", "60") == 1
    assert "Publication check failed" in capsys.readouterr().err
    assert len(calls) == 2
    assert clock.now == 2
    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize("verify", [False, True])
def test_default_checks_make_one_request_without_waiting(
    distributions, tmp_path, monkeypatch, clock, verify
):
    calls = set_responses(monkeypatch, [remote_document({})])
    assert run(distributions, tmp_path, *(["--verify"] if verify else [])) == int(verify)
    assert len(calls) == 1
    assert clock.delays == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--wait-seconds", "0"],
        ["--wait-seconds", "60"],
        ["--verify", "--wait-seconds", "-1"],
        ["--verify", "--wait-seconds", "nan"],
        ["--verify", "--wait-seconds", "inf"],
        ["--verify", "--wait-seconds", "invalid"],
    ],
)
def test_invalid_wait_options_fail_before_network(distributions, tmp_path, monkeypatch, arguments):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Invalid wait options must not query PyPI")

    monkeypatch.setattr(publication, "urlopen", unexpected_request)
    with pytest.raises(SystemExit) as caught:
        run(distributions, tmp_path, *arguments)
    assert caught.value.code == 2


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
def test_conflicting_published_hash_fails_before_staging(
    distributions, tmp_path, monkeypatch, capsys, suffix
):
    remote = hashes(distributions)
    name = next(name for name in remote if name.endswith(suffix))
    remote[name] = "0" * 64
    set_remote(monkeypatch, remote)
    assert run(distributions, tmp_path) == 1
    assert "SHA256 mismatch" in capsys.readouterr().err
    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize("verify", [False, True])
def test_unexpected_remote_file_fails(distributions, tmp_path, monkeypatch, verify):
    set_remote(monkeypatch, {**hashes(distributions), "anyo-other.whl": "0" * 64})
    assert run(distributions, tmp_path, *(["--verify"] if verify else [])) == 1
    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize("problem", ["missing", "extra", "directory", "symlink", "wrong-version"])
def test_invalid_distribution_set_fails_before_network(
    distributions, tmp_path, monkeypatch, problem
):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Invalid artifacts must not query PyPI")

    monkeypatch.setattr(publication, "urlopen", unexpected_request)
    wheel = next(distributions.glob("*.whl"))
    if problem == "missing":
        wheel.unlink()
    elif problem == "extra":
        (distributions / "old.whl").write_text("stale build")
    elif problem == "directory":
        wheel.unlink()
        wheel.mkdir()
    elif problem == "symlink":
        target = tmp_path / "wheel"
        wheel.rename(target)
        wheel.symlink_to(target)
    else:
        wheel.rename(wheel.with_name(wheel.name.replace(VERSION, "0.1.0a6")))
    assert run(distributions, tmp_path) == 1


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
@pytest.mark.parametrize(
    "change", [{"name": "other"}, {"version": "0.1.0a6"}, {"license": "Apache-2.0"}]
)
def test_wrong_package_metadata_is_rejected(distributions, suffix, change):
    path = next(path for path in distributions.iterdir() if path.name.endswith(suffix))
    write_distribution(path, **change)
    with pytest.raises(ValueError, match="Expected"):
        publication.distribution_files(VERSION, distributions)


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://pypi.org", 503, "Unavailable", {}, None),
        URLError("unavailable"),
        TimeoutError(),
    ],
)
def test_network_failure_never_means_missing_release(
    distributions, tmp_path, monkeypatch, capsys, error
):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(publication, "urlopen", fail)
    assert run(distributions, tmp_path) == 1
    assert "PyPI lookup failed" in capsys.readouterr().err
    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"info": {"name": "other", "version": VERSION}, "urls": []},
        {
            "info": {"name": "anyo", "version": VERSION},
            "urls": [{"filename": "file", "digests": {"sha256": "invalid"}}],
        },
    ],
)
def test_invalid_remote_response_is_rejected(monkeypatch, document):
    set_remote(monkeypatch, {}, document=document)
    with pytest.raises(ValueError, match="PyPI returned"):
        publication.published_hashes(VERSION)


def test_duplicate_remote_filename_is_rejected(monkeypatch):
    row = {"filename": "file", "digests": {"sha256": "0" * 64}}
    set_remote(
        monkeypatch, {}, document={"info": {"name": "anyo", "version": VERSION}, "urls": [row, row]}
    )
    with pytest.raises(ValueError, match="duplicate"):
        publication.published_hashes(VERSION)


def test_invalid_json_is_rejected(monkeypatch):
    monkeypatch.setattr(publication, "urlopen", lambda *args, **kwargs: io.BytesIO(b"not JSON"))
    with pytest.raises(ValueError, match="invalid JSON"):
        publication.published_hashes(VERSION)


def test_staging_never_deletes_unexpected_files(distributions, tmp_path, monkeypatch):
    set_remote(monkeypatch, {})
    upload = tmp_path / "upload"
    upload.mkdir()
    existing = upload / "important.txt"
    existing.write_text("keep")
    assert run(distributions, tmp_path) == 1
    assert existing.read_text() == "keep"


def test_staging_cannot_replace_source_distributions(distributions, monkeypatch):
    original = hashes(distributions)
    set_remote(monkeypatch, original)
    assert publication.main([VERSION, str(distributions), "--upload-dir", str(distributions)]) == 1
    assert hashes(distributions) == original


def test_staging_cannot_add_files_inside_distribution_directory(distributions, monkeypatch):
    original = hashes(distributions)
    set_remote(monkeypatch, {})
    assert (
        publication.main(
            [VERSION, str(distributions), "--upload-dir", str(distributions / "upload")]
        )
        == 1
    )
    assert hashes(distributions) == original
