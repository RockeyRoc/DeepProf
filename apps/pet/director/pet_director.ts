export const PET_ACTIONS = [
  "idle",
  "running-right",
  "running-left",
  "waving",
  "jumping",
  "failed",
  "waiting",
  "running",
  "review",
] as const;

export type PetAction = (typeof PET_ACTIONS)[number];
export type PetReasonKey =
  | "petWaitingInput"
  | "petSessionReady"
  | "petProcessing"
  | "petWaitingApproval"
  | "petCorrect"
  | "petReview"
  | "petEvidence"
  | "petProblem";

export interface PetEvent {
  type: string;
  sequence: number;
  payload?: Record<string, unknown>;
}

export interface PetState {
  action: PetAction;
  lastSequence: number;
  reason: PetReasonKey;
}

export const initialPetState: PetState = {
  action: "idle",
  lastSequence: 0,
  reason: "petWaitingInput",
};

function state(action: PetAction, event: PetEvent, reason: PetReasonKey): PetState {
  return { action, lastSequence: event.sequence, reason };
}

/** Project RuntimeEvent into a visual state. The pet never calls models, tools, or Memory. */
export function projectPetEvent(current: PetState, event: PetEvent): PetState {
  if (event.sequence <= current.lastSequence) return current;

  switch (event.type) {
    case "session.started":
    case "session.resumed":
      return state("waving", event, "petSessionReady");
    case "agent.started":
    case "agent.turn.started":
    case "model.requested":
    case "model.stream.delta":
    case "tool.started":
      return state("running", event, "petProcessing");
    case "tool.requested":
      return state("waiting", event, "petWaitingApproval");
    case "quiz.completed":
      return state(event.payload?.correct === true ? "jumping" : "review", event,
        event.payload?.correct === true ? "petCorrect" : "petReview");
    case "sources.opened":
    case "review.opened":
    case "model.completed":
    case "tool.completed":
      return state("review", event, "petEvidence");
    case "insufficient_evidence":
    case "model.failed":
    case "agent.failed":
    case "tool.failed":
      return state("failed", event, "petProblem");
    case "session.ended":
      return state("idle", event, "petWaitingInput");
    default:
      return { ...current, lastSequence: event.sequence };
  }
}

export class PetDirector {
  private currentState: PetState = initialPetState;

  get state(): PetState {
    return this.currentState;
  }

  accept(event: PetEvent): PetState {
    this.currentState = projectPetEvent(this.currentState, event);
    return this.currentState;
  }

  reset(): PetState {
    this.currentState = initialPetState;
    return this.currentState;
  }
}
