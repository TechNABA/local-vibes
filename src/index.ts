#!/usr/bin/env node
/**
 * local-vibes — bin entry. Exposes the `delegate` MCP tool over stdio and runs
 * each delegation through a bounded-concurrency gate. This is the only module
 * with side effects; all logic lives in agent.ts.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { runAgent, gate, config } from "./agent.js";

async function main(): Promise<void> {
  const argv = process.argv.slice(2);

  // Direct loop runner for testing, bypassing MCP stdio:
  //   local-vibes selftest "<task>" [working_dir] [--read-only]
  if (argv[0] === "selftest") {
    const task = argv[1] ?? "";
    const wd = argv[2] && !argv[2].startsWith("--") ? argv[2] : ".";
    const readOnly = argv.includes("--read-only");
    const tIdx = argv.indexOf("--temperature");
    const temperature = tIdx >= 0 ? Number(argv[tIdx + 1]) : undefined;
    const lIdx = argv.indexOf("--lane");
    const lane = lIdx >= 0 ? argv[lIdx + 1] : undefined;
    console.log(await runAgent(task, { workingDir: wd, readOnly, temperature, lane }));
    return;
  }

  const server = new McpServer({ name: "local-vibes", version: "0.1.0" });

  server.registerTool(
    "delegate",
    {
      description:
        "Delegate a self-contained coding or research task to a LOCAL model running in its " +
        "own sandboxed agent loop. USE PROACTIVELY to offload grunt work — codebase searches, " +
        "boilerplate, mechanical multi-file edits, test scaffolding — instead of doing it inline. " +
        "The subagent reads/edits files and runs shell commands confined to `working_dir`, and " +
        "returns a summary plus a git change report. Give it ONE clear, self-contained task with " +
        "enough context to act without follow-up questions. Safe to call multiple times " +
        "concurrently; in-flight runs are bounded by LOCAL_VIBES_MAX_CONCURRENCY.",
      inputSchema: {
        task: z.string().describe("A complete, self-contained instruction (include acceptance criteria)."),
        working_dir: z.string().optional().describe("Sandbox root for all file/shell operations. Defaults to CWD."),
        read_only: z.boolean().optional().describe("If true, disable all writes/edits/bash (safe for search/analysis)."),
        temperature: z.number().optional().describe("Sampling temperature for this task (overrides LOCAL_VIBES_TEMPERATURE). Use 0 for deterministic / greedy-cohort-compatible output."),
        lane: z.string().optional().describe("Sticky-session lane id, sent as the lane header (LOCAL_VIBES_LANE_HEADER) so a cohort-aware server pins this session to one lane. Reuse the same id to keep related delegations on one lane."),
      },
    },
    async ({ task, working_dir, read_only, temperature, lane }) => {
      const text = await gate.run(() =>
        runAgent(task, { workingDir: working_dir, readOnly: read_only, temperature, lane }),
      );
      return { content: [{ type: "text" as const, text }] };
    },
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
  // stdout is the MCP stream; status goes to stderr.
  console.error(
    `local-vibes ready — backend ${config.model} @ ${config.baseURL}, ` +
      `max concurrency ${config.maxConcurrency}`,
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
