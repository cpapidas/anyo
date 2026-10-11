# Simplify the harness example and bundle both executables

## For Humans

The first selectable harness example mixed application code with installation, fixtures, delegation, and test reporting. This task reduces the consumer example to one Python file and one Markdown prompt, and makes the Docker image ready to run either harness. Verification belongs in the tests and this task record.

Status: done
Last updated: 2026-10-08

## Objective and scope

Keep only `examples/harness-review/main.py` and `review.md`. The Python file selects OmO or OpenCode, submits the prompt, raises failures, and prints the result. The prompt contains the small function being reviewed. Remove the example's setup script, fixture, separate README, result audit, private-path discovery and explicit delegation.

Extend the root Dockerfile to install native OmO 5.1.21 alongside OpenCode 1.18.34 on Linux ARM64 and AMD64, using pinned release checksums. Include the two example files at `/app` and preserve the existing `python /app/main.py` command and unprivileged user. `.dockerignore` permits only those example files; credentials remain outside the build context. Consumers can still mount their own application over `/app`.

Related contracts: [native OmO](../../knowledge/omo-prompts.md), [OpenCode](../../knowledge/opencode-prompts.md), and the [earlier native adapter task](../native-omo-harness/plan.md). No library API or dependency changes are required.

## Completion criteria

- The example directory has exactly one Python file and one Markdown file, with no setup script.
- A Docker build provides both executable versions on `PATH` and the installed Anyo package.
- The default image command runs the example using only `MINIMAX_API_KEY` and optional `HARNESS=omo|opencode`.
- Both choices return the expected empty-list finding using MiniMax M2.7, on both supported container architectures.
- Tests, applicable style checks, package checks, and documentation links pass.

## Implementation and verification

The example is 21 lines of Python and 12 lines of Markdown. The shared [example guide](../../../examples/README.md#choose-opencode-or-native-omo) supplies the build/run commands. It performs a direct review with both harnesses; advanced native delegation remains available through the existing adapter.

Completed on 2026-10-08:

- `docker build --platform linux/arm64 -t anyo:local .` and `docker build --platform linux/amd64 -t anyo:local-amd64 .` passed. Both images report OmO 5.1.21 and OpenCode 1.18.34, run as UID 10001, and import Anyo from installed `site-packages`. AMD64 was exercised under emulation on the ARM64 host.
- The unmodified default image command passed paid MiniMax M2.7 reviews with `HARNESS=omo` and `HARNESS=opencode` on both architectures: **2 passed in 15.44 seconds** on ARM64 and **2 passed in 34.29 seconds** on AMD64. Each returned `ZeroDivisionError` and a nonempty fix. The first native probe returned null data; explicitly naming the result's `data` field in the prompt resolved that without adding Python machinery.
- Tests use the same Docker commands as consumers, with no mounted example files, special executable paths, or endpoint overrides. `ANYO_LIVE_HARNESSES=1` opts in; `ANYO_HARNESS_IMAGE` selects the tested image. Credentials are passed by environment-variable name, never as command-line values. Safe result evidence is under `.anyo/minimal-harness/verified-*.json`.
- Full default suite: **537 passed, 18 deselected**. Ruff check and format check passed for the changed example and OmO tests. No library code was changed.
- Built a source archive and a wheel from that archive in `dist/minimal-harness-20261008/`; `twine check` passed. A fresh environment outside the checkout imported the installed base package with no OpenCode dependency, loaded the native bridge resource, configured the inert OmO harness, and passed `pip check`. The Docker live runs separately exercised the installed package with its OpenCode extra.
- Both images contain exactly `main.py` and `review.md` at `/app`, byte-for-byte matching the two source files. No credentials are baked into image environment metadata; source/report scans and local documentation links passed. All test containers were removed.

The older adapter task retains the historical delegated-review and request-recorder results. This example intentionally demonstrates only the shared prompt call. Consumers supply their API key at runtime; no package or image was published.

## Sources

- User follow-up, 2026-10-08 (conversation without a share link): one Python file, one Markdown file, install both harnesses in Docker, and remove setup requirements.
- [OmO 5.1.21 release](https://github.com/code-yeongyu/oh-my-openagent/releases/tag/v5.1.21) and its [SHA256SUMS](https://github.com/code-yeongyu/oh-my-openagent/releases/download/v5.1.21/SHA256SUMS), checked 2026-10-08. ARM64 uses `omo-linux-arm64`; AMD64 uses `omo-linux-x64-baseline` for CPUs without AVX2.
- Existing OpenCode download pins and checksum verification in the root [Dockerfile](../../../Dockerfile).
