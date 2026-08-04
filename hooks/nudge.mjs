#!/usr/bin/env node
// UserPromptSubmit hook: nudge the primary to use the local-vibes `delegate` tool
// ONLY when the user's prompt asks to offload to a local model. Targeted on
// purpose — it stays silent otherwise, so it is not intrusive and adds nothing to
// unrelated turns. Removed cleanly when the plugin is uninstalled.
import { readFileSync } from "node:fs";

let prompt = "";
try {
  prompt = (JSON.parse(readFileSync(0, "utf8")).prompt || "").toLowerCase();
} catch {
  process.exit(0);
}

const TRIGGERS = [
  "local llm",
  "local model",
  "local-vibes",
  "local vibes",
  "delegate to the local",
  "offload",
  "run it locally",
  "run this locally",
  "use the local",
];

if (TRIGGERS.some((t) => prompt.includes(t))) {
  process.stdout.write(
    "local-vibes hint: use the `delegate` MCP tool to run this on the local model. " +
      "Pass a complete, self-contained task and the working_dir. For bulk independent " +
      "work, fan out multiple concurrent `delegate` calls (up to the server's cohort width, commonly 8).",
  );
}
process.exit(0);
