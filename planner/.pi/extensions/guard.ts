/**
 * Etude planner guard (design §2 P1 "tool_call 扩展拦截其他一切", §3.5 rows
 * "读 scene_manager…" and "超调用预算").
 *
 * - Every tool call whose name is not one of the 7 MCP tools P2 exposes is
 *   blocked with { block: true, reason }. MCP-adapter prefixes "etude_" /
 *   "etude-" are tolerated (same rule as etude.planner.rpc_driver.canonical_tool).
 * - Per planning call (agent_start .. agent_end) allowed calls are counted;
 *   beyond min(ETUDE_MAX_TOOL_CALLS, frozen budget.max_tool_calls_per_plan = 40)
 *   everything except submit_sequence and budget is blocked, so a looping
 *   planner must submit. The per-episode LLM-request cap
 *   (budget.vlm_requests_per_episode) is enforced on the P2 side
 *   (etude.planner.rpc_driver aborts pi when it is used up).
 * - Status is reported through ctx.ui.setStatus("etude-guard", ...), which
 *   pi's RPC mode forwards as extension_ui_request/setStatus; the P2 driver
 *   discards any sequence from a session where this never appeared.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOLS = [
  "observe",
  "inspect",
  "perceive",
  "memory_lookup",
  "submit_sequence",
  "get_verdict",
  "budget",
];
const PREFIXES = ["", "etude_", "etude-"];
const ALWAYS_ALLOWED = new Set(["submit_sequence", "budget"]);
const STATUS_KEY = "etude-guard";

// Frozen ceiling (frozen/params/thresholds.yaml budget.max_tool_calls_per_plan, tighten-only).
// ETUDE_MAX_TOOL_CALLS can only lower it. If the file cannot be read, fall back to 40.
const FALLBACK_MAX_TOOL_CALLS = 40;

function frozenMaxToolCalls(): number {
  const candidates: string[] = [];
  try {
    candidates.push(resolve(dirname(fileURLToPath(import.meta.url)), "../../../frozen/params/thresholds.yaml"));
  } catch {
    /* not an ES module URL */
  }
  candidates.push(resolve(process.cwd(), "../frozen/params/thresholds.yaml"));
  for (const p of candidates) {
    try {
      const m = readFileSync(p, "utf8").match(/budget\.max_tool_calls_per_plan:\s*\{\s*value:\s*(\d+)/);
      if (m) return Number(m[1]);
    } catch {
      /* try next */
    }
  }
  return FALLBACK_MAX_TOOL_CALLS;
}

function canonical(name: string): string | null {
  for (const p of PREFIXES) {
    if (name.startsWith(p) && TOOLS.includes(name.slice(p.length))) return name.slice(p.length);
  }
  return null;
}

export default function (pi: ExtensionAPI) {
  const ceiling = frozenMaxToolCalls();
  const parsed = Number(process.env.ETUDE_MAX_TOOL_CALLS ?? String(ceiling));
  const maxCalls = Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, ceiling) : ceiling;
  let counts: Record<string, number> = {};
  let allowed = 0;
  let blocked = 0;
  let totalAllowed = 0;
  let totalBlocked = 0;

  const report = (ctx: any, extra: Record<string, unknown> = {}) => {
    const text = JSON.stringify({ allowed, blocked, totalAllowed, totalBlocked, counts, maxCalls, ...extra });
    try {
      ctx?.ui?.setStatus?.(STATUS_KEY, text);
    } catch {
      /* status is best-effort */
    }
  };

  pi.on("session_start", async (_event, ctx) => {
    report(ctx, { phase: "session_start" });
  });

  pi.on("agent_start", async (_event, ctx) => {
    counts = {};
    allowed = 0;
    blocked = 0;
    report(ctx, { phase: "agent_start" });
  });

  pi.on("tool_call", async (event, ctx) => {
    const name = String(event.toolName ?? "");
    const tool = canonical(name);
    if (tool === null) {
      blocked++;
      totalBlocked++;
      const reason = `etude-guard: tool '${name}' is not allowed; only ${TOOLS.join(", ")}`;
      try {
        ctx?.ui?.notify?.(reason, "warning");
      } catch {
        /* ignore */
      }
      report(ctx, { lastBlocked: name });
      return { block: true, reason };
    }
    if (allowed >= maxCalls && !ALWAYS_ALLOWED.has(tool)) {
      blocked++;
      totalBlocked++;
      report(ctx, { lastBlocked: name, overBudget: true });
      return {
        block: true,
        reason: `etude-guard: tool-call budget (${maxCalls}) for this planning call is used up; call submit_sequence now`,
      };
    }
    allowed++;
    totalAllowed++;
    counts[tool] = (counts[tool] ?? 0) + 1;
    return undefined;
  });

  pi.on("agent_end", async (_event, ctx) => {
    report(ctx, { phase: "agent_end" });
    pi.appendEntry("etude-guard", { allowed, blocked, counts, maxCalls });
  });
}
