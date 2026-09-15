/**
 * Automatic handoff for local-harness.
 *
 * pi auto compaction is off (it discards the prompt cache), so a session that
 * fills the context window would otherwise get one token replies. This
 * extension hands off to a new session before that happens:
 *
 * 1. After each turn it checks context usage. Past the threshold it steers the
 *    agent to finish the current step and start no new work.
 * 2. When the agent settles past the threshold it runs /handoff.
 * 3. /handoff runs the `handoff` skill. The agent replies with a note of file
 *    pointers, decisions, challenges, and next steps; this extension saves the
 *    reply (a 4B model often skips a write tool call) and starts a new session
 *    whose first message tells the agent to read that note.
 *
 * /handoff can also be run by hand, with optional focus text.
 *
 * Threshold: contextWindow minus PI_HANDOFF_RESERVE (default 20000 tokens), or
 * PI_HANDOFF_AT_TOKENS when set (used for testing). A session that itself
 * started from a handoff must also grow MIN_NEW_WORK tokens past its first turn,
 * so a session that begins near the limit does not hand off in a loop.
 *
 * Install: symlink this file into ~/.pi/agent/extensions/.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { appendFileSync, existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";

const STATE_HOME = process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state");
const STATE_DIR = join(STATE_HOME, "local-harness");
const HANDOFF_DIR = join(STATE_DIR, "handoffs");
const TELEMETRY = join(STATE_DIR, "telemetry.jsonl");
const RESERVE = Number(process.env.PI_HANDOFF_RESERVE ?? 20000);
const AT_TOKENS = process.env.PI_HANDOFF_AT_TOKENS ? Number(process.env.PI_HANDOFF_AT_TOKENS) : undefined;
const MIN_NEW_WORK = 10000;
const KICKOFF_PREFIX = "Continue work handed off from a previous session";

type Usage = { tokens: number | null; contextWindow: number };

// A ctx goes stale once its session is replaced or disposed; reads then throw.
function usageOf(ctx: ExtensionContext): Usage | undefined {
  try {
    return ctx.getContextUsage() ?? undefined;
  } catch {
    return undefined;
  }
}

function limitFor(usage: Usage): number {
  return AT_TOKENS ?? usage.contextWindow - RESERVE;
}

// True when this session's first user message is a handoff kickoff.
function isHandoffChild(ctx: ExtensionContext): boolean {
  try {
    for (const entry of ctx.sessionManager.getBranch()) {
      if (entry.type !== "message" || entry.message.role !== "user") continue;
      const content = entry.message.content;
      const text = typeof content === "string" ? content : content.map((b) => ("text" in b ? b.text : "")).join("");
      return text.startsWith(KICKOFF_PREFIX);
    }
  } catch {
    // Stale ctx: treat as not a child.
  }
  return false;
}

// The last assistant text in the session, which is the handoff reply.
function lastAssistantText(ctx: ExtensionContext): string {
  const branch = ctx.sessionManager.getBranch();
  for (let i = branch.length - 1; i >= 0; i--) {
    const entry = branch[i];
    if (entry.type !== "message" || entry.message.role !== "assistant") continue;
    const content = entry.message.content;
    const text = Array.isArray(content)
      ? content.map((b) => (b.type === "text" ? b.text : "")).join("")
      : String(content ?? "");
    if (text.trim()) return text;
  }
  return "";
}

// Strip an outer code fence and anything before the note heading.
function cleanNote(text: string): string {
  let note = text.trim();
  const fence = note.match(/^```[a-z]*\n([\s\S]*?)\n```\s*$/);
  if (fence) note = fence[1].trim();
  const heading = note.indexOf("# Handoff");
  if (heading > 0) note = note.slice(heading);
  return note;
}

function notePath(cwd: string): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  const name = basename(cwd).replace(/[^A-Za-z0-9_.]+/g, "_") || "root";
  return join(HANDOFF_DIR, `${stamp}-${name}.md`);
}

function log(record: { [key: string]: unknown }): void {
  try {
    mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
    appendFileSync(
      TELEMETRY,
      JSON.stringify({ schema: "local-harness/telemetry/v1", ts: Date.now(), pid: process.pid, ...record }) + "\n",
      { mode: 0o600 },
    );
  } catch {
    // Logging must never break a handoff.
  }
}

export default function (pi: ExtensionAPI) {
  let warned = false;
  let triggered = false;
  let firstTurnTokens: number | undefined;

  const overLimit = (ctx: ExtensionContext, usage: Usage | undefined): boolean => {
    if (!usage || usage.tokens === null) return false;
    firstTurnTokens ??= usage.tokens;
    if (usage.tokens < limitFor(usage)) return false;
    if (isHandoffChild(ctx) && usage.tokens < firstTurnTokens + MIN_NEW_WORK) return false;
    return true;
  };

  pi.on("turn_end", async (_event, ctx) => {
    const usage = usageOf(ctx);
    if (warned || !overLimit(ctx, usage)) return;
    warned = true;
    log({ event: "handoff_warning", tokens: usage?.tokens, limit: usage && limitFor(usage) });
    pi.sendMessage(
      {
        customType: "handoff-warning",
        content:
          "Context window is nearly full. Finish the step you are on, start no new work, and end your turn. " +
          "A handoff note and a new session follow automatically.",
        display: true,
      },
      { deliverAs: "steer" },
    );
  });

  pi.on("agent_settled", async (_event, ctx) => {
    if (triggered || !(warned || overLimit(ctx, usageOf(ctx)))) return;
    triggered = true;
    pi.sendUserMessage("/handoff", { expandPromptTemplates: true });
  });

  pi.registerCommand("handoff", {
    description: "Write a handoff note and continue in a new session",
    handler: async (args, ctx) => {
      triggered = true;
      const cwd = ctx.cwd;
      const path = notePath(cwd);
      const parentSession = ctx.sessionManager.getSessionFile();
      const usage = usageOf(ctx);
      mkdirSync(HANDOFF_DIR, { recursive: true, mode: 0o700 });
      log({ event: "handoff_start", path, tokens: usage?.tokens, cwd });

      const focus = args.trim();
      try {
        await ctx.waitForIdle();
        pi.sendUserMessage(`/skill:handoff ${path}${focus ? `\nFocus for the next session: ${focus}` : ""}`, {
          expandPromptTemplates: true,
        });
        // sendUserMessage starts the turn asynchronously; wait until it has begun, then until it ends.
        for (let i = 0; i < 50 && ctx.isIdle(); i++) await new Promise((r) => setTimeout(r, 100));
        await ctx.waitForIdle();
      } catch (error) {
        // The session was closed or replaced while the note was being written.
        log({ event: "handoff_failed", path, reason: `session closed: ${String(error).slice(0, 120)}` });
        return;
      }

      if (!existsSync(path) || statSync(path).size === 0) {
        let note = "";
        try {
          note = cleanNote(lastAssistantText(ctx));
        } catch {
          // Stale ctx: fall through to the failure below.
        }
        if (note.startsWith("# Handoff") && note.length > 200) {
          writeFileSync(path, note + "\n", { mode: 0o600 });
          log({ event: "handoff_note_saved", path, chars: note.length });
        }
      }

      if (!existsSync(path) || statSync(path).size === 0) {
        log({ event: "handoff_failed", path, reason: "note not written" });
        ctx.ui.notify(`Handoff note was not written to ${path}; staying in this session.`, "error");
        triggered = false;
        return;
      }

      const kickoff =
        `Continue work handed off from a previous session in ${cwd}.\n` +
        `First read the handoff note at ${path}.\n` +
        "Open the files it points to before acting, avoid the approaches listed under Challenges, " +
        "and start with the first item under Next steps.";

      const result = await ctx.newSession({
        parentSession,
        withSession: async (next) => {
          log({ event: "handoff_done", path, parentSession });
          next.ui.notify(`Handed off. Note: ${path}`, "info");
          await next.sendUserMessage(kickoff);
        },
      });
      if (result.cancelled) {
        log({ event: "handoff_failed", path, reason: "new session cancelled" });
        triggered = false;
      }
    },
  });
}
