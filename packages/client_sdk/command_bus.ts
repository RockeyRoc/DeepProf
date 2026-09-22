import type { ClientCommand, CommandAccepted, Surface } from "./types.js";

export class GatewayError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "gateway_error",
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "GatewayError";
  }
}

function id(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

export class CommandBus {
  constructor(
    private readonly baseUrl: string,
    private readonly clientId: string,
    private readonly surface: Surface,
    private readonly learnerId = "local",
  ) {}

  create(
    type: string,
    payload: Record<string, unknown> = {},
    sessionId: string | null = null,
  ): ClientCommand {
    return {
      command_id: id("cmd"),
      client_id: this.clientId,
      surface: this.surface,
      session_id: sessionId,
      learner_id: this.learnerId,
      type,
      payload,
      timestamp: new Date().toISOString(),
    };
  }

  async send(command: ClientCommand): Promise<CommandAccepted> {
    const response = await fetch(`${this.baseUrl}/commands`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(command),
    });
    return parseResponse<CommandAccepted>(response, "command_failed");
  }
}

export async function parseResponse<T>(response: Response, fallbackCode: string): Promise<T> {
  const raw = await response.text();
  let payload: Record<string, unknown> = {};
  try {
    payload = raw ? JSON.parse(raw) as Record<string, unknown> : {};
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = (payload.error && typeof payload.error === "object")
      ? payload.error as Record<string, unknown>
      : {};
    const detail = typeof payload.detail === "string" ? payload.detail : "";
    throw new GatewayError(
      String(error.message || detail || `${fallbackCode}:${response.status}`),
      response.status,
      String(error.code || fallbackCode),
      (error.details && typeof error.details === "object") ? error.details as Record<string, unknown> : {},
    );
  }
  return payload as T;
}
