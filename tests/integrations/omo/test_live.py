"""Opt-in paid MiniMax checks of the image's minimal two-file example."""

import ast
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest


@pytest.mark.live
@pytest.mark.parametrize("provider", ["omo", "opencode"])
def test_minimal_review_in_docker(tmp_path, provider):
    if os.environ.get("ANYO_LIVE_HARNESSES") != "1":
        pytest.skip("Set ANYO_LIVE_HARNESSES=1 to run paid MiniMax container reviews")
    root = Path(__file__).resolve().parents[3]
    key = os.environ.get("MINIMAX_API_KEY", "")
    credential = root / ".anyo/credentials/minimax.key"
    if not key and credential.is_file():
        key = credential.read_text().strip()
    if not key:
        pytest.skip("Configure MINIMAX_API_KEY")
    image = os.environ.get("ANYO_HARNESS_IMAGE", "anyo:local")
    name = "anyo-review-test-" + uuid.uuid4().hex
    env = {**os.environ, "MINIMAX_API_KEY": key}
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--env",
                "MINIMAX_API_KEY",
                "--env",
                f"HARNESS={provider}",
                image,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=360,
        )
    finally:
        # Also remove the container if the host test times out or is interrupted.
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.replace(key, "[redacted]")
    # Prompt data can be a dict or JSON text; main.py simply prints that data.
    try:
        finding = json.loads(result.stdout)
    except json.JSONDecodeError:
        finding = ast.literal_eval(result.stdout)
    assert isinstance(finding, dict), "The example returned no review object"
    assert finding["error"] == "ZeroDivisionError"
    assert isinstance(finding["fix"], str) and finding["fix"].strip()
    assert key not in result.stdout
    evidence = root / ".anyo/minimal-harness"
    evidence.mkdir(parents=True, exist_ok=True)
    suffix = image.rsplit(":", 1)[-1]
    (evidence / f"verified-{suffix}-{provider}.json").write_text(
        json.dumps(
            {"image": image, "harness": provider, "model": "MiniMax-M2.7", "finding": finding},
            indent=2,
        )
        + "\n"
    )
