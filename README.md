# local-subagent-mcp

A generic **"delegate to a local model" subagent** exposed as an MCP server.

It gives a host like Claude Code a single tool, `delegate`, that runs a real
tool-calling **agent loop** (read → edit → run → iterate) against **any
OpenAI-compatible chat-completions endpoint** — Ollama, LM Studio, vLLM, or a
remote OpenAI-compatible API. The host's own model and auth are never touched:
this is a plain MCP tool call, so a **Max-subscription Claude Code primary can
offload grunt work to a local model with no `ANTHROPIC_BASE_URL` change.**

Backend, model, and limits are all environment variables — nothing is
model-specific. Point it at a different endpoint and it just works.

## How it works

```
Claude Code (primary, on your subscription)
   └─ tool call: delegate(task, working_dir, read_only)
        └─ this MCP server runs an agent loop on your local model:
             read_file · list_dir · grep · write_file · edit_file · run_bash · finish
             (all file/shell ops sandboxed to working_dir; bounded by max iters)
        └─ returns: summary + git change report of what the subagent did
```

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (handles the Python deps from the inline
  script metadata — no venv or `pip install` needed).
- An OpenAI-compatible endpoint serving a **tool-calling capable** model.
  For Ollama: `ollama serve` and e.g. `ollama pull qwen3-coder:30b`.

## Install into Claude Code

Use an **absolute path** to `server.py`:

```bash
claude mcp add local-subagent \
  -e SUBAGENT_BASE_URL=http://localhost:11434/v1 \
  -e SUBAGENT_MODEL=qwen3.6:35b-a3b-coding-mxfp8 \
  -- uv run --quiet /ABSOLUTE/PATH/TO/local-subagent-mcp/server.py
```

Then, from a normal (subscription) Claude Code session, the primary can call the
`delegate` tool. Encourage it with a line in `CLAUDE.md`, e.g.:

> For codebase searches, boilerplate, mechanical multi-file edits, and test
> scaffolding, use the `delegate` tool (local subagent) instead of doing it
> inline. Pass a complete, self-contained task and the `working_dir`.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SUBAGENT_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `SUBAGENT_API_KEY` | `not-needed` | Sent as the key; local servers ignore it |
| `SUBAGENT_MODEL` | `qwen3-coder:30b` | Model name on that endpoint |
| `SUBAGENT_MAX_ITERS` | `25` | Max model↔tool round-trips before giving up |
| `SUBAGENT_TEMPERATURE` | `0.1` | Sampling temperature |
| `SUBAGENT_ALLOW_BASH` | `1` | `0` disables the `run_bash` tool entirely |
| `SUBAGENT_BASH_TIMEOUT` | `120` | Per-command timeout (seconds) |
| `SUBAGENT_TOOL_RESULT_CAP` | `12000` | Max chars of any tool result fed back to the model |

## The `delegate` tool

```
delegate(task: str, working_dir: str = ".", read_only: bool = False) -> str
```

- **task** — one complete, self-contained instruction, ideally with acceptance
  criteria ("…and run the test to confirm it exits 0").
- **working_dir** — sandbox root; every file/shell operation is confined here.
- **read_only** — `true` disables all writes/edits/bash (safe for search and
  analysis delegation).

## Safety model

- **Path confinement.** Every file path is resolved and rejected if it escapes
  `working_dir`. The subagent cannot read or write outside the sandbox.
- **Bash is gated.** `run_bash` runs with `cwd=working_dir` and a timeout, and
  can be turned off with `SUBAGENT_ALLOW_BASH=0`. It is *not* otherwise
  sandboxed — a determined command can still reach the network or files the
  process can access, so point `delegate` at a repo you'd let a CI job touch,
  or run with `read_only=true` / bash disabled for untrusted tasks.
- **The change report is read-only** (`git diff --stat` + untracked list); it
  never mutates your git index.

## Local test (no MCP client needed)

`selftest` runs the loop directly against your configured backend:

```bash
SUBAGENT_MODEL=qwen3.6:35b-a3b-coding-mxfp8 \
uv run --quiet server.py selftest "your task here" /path/to/working_dir
# add --read-only to forbid edits
```

## Notes and caveats

- **Pinned to `mcp>=1.2.0,<2`.** The MCP SDK's 2.0 major reorganized the server
  API (`FastMCP` → `MCPServer`); the pin keeps this server reproducible.
- **Output quality depends on the local model.** The loop is correct, but small
  local models vary a lot at sustained multi-tool-call orchestration. Verify the
  subagent's diffs — treat it as a fast junior, not a trusted senior. A coder-
  tuned model (e.g. `qwen3-coder` / `qwen3.6-*-coding`) does noticeably better
  at tool calling than a general chat model.
- **Requires tool-calling support.** The endpoint/model must support OpenAI
  function calling via `/v1/chat/completions`. Pure text-completion models won't
  work.
