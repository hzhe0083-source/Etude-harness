/**
 * Etude P6 editor guard (design §2 P6 row "只能写 evolvable/；读 E 划分的 trace",
 * §3.2 "P6 只收 accept/reject 一位", §3.5 row "编辑者偷看 V/T").
 *
 * Read-path ALLOW-list for the pi-mono editor session that runs in a candidate
 * git worktree. Every tool call is checked before it runs:
 *
 * - read / ls / grep / find: the target path (realpath-resolved, so symlinks
 *   cannot escape) must lie under one of
 *     <worktree>/evolvable, <worktree>/docs,
 *     and the directories in $ETUDE_EDITOR_EVIDENCE_DIRS (":"-separated; the
 *     E-split evidence P5 exports for this round: E traces, verdicts,
 *     per-layout official scores of E only).
 *   Everything else (traces/ of V/A-A/T episodes, the lineage receipts and
 *   evolution state, gate/, scorer/audit/, frozen/, research/, $HOME) is blocked.
 * - write / edit: only the whitelisted surface files
 *     evolvable/prompts/planner.md, evolvable/rules/recovery.yaml, evolvable/skills/SKILL.md
 *   ($ETUDE_EDITOR_SURFACE narrows this to the one surface open this round).
 * - bash and any other tool: blocked.
 *
 * Status goes to ctx.ui.setStatus("etude-editor-guard", ...) so the driver can
 * refuse a proposal from a session where the guard never reported.
 * <worktree> = $ETUDE_EDITOR_WORKTREE, else the session cwd.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { existsSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";

const STATUS_KEY = "etude-editor-guard";
const WRITABLE = ["evolvable/prompts/planner.md", "evolvable/rules/recovery.yaml", "evolvable/skills/SKILL.md"];
const READ_TOOLS = new Set(["read", "ls", "grep", "find"]);
const WRITE_TOOLS = new Set(["write", "edit"]);

function real(p: string): string {
  // realpath of the longest existing prefix, so a not-yet-existing file still resolves symlinked parents
  let cur = p;
  const tail: string[] = [];
  while (!existsSync(cur)) {
    const parent = dirname(cur);
    if (parent === cur) break;
    tail.unshift(cur.slice(parent.length + 1));
    cur = parent;
  }
  let base = cur;
  try {
    base = realpathSync(cur);
  } catch {
    /* keep */
  }
  return tail.length ? resolve(base, ...tail) : base;
}

function within(p: string, root: string): boolean {
  const r = relative(root, p);
  return r === "" || (!r.startsWith("..") && !isAbsolute(r));
}

export default function (pi: ExtensionAPI) {
  const wt = real(resolve(process.env.ETUDE_EDITOR_WORKTREE ?? process.cwd()));
  const evidence = (process.env.ETUDE_EDITOR_EVIDENCE_DIRS ?? "")
    .split(":")
    .filter((d) => d.trim() !== "")
    .map((d) => real(resolve(d)));
  const readRoots = [real(resolve(wt, "evolvable")), real(resolve(wt, "docs")), ...evidence];
  const surface = process.env.ETUDE_EDITOR_SURFACE;
  const writable = (surface ? WRITABLE.filter((w) => w === `evolvable/${surface}`) : WRITABLE).map((w) =>
    real(resolve(wt, w)),
  );
  let allowed = 0;
  let blocked = 0;

  const report = (ctx: any, extra: Record<string, unknown> = {}) => {
    try {
      ctx?.ui?.setStatus?.(STATUS_KEY, JSON.stringify({ allowed, blocked, readRoots, writable, ...extra }));
    } catch {
      /* best effort */
    }
  };
  const deny = (ctx: any, reason: string) => {
    blocked++;
    report(ctx, { lastBlocked: reason });
    return { block: true, reason: `etude-editor-guard: ${reason}` };
  };

  pi.on("session_start", async (_event, ctx) => report(ctx, { phase: "session_start" }));

  pi.on("tool_call", async (event: any, ctx) => {
    const name = String(event.toolName ?? "");
    const input = (event.input ?? {}) as Record<string, unknown>;
    const raw = typeof input.path === "string" && input.path !== "" ? (input.path as string) : ".";
    const target = real(resolve(wt, raw));
    if (READ_TOOLS.has(name)) {
      if (!readRoots.some((r) => within(target, r))) {
        return deny(ctx, `${name} ${raw}: outside the editor's read allow-list (evolvable/, docs/, E evidence)`);
      }
    } else if (WRITE_TOOLS.has(name)) {
      if (!writable.includes(target)) {
        return deny(ctx, `${name} ${raw}: only ${writable.map((w) => relative(wt, w)).join(", ")} may be written`);
      }
    } else {
      return deny(ctx, `tool '${name}' is not available to the editor`);
    }
    allowed++;
    report(ctx);
    return undefined;
  });

  pi.on("agent_end", async (_event, ctx) => {
    report(ctx, { phase: "agent_end" });
    pi.appendEntry(STATUS_KEY, { allowed, blocked });
  });
}

export const _test = { real, within };
