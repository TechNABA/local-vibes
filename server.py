#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0,<2", "openai>=1.40.0"]
# ///
"""
local-subagent-mcp — a generic "delegate to a local model" subagent MCP server.

Exposes ONE MCP tool, `delegate`, to a host (e.g. Claude Code). The tool runs a
real tool-calling agent loop against ANY OpenAI-compatible chat-completions
endpoint (Ollama, LM Studio, vLLM, or a remote OpenAI-compatible API), letting a
local model read/edit files, run shell commands, and iterate until a delegated
task is done. The host's own model/auth is never touched — this is a plain MCP
tool call, so a Max-subscription Claude Code primary can offload grunt work to a
local model with no ANTHROPIC_BASE_URL change.

Config is entirely via environment variables (see README). Nothing is
model-specific: point SUBAGENT_BASE_URL/SUBAGENT_MODEL at any compatible server.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from openai import OpenAI
from mcp.server.fastmcp import FastMCP

# ── Config (all overridable via env) ─────────────────────────────────────────
BASE_URL = os.environ.get("SUBAGENT_BASE_URL", "http://localhost:11434/v1")
API_KEY = os.environ.get("SUBAGENT_API_KEY", "not-needed")  # local servers ignore it
MODEL = os.environ.get("SUBAGENT_MODEL", "qwen3-coder:30b")
MAX_ITERS = int(os.environ.get("SUBAGENT_MAX_ITERS", "25"))
TEMPERATURE = float(os.environ.get("SUBAGENT_TEMPERATURE", "0.1"))
ALLOW_BASH = os.environ.get("SUBAGENT_ALLOW_BASH", "1") == "1"
TOOL_RESULT_CAP = int(os.environ.get("SUBAGENT_TOOL_RESULT_CAP", "12000"))  # chars
BASH_TIMEOUT = int(os.environ.get("SUBAGENT_BASH_TIMEOUT", "120"))  # seconds

mcp = FastMCP("local-subagent")


# ── Sandbox helpers ──────────────────────────────────────────────────────────
def _resolve(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes the sandbox."""
    full = (root / rel).resolve()
    if full != root and root not in full.parents:
        raise ValueError(f"path {rel!r} escapes working_dir")
    return full


def _cap(text: str) -> str:
    if len(text) <= TOOL_RESULT_CAP:
        return text
    return text[:TOOL_RESULT_CAP] + f"\n...[truncated {len(text) - TOOL_RESULT_CAP} chars]"


# ── Inner tools the local model may call ─────────────────────────────────────
def _read_file(root: Path, path: str) -> str:
    p = _resolve(root, path)
    return _cap(p.read_text(errors="replace"))


def _write_file(root: Path, path: str, content: str) -> str:
    p = _resolve(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {path}"


def _edit_file(root: Path, path: str, old_string: str, new_string: str) -> str:
    p = _resolve(root, path)
    text = p.read_text(errors="replace")
    n = text.count(old_string)
    if n == 0:
        return f"ERROR: old_string not found in {path}"
    if n > 1:
        return f"ERROR: old_string occurs {n}× in {path}; make it unique"
    p.write_text(text.replace(old_string, new_string, 1))
    return f"edited {path}"


def _list_dir(root: Path, path: str = ".") -> str:
    p = _resolve(root, path)
    if not p.is_dir():
        return f"ERROR: {path} is not a directory"
    entries = sorted(
        (c.name + ("/" if c.is_dir() else "")) for c in p.iterdir()
    )
    return "\n".join(entries) if entries else "(empty)"


def _grep(root: Path, pattern: str, path: str = ".") -> str:
    base = _resolve(root, path)
    rx = re.compile(pattern)
    hits, files = [], [base] if base.is_file() else base.rglob("*")
    for f in files:
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{i}: {line.strip()}")
                    if len(hits) >= 200:
                        return _cap("\n".join(hits) + "\n...[capped at 200 hits]")
        except (OSError, UnicodeError):
            continue
    return _cap("\n".join(hits)) if hits else "(no matches)"


def _run_bash(root: Path, command: str) -> str:
    if not ALLOW_BASH:
        return "ERROR: bash is disabled (SUBAGENT_ALLOW_BASH=0)"
    try:
        r = subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True,
            text=True, timeout=BASH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {BASH_TIMEOUT}s"
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    return _cap(f"[exit {r.returncode}]\n{out}".rstrip())


def _tool_schemas(read_only: bool) -> list[dict]:
    def fn(name, desc, props, required):
        return {
            "type": "function",
            "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }

    s = str  # shorthand
    tools = [
        fn("read_file", "Read a file's contents.",
           {"path": {"type": "string"}}, ["path"]),
        fn("list_dir", "List the entries of a directory (default: working dir root).",
           {"path": {"type": "string"}}, []),
        fn("grep", "Search files under `path` for a regex pattern.",
           {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
        fn("finish", "Call when the task is complete. Provide a concise summary of what you did.",
           {"summary": {"type": "string"}}, ["summary"]),
    ]
    if not read_only:
        tools[3:3] = [  # insert mutating tools before finish
            fn("write_file", "Create or overwrite a file with `content`.",
               {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            fn("edit_file", "Replace the unique occurrence of `old_string` with `new_string` in a file.",
               {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}},
               ["path", "old_string", "new_string"]),
        ]
        if ALLOW_BASH:
            tools[-1:-1] = [
                fn("run_bash", "Run a shell command in the working directory and return its output.",
                   {"command": {"type": "string"}}, ["command"]),
            ]
    return tools


_DISPATCH = {
    "read_file": _read_file, "write_file": _write_file, "edit_file": _edit_file,
    "list_dir": _list_dir, "grep": _grep, "run_bash": _run_bash,
}
_MUTATORS = {"write_file", "edit_file", "run_bash"}


# ── The agent loop ───────────────────────────────────────────────────────────
_SYSTEM = """You are an autonomous coding subagent working inside a sandboxed \
working directory. You complete the delegated task by calling the provided tools \
to inspect and modify files. Work iteratively: look before you edit, make the \
smallest change that satisfies the task, and verify your work when possible. Do \
not ask questions — decide and act. When the task is fully done, call `finish` \
with a short summary of exactly what you changed. All file paths are relative to \
the working directory root; you cannot access anything outside it."""


def _git_change_report(root: Path) -> str:
    """Read-only summary of changes: tracked diffstat + untracked new files."""
    def git(*args: str) -> str:
        try:
            r = subprocess.run(["git", "-C", str(root), *args],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip()
        except Exception:
            return ""
    lines = []
    stat = git("diff", "--stat")
    if stat:
        lines.append(stat)
    new = git("ls-files", "--others", "--exclude-standard")
    if new:
        lines.append("new (untracked) files:\n" + new)
    return "\n".join(lines)


def run_agent(task: str, working_dir: str, read_only: bool) -> str:
    root = Path(working_dir).resolve()
    if not root.is_dir():
        return f"ERROR: working_dir {working_dir!r} is not a directory"

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    tools = _tool_schemas(read_only)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content":
            f"Working directory root: {root}\n\nTask:\n{task}\n\n"
            f"Start by exploring with list_dir/read_file, then act."},
    ]
    touched: set[str] = set()

    def finalize(summary: str, iters: int) -> str:
        parts = [
            f"[local-subagent finished in {iters} iteration(s)]",
            f"backend: {MODEL} @ {BASE_URL}",
        ]
        if touched:
            parts.append("files touched: " + ", ".join(sorted(touched)))
        changes = _git_change_report(root) if not read_only else ""
        if changes:
            parts.append("git changes:\n" + changes)
        parts.append("\nsummary:\n" + (summary or "(none)"))
        return "\n".join(parts)

    for i in range(1, MAX_ITERS + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, tools=tools,
                tool_choice="auto", temperature=TEMPERATURE,
            )
        except Exception as e:  # noqa: BLE001 — surface any backend error to the host
            return f"ERROR calling backend {MODEL} @ {BASE_URL}: {e}"

        msg = resp.choices[0].message
        assistant = {"role": "assistant", "content": msg.content or None}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        messages.append(assistant)

        if not msg.tool_calls:
            # Model answered without calling a tool — treat its prose as the result.
            return finalize(msg.content or "(model returned no content)", i)

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "finish":
                return finalize(args.get("summary", ""), i)
            handler = _DISPATCH.get(name)
            if handler is None:
                result = f"ERROR: unknown tool {name}"
            elif read_only and name in _MUTATORS:
                result = f"ERROR: {name} is disabled in read_only mode"
            else:
                try:
                    result = handler(root, **args)
                    if name in _MUTATORS and not result.startswith("ERROR") and "path" in args:
                        touched.add(args["path"])
                except Exception as e:  # noqa: BLE001 — feed tool errors back to the model
                    result = f"ERROR in {name}: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": _cap(result)})

    return finalize("(reached max iterations without calling finish)", MAX_ITERS)


# ── MCP tool surface ─────────────────────────────────────────────────────────
@mcp.tool()
def delegate(task: str, working_dir: str = ".", read_only: bool = False) -> str:
    """Delegate a self-contained coding or research task to a LOCAL model running
    in its own sandboxed agent loop. USE PROACTIVELY to offload grunt work —
    codebase searches, boilerplate, mechanical multi-file edits, test scaffolding
    — instead of doing it inline.

    The subagent can read/edit files and run shell commands, all confined to
    `working_dir`. It iterates autonomously and returns a summary plus a
    `git diff --stat` of what changed. Give it ONE clear, self-contained task with
    enough context to act without follow-up questions.

    Args:
        task: A complete, self-contained instruction (include acceptance criteria).
        working_dir: Sandbox root for all file/shell operations. Defaults to CWD.
        read_only: If true, disable all writes/edits/bash (safe for search/analysis).
    """
    return run_agent(task, working_dir, read_only)


if __name__ == "__main__":
    import sys
    # `server.py selftest "<task>" [working_dir] [--read-only]` runs the loop
    # directly against the configured backend, bypassing MCP stdio — for testing.
    if len(sys.argv) >= 3 and sys.argv[1] == "selftest":
        wd = sys.argv[3] if len(sys.argv) >= 4 and not sys.argv[3].startswith("--") else "."
        ro = "--read-only" in sys.argv
        print(run_agent(sys.argv[2], wd, ro))
    else:
        mcp.run()  # stdio transport
