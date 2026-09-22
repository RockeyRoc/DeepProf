import { CommandBus } from "../../../packages/client_sdk/command_bus.js";
import { EventClient } from "../../../packages/client_sdk/event_client.js";
import { SessionClient } from "../../../packages/client_sdk/session_client.js";
import type { RuntimeEvent, SessionMessage, SessionSummary } from "../../../packages/client_sdk/types.js";

export interface AskResult {
  session_id: string;
  answer: string;
  usage: Record<string, unknown>;
  provider_profile: string | null;
  model: string | null;
  last_sequence: number;
  events: RuntimeEvent[];
}

export function clients(baseUrl: string, learnerId: string): { commands: CommandBus; sessions: SessionClient } {
  const commands = new CommandBus(baseUrl, "cli", "cli", learnerId);
  return { commands, sessions: new SessionClient(baseUrl, commands) };
}

export async function ask(
  baseUrl: string,
  learnerId: string,
  sessionId: string,
  content: string,
  onDelta?: (text: string) => void,
): Promise<AskResult> {
  const { sessions } = clients(baseUrl, learnerId);
  const summary = await sessions.get(sessionId);
  const events: RuntimeEvent[] = [];
  let answer = "";
  let usage: Record<string, unknown> = {};
  let providerProfile: string | null = null;
  let model: string | null = null;
  let settled = false;
  let resolveTurn: (() => void) | null = null;
  let rejectTurn: ((error: Error) => void) | null = null;
  const done = new Promise<void>((resolve, reject) => { resolveTurn = resolve; rejectTurn = reject; });
  const eventClient = new EventClient({
    baseUrl,
    onEvent: (event) => {
      events.push(event);
      if (event.type === "model.requested") {
        providerProfile = String(event.payload.provider_profile || "") || null;
        model = String(event.payload.model || "") || null;
      } else if (event.type === "model.stream.delta") {
        const text = String(event.payload.text || "");
        answer += text;
        onDelta?.(text);
      } else if (event.type === "model.completed") {
        usage = (event.payload.usage && typeof event.payload.usage === "object") ? event.payload.usage as Record<string, unknown> : {};
      } else if (event.type === "agent.failed") {
        rejectTurn?.(new Error(String((event.payload.error as Record<string, unknown> | undefined)?.message || "agent_failed")));
      } else if (event.type === "agent.turn.completed" && String(event.payload.status || "") === "ok") {
        resolveTurn?.();
      }
    },
    onError: (error) => { if (!settled) rejectTurn?.(error); },
  });
  eventClient.connect(sessionId, summary.last_sequence);
  let timeout: NodeJS.Timeout | undefined;
  try {
    await sessions.send(sessionId, content);
    await Promise.race([
      done,
      new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error("turn_timeout")), 180_000); }),
    ]);
    settled = true;
    return { session_id: sessionId, answer, usage, provider_profile: providerProfile, model, last_sequence: eventClient.lastSequence, events };
  } finally {
    if (timeout) clearTimeout(timeout);
    settled = true;
    eventClient.close();
  }
}

export function buildTree(sessions: SessionSummary[]): Array<SessionSummary & { children: SessionSummary[] }> {
  const nodes = new Map(sessions.map((item) => [item.session_id, { ...item, children: [] as SessionSummary[] }]));
  const roots: Array<SessionSummary & { children: SessionSummary[] }> = [];
  for (const node of nodes.values()) {
    const parent = node.parent_id ? nodes.get(node.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

export function transcriptText(messages: SessionMessage[]): string {
  return messages.map((message) => `${message.role}: ${message.content}`).join("\n");
}
