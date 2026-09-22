import type { RuntimeEvent } from "./types.js";

export interface ProjectedState {
  lastSequence: number;
  status: "idle" | "running" | "waiting" | "review" | "failed";
  activeAction: string | null;
  answer: string;
  events: RuntimeEvent[];
}

export const initialProjectedState: ProjectedState = {
  lastSequence: 0,
  status: "idle",
  activeAction: null,
  answer: "",
  events: [],
};

export function projectEvent(state: ProjectedState, event: RuntimeEvent): ProjectedState {
  if (event.sequence <= state.lastSequence) return state;

  let next: ProjectedState = {
    ...state,
    lastSequence: event.sequence,
    events: [...state.events, event].slice(-200),
  };

  switch (event.type) {
    case "agent.started":
      next = { ...next, status: "running", answer: "" };
      break;
    case "agent.turn.started":
    case "model.requested":
    case "tool.started":
      next = { ...next, status: "running" };
      break;
    case "tool.requested":
      next = { ...next, status: "waiting" };
      break;
    case "model.stream.delta": {
      const text = String(event.payload.text ?? "");
      next = { ...next, status: "running", answer: `${next.answer}${text}` };
      break;
    }
    case "pedagogy.decision":
      next = { ...next, activeAction: String(event.payload.action ?? "") || null };
      break;
    case "model.completed":
    case "pedagogy.node.exited":
      next = { ...next, status: "review" };
      break;
    case "insufficient_evidence":
    case "model.failed":
    case "agent.failed":
    case "tool.failed":
      next = { ...next, status: "failed" };
      break;
    default:
      break;
  }
  return next;
}
