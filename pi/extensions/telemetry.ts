/**
 * Telemetry for local-harness.
 *
 * Appends one JSON line per turn, tool call, and provider error to
 * ~/.local/state/local-harness/telemetry.jsonl. FleetView shows live state
 * inside pi; this file is the record other processes (harness report) read.
 *
 * Tool arguments are reduced to a path or a short command so the file stays
 * small. Full transcripts already live in ~/.pi/agent/sessions.
 *
 * Install: symlink this file into ~/.pi/agent/extensions/.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const STATE_HOME = process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state");
const FILE = join(STATE_HOME, "local-harness", "telemetry.jsonl");

type Record = { [key: string]: unknown };

function summarizeArgs(tool: string, args: unknown): Record {
  const a = (args ?? {}) as { [key: string]: unknown };
  const out: Record = {};
  if (typeof a.path === "string") out.path = a.path;
  if (typeof a.command === "string") out.command = a.command.slice(0, 300);
  if (typeof a.agent === "string") out.agent = a.agent;
  if (typeof a.task === "string") out.task = a.task.slice(0, 160);
  if (tool === "subagent" && Array.isArray(a.tasks)) out.tasks = a.tasks.length;
  return out;
}

function textLength(content: unknown, type: string): number {
  if (!Array.isArray(content)) return 0;
  let n = 0;
  for (const block of content as Array<{ type?: string; text?: string; thinking?: string }>) {
    if (block.type === type) n += (block.text ?? block.thinking ?? "").length;
  }
  return n;
}

export default function (pi: ExtensionAPI) {
  let sessionId = "";
  let cwd = "";
  const toolStarts = new Map<string, number>();

  const write = (record: Record) => {
    try {
      mkdirSync(dirname(FILE), { recursive: true });
      appendFileSync(FILE, JSON.stringify({ ts: Date.now(), pid: process.pid, session: sessionId, cwd, ...record }) + "\n");
    } catch {
      // Telemetry must never break a session.
    }
  };

  pi.on("session_start", async (_event, ctx) => {
    sessionId = ctx.sessionManager.getSessionId?.() ?? "";
    cwd = ctx.cwd;
    write({ event: "session_start", model: ctx.model?.id, provider: ctx.model?.provider, mode: ctx.mode });
  });

  pi.on("turn_start", async (event, ctx) => {
    write({ event: "turn_start", turn: event.turnIndex, model: ctx.model?.id });
  });

  pi.on("turn_end", async (event, ctx) => {
    const message = event.message as { usage?: Record; stopReason?: string; content?: unknown } | undefined;
    const usage = message?.usage;
    const content = message?.content;
    const toolCalls = Array.isArray(content)
      ? (content as Array<{ type?: string; name?: string }>).filter((b) => b.type === "toolCall").map((b) => b.name)
      : [];
    write({
      event: "turn_end",
      turn: event.turnIndex,
      model: ctx.model?.id,
      stop: message?.stopReason,
      tokens: usage ? { input: usage.input, output: usage.output, cacheRead: usage.cacheRead } : undefined,
      textChars: textLength(content, "text"),
      thinkingChars: textLength(content, "thinking"),
      toolCalls,
    });
  });

  pi.on("tool_execution_start", async (event) => {
    toolStarts.set(event.toolCallId, Date.now());
    write({ event: "tool_start", tool: event.toolName, id: event.toolCallId, ...summarizeArgs(event.toolName, event.args) });
  });

  pi.on("tool_execution_end", async (event) => {
    const started = toolStarts.get(event.toolCallId);
    toolStarts.delete(event.toolCallId);
    const result = event.result as { content?: unknown } | undefined;
    write({
      event: "tool_end",
      tool: event.toolName,
      id: event.toolCallId,
      ms: started ? Date.now() - started : undefined,
      error: Boolean(event.isError),
      resultChars: textLength(result?.content, "text"),
    });
  });

  pi.on("after_provider_response", async (event, ctx) => {
    if (event.status >= 400) write({ event: "provider_error", status: event.status, model: ctx.model?.id });
  });

  pi.on("agent_settled", async (_event, ctx) => {
    write({ event: "agent_settled", model: ctx.model?.id });
  });
}
