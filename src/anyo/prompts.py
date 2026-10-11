"""Prompt loading and the provider-independent JSON envelope boundary."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]

MAX_BYTES = 1_048_576
MAX_DEPTH = 64


def json_value(value: object, depth: int = 0) -> JSONValue:
    """Validate and copy a finite JSON tree without coercion."""
    if depth > MAX_DEPTH:
        raise ValueError("JSON exceeds the nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [json_value(item, depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: json_value(item, depth + 1) for key, item in value.items()}
    raise ValueError("Expected a finite JSON value")


def json_text(value: object) -> str:
    text = json.dumps(json_value(value), ensure_ascii=False, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("JSON exceeds the 1 MiB limit")
    return text


def decode_json(data: bytes | str) -> JSONValue:
    def pairs(items: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
        result: dict[str, JSONValue] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    if len(data if isinstance(data, bytes) else data.encode("utf-8")) > MAX_BYTES:
        raise ValueError("JSON exceeds the 1 MiB limit")
    try:
        return json_value(json.loads(data, object_pairs_hook=pairs))
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid JSON encoding or depth") from error


def envelope_schema() -> dict[str, JSONValue]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "data", "failure_code", "failure_message"],
        "properties": {
            "type": {"type": "string", "enum": ["success", "failure"]},
            "data": {},
            "failure_code": {"type": ["string", "null"]},
            "failure_message": {"type": ["string", "null"]},
        },
    }


def envelope_instruction() -> str:
    return files("anyo").joinpath("_prompt_envelope.txt").read_text(encoding="utf-8")


@dataclass(frozen=True)
class PromptRequest:
    run_id: str
    text: str
    workspace: Path
    model: str
    timeout: float
    include_reasoning: bool = False


def load_prompt(prompt_file: str | Path, context: object = None) -> str:
    with Path(prompt_file).expanduser().resolve().open("rb") as source:
        content = source.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Prompt exceeds the 1 MiB limit")
    text = content.decode("utf-8")
    if not text.strip():
        raise ValueError("Prompt must not be empty")
    if context is not None:
        text += "\n\nSupplied context (JSON data):\n" + json_text(context)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Prompt and context exceed the 1 MiB limit")
    return text
