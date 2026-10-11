import os
from pathlib import Path

import anyo

provider = os.environ.get("HARNESS", "omo")  # "omo" or "opencode"
model = {
    "omo": "minimax/MiniMax-M2.7",
    "opencode": "minimax-coding-plan/MiniMax-M2.7",
}[provider]

harness = anyo.harness(
    provider=provider,
    models=[{"model": model, "key": os.environ["MINIMAX_API_KEY"]}],
)

with anyo.PromptClient(harness=harness) as client:
    result = client.inject_prompt(Path(__file__).with_name("review.md"))

result.raise_for_failure()
print(result.success.data)
