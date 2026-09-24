import { CommandBus } from "../../../packages/client_sdk/command_bus.js";
import { EventClient } from "../../../packages/client_sdk/event_client.js";
import { SessionClient } from "../../../packages/client_sdk/session_client.js";
import type { ExperimentGroup, RuntimeEvent, SessionMessage, SessionSummary } from "../../../packages/client_sdk/types.js";

export interface AskResult {
  session_id: string;
  answer: string;
  usage: Record<string, unknown>;
  provider_profile: string | null;
  model: string | null;
  last_sequence: number;
  trace_id: string;
  session_mode: "chat" | "study";
  turn_mode: "chat" | "study";
  routing_reason: string;
  routing_version: string;
  experiment_group: ExperimentGroup | "legacy" | "";
  action: string;
  policy_version: string;
  learner_estimate_status: string;
  mastery: number | null;
  learner_evidence_count: number;
  learner_updated_at: string | null;
  learner_uncertainty: number | null;
  bkt_model_version: string;
  evidence_refs: Array<Record<string, unknown>>;
  streamed: boolean;
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
  requestedAction = "",
  signal?: AbortSignal,
): Promise<AskResult> {
  const { sessions, commands } = clients(baseUrl, learnerId);
  const summary = await sessions.get(sessionId);
  const accepted = await sessions.send(sessionId, content, requestedAction);
  const traceId = String(accepted.trace_id || "");
  if (!traceId) throw new Error("turn_trace_id_missing");
  const events: RuntimeEvent[] = [];
  let answer = "";
  let usage: Record<string, unknown> = {};
  let providerProfile: string | null = null;
  let model: string | null = null;
  let action = "";
  let policyVersion = "";
  let learnerEstimateStatus = "";
  let mastery: number | null = null;
  let learnerEvidenceCount = 0;
  let learnerUpdatedAt: string | null = null;
  let learnerUncertainty: number | null = null;
  let bktModelVersion = "";
  let evidenceRefs: Array<Record<string, unknown>> = [];
  let experimentGroup: ExperimentGroup | "legacy" | "" = summary.experiment_group || "";
  let turnMode: "chat" | "study" = summary.session_mode || "study";
  let routingReason = "";
  let routingVersion = "";
  let streamed = false;
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
        streamed = true;
        onDelta?.(text);
      } else if (event.type === "model.completed") {
        usage = (event.payload.usage && typeof event.payload.usage === "object") ? event.payload.usage as Record<string, unknown> : {};
      } else if (event.type === "agent.turn.started") {
        turnMode = event.payload.turn_mode === "chat" ? "chat" : "study";
        routingReason = String(event.payload.routing_reason || routingReason);
        routingVersion = String(event.payload.routing_version || routingVersion);
      } else if (event.type === "agent.failed") {
        rejectTurn?.(new Error(String((event.payload.error as Record<string, unknown> | undefined)?.message || "agent_failed")));
      } else if (event.type === "teaching.turn.completed") {
        turnMode = "study";
        routingReason = String(event.payload.routing_reason || "");
        action = String(event.payload.action || "");
        policyVersion = String(event.payload.policy_version || policyVersion);
        const group = String(event.payload.group || "").toUpperCase();
        if (group === "A" || group === "B" || group === "C") experimentGroup = group;
        evidenceRefs = Array.isArray(event.payload.evidence_refs) ? event.payload.evidence_refs as Array<Record<string, unknown>> : [];
      } else if (event.type === "conversation.turn.completed") {
        turnMode = "chat";
        action = "chat";
        routingReason = String(event.payload.routing_reason || "");
        routingVersion = String(event.payload.routing_version || routingVersion);
      } else if (event.type === "pedagogy.decision" && event.payload.node === "assess") {
        const value = event.payload.learner_estimate && typeof event.payload.learner_estimate === "object"
          ? event.payload.learner_estimate as Record<string, unknown> : {};
        learnerEstimateStatus = String(value.status || "");
        mastery = typeof value.mastery === "number" ? value.mastery : null;
        learnerEvidenceCount = Number(value.evidence_count ?? event.payload.learner_evidence_count ?? 0);
        learnerUpdatedAt = String(value.updated_at || "") || null;
        learnerUncertainty = typeof value.uncertainty === "number" ? value.uncertainty : null;
        bktModelVersion = String(value.model_version || event.payload.bkt_model_version || "");
      } else if (event.type === "agent.turn.completed") {
        if (String(event.payload.status || "") === "ok") resolveTurn?.();
        else rejectTurn?.(new Error(`turn_${String(event.payload.status || "failed")}`));
      }
    },
    onError: (error) => { if (!settled) rejectTurn?.(error); },
  });
  eventClient.connect(sessionId, summary.last_sequence, traceId);
  let timeout: NodeJS.Timeout | undefined;
  let abortListener: (() => void) | undefined;
  const cancelled = signal ? new Promise<never>((_, reject) => {
    abortListener = () => {
      if (settled) return;
      void commands.send(commands.create("turn.cancel", {}, sessionId))
        .catch(() => undefined)
        .finally(() => reject(new Error("turn_cancelled")));
    };
    if (signal.aborted) abortListener();
    else signal.addEventListener("abort", abortListener, { once: true });
  }) : new Promise<never>(() => undefined);
  try {
    await Promise.race([
      done,
      cancelled,
      new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error("turn_timeout")), 180_000); }),
    ]);
    settled = true;
    if (!answer) {
      const transcript = await sessions.transcript(sessionId);
      answer = transcript.filter((message) => message.role === "assistant").at(-1)?.content || "";
    }
    return { session_id: sessionId, answer, usage, provider_profile: providerProfile || summary.provider_profile || null,
      model: model || summary.model || null, last_sequence: eventClient.lastSequence, trace_id: traceId,
      session_mode: summary.session_mode || "study", turn_mode: turnMode, routing_reason: routingReason,
      routing_version: routingVersion,
      experiment_group: experimentGroup, action, policy_version: policyVersion,
      learner_estimate_status: learnerEstimateStatus, mastery, learner_evidence_count: learnerEvidenceCount,
      learner_updated_at: learnerUpdatedAt, learner_uncertainty: learnerUncertainty,
      bkt_model_version: bktModelVersion, evidence_refs: evidenceRefs, streamed, events };
  } finally {
    if (timeout) clearTimeout(timeout);
    if (abortListener) signal?.removeEventListener("abort", abortListener);
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
  return messages.map((message) => {
    const mode = message.metadata?.turn_mode === "chat" ? "常规对话"
      : message.metadata?.turn_mode === "study" ? "教学回合" : "";
    return `${mode ? `[${mode}] ` : ""}${message.role}: ${message.content}`;
  }).join("\n");
}
