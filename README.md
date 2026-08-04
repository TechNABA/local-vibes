# local-vibes

**Delegate coding grunt-work to a local model.** A generic OpenAI-compatible
**subagent MCP server** for Claude Code (and any MCP host).

It exposes one tool, `delegate`, that runs a real tool-calling **agent loop**
(read → edit → run → iterate) against **any OpenAI-compatible chat-completions
endpoint** — Ollama, LM Studio, vLLM, or a remote API. The host's own model and
auth are never touched: this is a plain MCP tool call, so a **Max-subscription
Claude Code primary can offload grunt work to a local model with no
`ANTHROPIC_BASE_URL` change.**

Backend, model, limits, and concurrency are all environment variables — nothing
is model-specific. Point it at a different endpoint and it just works.

## How it works

```
Claude Code (primary, on your subscription)
   └─ tool call: delegate(task, working_dir, read_only)  ── may fan out many in parallel
        └─ bounded-concurrency gate (LOCAL_VIBES_MAX_CONCURRENCY)
             └─ agent loop on your local model:
                  read_file · list_dir · grep · write_file · edit_file · run_bash · finish
                  (file/shell ops sandboxed to working_dir; bounded by max iters)
        └─ returns: summary + git change report of what the subagent did
```

## Install

Requires **Node ≥ 18** and an OpenAI-compatible endpoint serving a
**tool-calling capable** model (e.g. Ollama: `ollama serve` + `ollama pull
qwen3-coder:30b`).

Once published to npm, add it to Claude Code with `npx` (no global install):

```bash
claude mcp add local-vibes \
  -e LOCAL_VIBES_BASE_URL=http://localhost:11434/v1 \
  -e LOCAL_VIBES_MODEL=qwen3-coder:30b \
  -e LOCAL_VIBES_MAX_CONCURRENCY=4 \
  -- npx -y local-vibes
```

Before publishing (local dev), build once and point at the entry directly:

```bash
npm install && npm run build
claude mcp add local-vibes \
  -e LOCAL_VIBES_MODEL=qwen3-coder:30b \
  -- node /ABSOLUTE/PATH/TO/local-vibes/dist/index.js
```

To publish: `npm publish` (the `prepublishOnly` script builds `dist/` first).

### Make the primary use it

Add to your project's `CLAUDE.md`:

> For codebase searches, boilerplate, mechanical multi-file edits, and test
> scaffolding, use the `delegate` tool (local subagent) with a complete
> self-contained task and the `working_dir`, instead of doing it inline.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LOCAL_VIBES_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `LOCAL_VIBES_API_KEY` | `not-needed` | Sent as the key; local servers ignore it |
| `LOCAL_VIBES_MODEL` | `qwen3-coder:30b` | Model name on that endpoint |
| `LOCAL_VIBES_MAX_ITERS` | `25` | Max model↔tool round-trips per task |
| `LOCAL_VIBES_TEMPERATURE` | `0.1` | Sampling temperature |
| `LOCAL_VIBES_MAX_CONCURRENCY` | `4` | Max delegate loops in flight at once (see below) |
| `LOCAL_VIBES_ALLOW_BASH` | `1` | `0` disables the `run_bash` tool entirely |
| `LOCAL_VIBES_BASH_TIMEOUT` | `120` | Per-command timeout (seconds) |
| `LOCAL_VIBES_TOOL_RESULT_CAP` | `12000` | Max chars of any tool result fed back to the model |

## Concurrency & scaling

The host can fan out many `delegate` calls at once. `local-vibes` runs them
through a **bounded-concurrency gate** so they don't overwhelm a single backend:
up to `LOCAL_VIBES_MAX_CONCURRENCY` loops run simultaneously and the rest queue.

**Tune it to your backend's parallel width.** Local inference servers process
requests in batched / lockstep decode — throughput scales as you fill the batch,
then collapses once you oversubscribe:

- **Ollama** — set concurrency to `OLLAMA_NUM_PARALLEL` (the number of parallel
  slots Ollama serves). Going higher just makes requests queue inside Ollama.
- **vLLM / TGI** — these batch aggressively; set concurrency near the server's
  max batch size to saturate the GPU without over-queuing.
- Concurrency of `1` serializes everything (safe but leaves the batch idle);
  the default `4` is a reasonable middle ground for a single Ollama host.

**File safety under concurrency:** the inner tools are synchronous, so Node's
single thread makes each file operation atomic — concurrent subagents can never
interleave a read-modify-write on the same file. There is no filesystem data
race to guard against. What the gate does *not* prevent is two subagents making
*logically* conflicting edits to the same files; for independent parallel tasks,
give each its own `working_dir` (or a separate `git worktree`) and merge the
results.

## The `delegate` tool

```
delegate(task: string, working_dir?: string = ".", read_only?: boolean = false)
```

- **task** — one complete, self-contained instruction, ideally with acceptance
  criteria ("…and run the test to confirm it exits 0").
- **working_dir** — sandbox root; every file/shell operation is confined here.
- **read_only** — `true` disables all writes/edits/bash (safe for search and
  analysis delegation).

## Safety model

- **Path confinement.** Every path is resolved and rejected if it escapes
  `working_dir` (lexically — `../`, absolute, and mixed escapes are blocked). The
  subagent cannot read or write outside the sandbox.
- **Bash is gated, not jailed.** `run_bash` runs with `cwd=working_dir` and a
  timeout, and can be turned off with `LOCAL_VIBES_ALLOW_BASH=0`. It is not
  otherwise sandboxed — a determined command can still reach the network or
  files the process can access. Point `delegate` at a repo you'd let a CI job
  touch, or run with `read_only=true` / bash disabled for untrusted tasks.
- **The change report is read-only** (`git diff --stat` + untracked list); it
  never mutates your git index.

## Development

```bash
npm install
npm run build        # tsc → dist/
npm test             # builds, then runs the invariant + concurrency tests
# run the loop directly against your backend, no MCP client needed:
LOCAL_VIBES_MODEL=qwen3-coder:30b \
  npm run selftest -- "your task here" /path/to/working_dir   # add --read-only to forbid edits
```

## Caveats

- **Output quality depends on the local model.** The loop is correct, but small
  local models vary a lot at sustained multi-tool-call orchestration. Verify the
  subagent's diffs — treat it as a fast junior, not a trusted senior. A
  coder-tuned model tool-calls far more reliably than a general chat model.
- **Requires tool-calling support.** The endpoint/model must support OpenAI
  function calling via `/v1/chat/completions`. Pure text-completion models won't
  work.
