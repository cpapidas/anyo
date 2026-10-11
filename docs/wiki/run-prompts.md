# Run an AI prompt

## For Humans

Give Anyo a Markdown prompt and read the result in Python. Choose OpenCode or native OmO as the tool that runs it.

## Try the Docker example

With Docker running, run these commands from the checkout. Replace the API key with your own MiniMax key; this calls the real model service.

```sh
export MINIMAX_API_KEY='your-minimax-api-key'
docker build -t anyo:local .
docker run --rm -e MINIMAX_API_KEY -e HARNESS=omo anyo:local
```

To use OpenCode instead:

```sh
docker run --rm -e MINIMAX_API_KEY -e HARNESS=opencode anyo:local
```

Both review the small function in [review.md](../../examples/harness-review/review.md) and print the result. Edit that file and rebuild to try a different prompt.

The [Dockerfile](../../Dockerfile) includes OmO 5.1.21, OpenCode 1.18.34, and their Python dependencies. No local harness installation or setup script is needed.

## Use it in Python

Outside Docker, [install Anyo](../../README.md#try-it) and the selected executable. OpenCode also needs `python -m pip install '.[opencode]'` from the checkout.

Create `prompt.md`:

```markdown
# Greeting

## For Humans

Write a short greeting. Return a JSON object with a `message` field.
```

Save this as `ask.py` beside it:

```python
import os

import anyo

harness = anyo.harness(
    provider="omo",
    models=[{"model": "minimax/MiniMax-M2.7", "key": os.environ["MINIMAX_API_KEY"]}],
)

with anyo.PromptClient(harness=harness) as client:
    result = client.inject_prompt("prompt.md")

result.raise_for_failure()
print(result.success.data)
```

Set `MINIMAX_API_KEY`, then run `python ask.py` from that directory. Example output: `{'message': 'Hello!'}`.

For OpenCode, change both values:

| Setting | OmO | OpenCode |
| --- | --- | --- |
| `provider` | `"omo"` | `"opencode"` |
| `model` | `"minimax/MiniMax-M2.7"` | `"minimax-coding-plan/MiniMax-M2.7"` |

Your prompt chooses the JSON data fields. Anyo checks the success/failure wrapper; your code checks the data it needs.

## Call from a hook

Configure `anyo.harness(...)` before `anyo.start()`. Inside a registered pipeline:

```python
def pipeline(event):
    result = anyo.inject_prompt("prompt.md", workspace="/path/to/repository")
    result.raise_for_failure()
    print(result.success.data)
```

Replace the workspace path with an existing directory. For an async pipeline, use `await anyo.ainject_prompt(...)`.

`raise_for_failure()` makes a failed prompt fail the pipeline too. To inspect the failure yourself, read `result.failure.code` and `result.failure.message` when `result.failure` is present.

Each call gets a fresh session. Anyo manages the local tool process and cleans it up.

More detail: [OpenCode options and progress](../knowledge/opencode-prompts.md), [OmO options and limits](../knowledge/omo-prompts.md), and a [complete GitHub review pipeline](../../examples/opencode_review.py).

[All guides](README.md)
