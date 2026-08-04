---
name: local-vibes
description: Delegate self-contained coding grunt-work — codebase searches, boilerplate, mechanical multi-file edits, test scaffolding — to a LOCAL model via the local-vibes `delegate` MCP tool, instead of doing it inline. Invoke when the user says "delegate to the local llm/model", "use local-vibes", "offload this locally", "run it on the local model", or when facing bulk mechanical work that does not need the main model's judgment.
---

# local-vibes: delegate grunt-work to a local model

`local-vibes` exposes one MCP tool, **`delegate`**, that runs a real tool-calling
agent loop (read / edit / grep / bash, sandboxed) on a **local** model behind any
OpenAI-compatible endpoint. Use it to offload work off the main model.

## When to use it

- The user explicitly asks to "delegate to the local LLM / model", "use
  local-vibes", "offload locally", or "run it on the local model".
- There is bulk, mechanical, or self-contained work that does not need the main
  model's judgment: codebase searches, boilerplate/scaffolding, mechanical
  multi-file edits, test stubs, format/lint fixes.

## When NOT to use it

- Work that needs the current conversation's context or your own judgment — the
  delegated agent runs on a *local* model and does **not** see this chat.
- Anything where correctness is subtle. Treat the local model as a fast junior:
  review its diffs.

## How to call it

Give **one complete, self-contained task** with acceptance criteria, plus the
`working_dir` (it defaults to the CWD and every file/shell op is confined there):

```
delegate(
  task: "Add a `subtract(a,b)` to calc.ts next to add(), and a test asserting
         subtract(5,2)==3; run it to confirm, then finish.",
  working_dir: "/abs/path/to/repo",
  temperature: 0,        // optional; 0 = deterministic
  read_only: false,      // optional; true disables writes/edits/bash
  lane: "session-A"      // optional; sticky session id (sent as the OpenAI `user` field)
)
```

It returns a summary plus a `git diff --stat` of what changed. Because the
subagent has no memory of this conversation, put everything it needs in `task`.

## Running many at once (concurrency)

Independent tasks can be delegated concurrently — issue multiple `delegate` calls
in the same turn. A batching local server cohorts them (shared weight read →
higher aggregate throughput). Fan out up to the server's cohort width (commonly
8). Give each concurrent task its own `working_dir` (or a separate git worktree)
so they don't make conflicting edits.

## Backend

`local-vibes` is backend-agnostic — it talks standard OpenAI `/v1/chat/completions`
to whatever `LOCAL_VIBES_BASE_URL` / `LOCAL_VIBES_MODEL` point at (Ollama by
default). If a `delegate` call errors with a connection error, the local server
is down — tell the user rather than silently retrying.
