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
| `LOCAL_VIBES_TEMPERATURE` | `0.1` | Default sampling temperature (per-call `temperature` overrides) |
| `LOCAL_VIBES_LANE` | _(unset)_ | Default sticky-session lane id (per-call `lane` overrides) |
| `LOCAL_VIBES_LANE_HEADER` | `X-MTPLX-Lane` | HTTP header name the lane id is sent under |
| `LOCAL_VIBES_MAX_CONCURRENCY` | `8` | Cohort width — concurrent delegate loops offered to the backend (see below) |
| `LOCAL_VIBES_EXTRA_BODY` | `{}` | JSON object merged into every request body — superset passthrough (see below) |
| `LOCAL_VIBES_ALLOW_BASH` | `1` | `0` disables the `run_bash` tool entirely |
| `LOCAL_VIBES_BASH_TIMEOUT` | `120` | Per-command timeout (seconds) |
| `LOCAL_VIBES_TOOL_RESULT_CAP` | `12000` | Max chars of any tool result fed back to the model |

## Model selection

The `model` field is **required by the OpenAI `/v1/chat/completions` protocol** —
every request must carry one, so `local-vibes` always sends `LOCAL_VIBES_MODEL`.
But the value is just a **routing key**, not a client-side choice of weights:

- On a **multi-model host** (Ollama with several tags, a router), it selects
  which model answers — e.g. `qwen3-coder:30b`.
- On a **pinned single-model server** (`mlx_lm.server`, llama.cpp server, a vLLM
  serving one model), set it to whatever stable name that server exposes and
  forget it. If the server publishes an alias like **`default_model`** and swaps
  the real weights behind it, point `LOCAL_VIBES_MODEL` at the alias once — the
  client never needs to change when you swap the underlying model.

### Example: a local MLX server with a stable alias + high concurrency

```bash
claude mcp add local-vibes \
  -e LOCAL_VIBES_BASE_URL=http://127.0.0.1:8080/v1 \
  -e LOCAL_VIBES_MODEL=default_model \
  -e LOCAL_VIBES_MAX_CONCURRENCY=8 \
  -- npx -y local-vibes
```

(Point at the OpenAI chat endpoint directly, not an Anthropic/Responses
translation gateway — `local-vibes` speaks `/v1/chat/completions`.)

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
- **Batching MLX servers (`mlx_lm.server` / MTPLX)** — aggregate throughput rises
  with streams up to a ceiling, then **collapses** once *distinct* prompts thrash
  the MoE expert-union. Measured on an M5 Max serving Qwen3.6-35B-A3B with
  **distinct prompts** (the realistic case for independent subagents): int4
  aggregate 103 → 210 → **274 tok/s at 8** → 150 at 12 → 190 at 16. Aggregate
  **peaks at 8** and N=12 is ~half of N=8, so **cap concurrency at 8** — higher
  actively hurts on this MoE. (Best-case *identical*-prompt runs scale further,
  to ~490 @16, because they share expert routing — real delegations don't, so
  don't tune to that number.) Crucially, the server's own `decode-concurrency` /
  `prompt-concurrency` flags are **inert** and offer no protection: nothing on the
  server side round-robins or admission-controls to keep you under the ceiling. So
  the cap must be enforced **client-side** — which is exactly what this gate is for.
- Concurrency of `1` serializes everything (safe but leaves the batch idle); the
  default `4` suits a single Ollama host — raise it toward a batching backend's
  ceiling (e.g. `8` for the MLX MoE above), but not past it.

To actually use N server-side streams you need N concurrent `delegate` calls in
flight: a single delegate loop is sequential (each step waits for the previous
tool result), so the *host* must fan out that many delegations **and**
`LOCAL_VIBES_MAX_CONCURRENCY` must be ≥ N.

### Named lanes (gateway routing)

If a router/gateway maps model names to different backends — e.g. a LiteLLM proxy
exposing `qwen` (a thinking model) and `qwen-instruct` (a fast non-thinking model
on a separate port) — point `LOCAL_VIBES_BASE_URL` at the gateway and set
`LOCAL_VIBES_MODEL` to the lane you want; **the model field is the lane selector.**
Route grunt delegations to the fast non-thinking lane. Note: lanes that share one
GPU also share that ~8-stream ceiling — naming a lane is for *routing* (task type,
thinking vs not), not for multiplying total concurrency.

**File safety under concurrency:** the inner tools are synchronous, so Node's
single thread makes each file operation atomic — concurrent subagents can never
interleave a read-modify-write on the same file. There is no filesystem data
race to guard against. What the gate does *not* prevent is two subagents making
*logically* conflicting edits to the same files; for independent parallel tasks,
give each its own `working_dir` (or a separate `git worktree`) and merge the
results.

## Strict superset & mtplx concurrency kernels

local-vibes is a **strict superset** OpenAI client: by default it sends only
standard `/v1/chat/completions` fields, so it works against any OpenAI-compatible
server (Ollama, LM Studio, vLLM). But an [mtplx](https://github.com/youssofal/mtplx)
server accepts arbitrary extra request fields (`extra="allow"`), so you can carry
mtplx-specific hints without breaking portability, via `LOCAL_VIBES_EXTRA_BODY`:

```bash
claude mcp add local-vibes \
  -e LOCAL_VIBES_BASE_URL=http://127.0.0.1:8080/v1 \
  -e LOCAL_VIBES_MODEL=mtplx-qwen36-27b-optimized-speed-v2 \
  -e LOCAL_VIBES_MAX_CONCURRENCY=8 \
  -e LOCAL_VIBES_EXTRA_BODY='{"top_k":20}' \
  -- npx -y local-vibes
```

The object is merged into every request body verbatim; a generic server that
doesn't understand a field never receives one (empty default) or ignores it.

**How the concurrency kernel engages.** mtplx's cross-request batched decode runs
`B` concurrent streams as one forward, amortizing a single dense-weight read across
the cohort (≈×2.2 net-ragged at B=8) and committing byte-identical greedy tokens
per stream. local-vibes feeds it the way it needs to be fed: it fires up to
`LOCAL_VIBES_MAX_CONCURRENCY` concurrent, independently-sequential delegate loops,
so a cohorting server can batch them. Set the cohort width to 8 (the mtplx
THROUGHPUT-preset width).

Two things gate whether the cohort actually forms, and both are **server-side**:
the server must run a batching preset (`--scheduler-mode cooperative
--batching-preset throughput`, not the default `serial`/`latency`), and its serving
path must route through the batched-decode kernel (as of mtplx 2.5.2 that kernel is
Phase-1 and not yet wired into the OpenAI serving path — the paged KV cache raises
at batch>1). Until then, concurrent requests are admitted and **queued** serially,
which is safe and correct — local-vibes is already shaped to cohort the moment the
server serves the batched path.

## The `delegate` tool

```
delegate(task, working_dir?=".", read_only?=false, temperature?, lane?)
```

- **task** — one complete, self-contained instruction, ideally with acceptance
  criteria ("…and run the test to confirm it exits 0").
- **working_dir** — sandbox root; every file/shell operation is confined here.
- **read_only** — `true` disables all writes/edits/bash (safe for search and
  analysis delegation).
- **temperature** — per-call sampling temperature (overrides
  `LOCAL_VIBES_TEMPERATURE`). Use `0` for deterministic output and to stay
  compatible with the greedy batched-decode cohort.
- **lane** — sticky-session lane id, sent as the `LOCAL_VIBES_LANE_HEADER`
  header (default `X-MTPLX-Lane`). Reuse the same id across related delegations
  so a cohort-aware server pins them to one lane; omit it for generic servers
  (no header is sent). All requests within a single delegate loop already share
  the lane, so a session is sticky by construction.

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
