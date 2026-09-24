import { CommandBus } from "./command_bus.js";
import { parseResponse } from "./command_bus.js";
import type { CommandAccepted, SessionMessage, SessionSummary } from "./types.js";

export class SessionClient {
  constructor(
    private readonly baseUrl: string,
    private readonly commands: CommandBus,
  ) {}

  async list(learnerId = "local"): Promise<SessionSummary[]> {
    const response = await fetch(`${this.baseUrl}/sessions?learner_id=${encodeURIComponent(learnerId)}`);
    if (!response.ok) throw new Error(`sessions_failed:${response.status}`);
    return response.json() as Promise<SessionSummary[]>;
  }

  async create(title = "学习会话", group = "B", courseId = "ds.c_language.v1", sessionMode: "chat" | "study" = "study", experimentRun = false): Promise<string> {
    const payload: Record<string, unknown> = { title, session_mode: sessionMode };
    if (sessionMode === "study") Object.assign(payload, { group, course_id: courseId, experiment_run: experimentRun });
    const accepted = await this.commands.send(this.commands.create("session.new", payload));
    if (!accepted.session_id) throw new Error("session_not_created");
    return accepted.session_id;
  }

  async get(sessionId: string): Promise<SessionSummary> {
    const response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`);
    return parseResponse<SessionSummary>(response, "session_failed");
  }

  async details(sessionId: string): Promise<SessionSummary> {
    return this.get(sessionId);
  }

  async transcript(sessionId: string, limit = 200): Promise<SessionMessage[]> {
    const response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}`);
    return parseResponse<SessionMessage[]>(response, "transcript_failed");
  }

  async send(sessionId: string, content: string, requestedAction = ""): Promise<CommandAccepted> {
    return this.commands.send(this.commands.create("message.send", { content, ...(requestedAction ? { requested_action: requestedAction } : {}) }, sessionId));
  }

  async learner(sessionId: string, conceptId = ""): Promise<Record<string, unknown>> {
    const query = conceptId ? `?concept_id=${encodeURIComponent(conceptId)}` : "";
    const response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/learner${query}`);
    return parseResponse<Record<string, unknown>>(response, "learner_estimates_failed");
  }

  async resume(sessionId: string): Promise<CommandAccepted> {
    return this.commands.send(this.commands.create("session.resume", {}, sessionId));
  }

  async fork(sessionId: string, title?: string): Promise<string> {
    const accepted = await this.commands.send(this.commands.create("session.fork", title ? { title } : {}, sessionId));
    if (!accepted.session_id) throw new Error("session_not_forked");
    return accepted.session_id;
  }

  async compact(sessionId: string, keep = 60): Promise<CommandAccepted> {
    return this.commands.send(this.commands.create("session.compact", { keep }, sessionId));
  }
}
