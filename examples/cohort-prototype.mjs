// 8-agent cohort prototype: fire N concurrent delegate loops at the backend and
// measure whether they cohort (aggregate ≈ one agent's time) or serialize.
//   N=8 LOCAL_VIBES_BASE_URL=http://127.0.0.1:8080/v1 \
//   LOCAL_VIBES_MODEL=mtplx-qwen36-27b-optimized-speed-v2 node examples/cohort-prototype.mjs
import { performance } from "node:perf_hooks";
import fs from "node:fs";
import { execSync } from "node:child_process";
import { runAgent, config } from "../dist/agent.js";

const N = Number(process.env.N || 8);
const ROOT = "/tmp/lv-cohort";
fs.rmSync(ROOT, { recursive: true, force: true });

const dirs = [];
for (let i = 0; i < N; i++) {
  const d = `${ROOT}/agent-${i}`;
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(`${d}/seed.txt`, String(i + 1));
  execSync("git init -q && git add -A && git -c user.email=t@t.co -c user.name=t commit -qm init", { cwd: d });
  dirs.push(d);
}

const TASK =
  "Read seed.txt (it contains one integer). Write a file double.txt containing that integer multiplied by 2, nothing else. Then finish.";

console.error(`firing ${N} concurrent delegate loops @ ${config.model} @ ${config.baseURL} (temp 0)...`);
const t0 = performance.now();
const results = await Promise.all(
  dirs.map((d) => {
    const s = performance.now();
    return runAgent(TASK, { workingDir: d, temperature: 0 }).then((out) => ({
      ms: performance.now() - s,
      ok: !out.startsWith("ERROR") && !out.includes("max iterations"),
    }));
  }),
);
const wall = performance.now() - t0;

let correct = 0;
for (let i = 0; i < N; i++) {
  const p = `${dirs[i]}/double.txt`;
  const got = fs.existsSync(p) ? fs.readFileSync(p, "utf8").trim() : "<missing>";
  if (got === String((i + 1) * 2)) correct++;
}

const times = results.map((r) => r.ms).sort((a, b) => a - b);
const sum = times.reduce((a, b) => a + b, 0);
const s = (ms) => (ms / 1000).toFixed(1) + "s";
console.log(`\n=== ${N}-agent cohort prototype ===`);
console.log(`wall (all ${N} concurrent): ${s(wall)}`);
console.log(`per-agent latency: min ${s(times[0])} / med ${s(times[times.length >> 1])} / max ${s(times[times.length - 1])}`);
console.log(`succeeded ${results.filter((r) => r.ok).length}/${N}   correct output ${correct}/${N}`);
console.log(`concurrency speedup (sum/wall): ${(sum / wall).toFixed(2)}x   (>1 → server cohorted; ~1 → serialized)`);
