"""Explicit paid-provider check; excluded by the default pytest selection."""

import os

import pytest

from anyo import PromptClient
from anyo.integrations.opencode import opencode_harness


@pytest.mark.live
def test_provider_obeys_json_and_no_json_contract(tmp_path):
    model = os.environ.get("ANYO_LIVE_MODEL")
    key = os.environ.get("ANYO_LIVE_API_KEY")
    if not model or not key:
        pytest.skip("Set ANYO_LIVE_MODEL and ANYO_LIVE_API_KEY for two bounded model calls")
    handle = opencode_harness(models=[{"model": model, "key": key}])
    prompt = tmp_path / "prompt.md"
    with PromptClient(harness=handle) as client:
        prompt.write_text('Return the JSON value {"comments": []}. No tools are needed.')
        result = client.inject_prompt(prompt, workspace=tmp_path, timeout=90)
        assert result.success is not None, result.failure
        assert result.success.data == {"comments": []}
        prompt.write_text("Confirm that two plus two equals four. No tools are needed.")
        result = client.inject_prompt(prompt, workspace=tmp_path, timeout=90)
        assert result.success is not None, result.failure
        assert result.success.data is None
