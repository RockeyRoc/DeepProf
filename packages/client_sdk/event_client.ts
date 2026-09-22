import type { RuntimeEvent } from "./types.js";

const EVENT_TYPES = [
  "session.started", "session.resumed", "session.compacted", "session.ended",
  "agent.started", "agent.turn.started", "agent.turn.completed", "agent.failed",
  "model.requested", "model.stream.delta", "model.completed", "model.failed",
  "tool.requested", "tool.approved", "tool.started", "tool.completed", "tool.failed",
  "memory.read", "memory.write", "pedagogy.node.entered", "pedagogy.decision",
  "pedagogy.node.exited", "pedagogy.attempt", "quiz.completed", "insufficient_evidence",
];

export interface EventClientOptions {
  baseUrl: string;
  onEvent: (event: RuntimeEvent) => void;
  onError?: (error: Error) => void;
}

/** Browser EventSource + Node fetch SSE client with one cursor per session. */
export class EventClient {
  private source: EventSource | null = null;
  private controller: AbortController | null = null;
  private readonly cursors = new Map<string, number>();
  private activeSession = "";
  private closed = true;

  constructor(private readonly options: EventClientOptions) {}

  get lastSequence(): number {
    return this.activeSession ? (this.cursors.get(this.activeSession) || 0) : 0;
  }

  connect(sessionId: string, fromSequence?: number): void {
    this.close();
    this.closed = false;
    this.activeSession = sessionId;
    if (fromSequence !== undefined) {
      this.cursors.set(sessionId, Math.max(this.cursors.get(sessionId) || 0, fromSequence));
    }
    if (typeof globalThis.EventSource === "function") {
      this.connectBrowser(sessionId);
    } else {
      void this.connectNode(sessionId);
    }
  }

  accept(raw: string): void {
    try {
      const event = JSON.parse(raw) as RuntimeEvent;
      if (!event.session_id || event.session_id !== this.activeSession) return;
      const cursor = this.cursors.get(event.session_id) || 0;
      if (event.sequence <= cursor) return;
      this.cursors.set(event.session_id, event.sequence);
      this.options.onEvent(event);
    } catch (error) {
      this.options.onError?.(error instanceof Error ? error : new Error("invalid_event"));
    }
  }

  close(): void {
    this.closed = true;
    this.source?.close();
    this.source = null;
    this.controller?.abort();
    this.controller = null;
  }

  abort(): void {
    this.close();
  }

  private connectBrowser(sessionId: string): void {
    const url = new URL(`/sessions/${encodeURIComponent(sessionId)}/events`, this.options.baseUrl);
    url.searchParams.set("from_sequence", String(this.cursors.get(sessionId) || 0));
    this.source = new EventSource(url);
    for (const eventType of EVENT_TYPES) {
      this.source.addEventListener(eventType, (event) => this.accept((event as MessageEvent).data));
    }
    this.source.onmessage = (message) => this.accept(message.data);
    this.source.onerror = () => this.options.onError?.(new Error("event_stream_unavailable"));
  }

  private async connectNode(sessionId: string): Promise<void> {
    let attempts = 0;
    while (!this.closed && attempts < 5) {
      this.controller = new AbortController();
      try {
        const url = new URL(`/sessions/${encodeURIComponent(sessionId)}/events`, this.options.baseUrl);
        url.searchParams.set("from_sequence", String(this.cursors.get(sessionId) || 0));
        const response = await fetch(url, { signal: this.controller.signal, headers: { accept: "text/event-stream" } });
        if (!response.ok || !response.body) throw new Error(`event_stream:${response.status}`);
        await this.readSse(response.body);
        if (!this.closed) {
          attempts += 1;
          if (attempts >= 5) {
            this.options.onError?.(new Error("event_stream_closed"));
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, Math.min(1000, attempts * 200)));
        }
      } catch (error) {
        if (this.closed) return;
        attempts += 1;
        if (attempts >= 5) {
          this.options.onError?.(error instanceof Error ? error : new Error("event_stream_unavailable"));
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, Math.min(1000, attempts * 200)));
      }
    }
  }

  private async readSse(body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (!this.closed) {
        const next = await reader.read();
        if (next.done) return;
        buffer += decoder.decode(next.value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || "";
        for (const frame of frames) {
          const data = frame.split(/\r?\n/).find((line) => line.startsWith("data:"));
          if (data) this.accept(data.slice(5).trim());
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
}
