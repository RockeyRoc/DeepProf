import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface CliState {
  version: 1;
  learner_id: string;
  active_session_id: string | null;
  course_id: string;
  experiment_group: "A" | "B" | "C";
}

function home(): string { return process.env.DEEPPROF_HOME || join(homedir(), ".deepprof"); }
export function stateFile(): string { return join(home(), "cli.json"); }

export function loadState(): CliState {
  if (!existsSync(stateFile())) return { version: 1, learner_id: "local", active_session_id: null, course_id: "ds.c_language.v1", experiment_group: "B" };
  try {
    const value = JSON.parse(readFileSync(stateFile(), "utf8")) as Partial<CliState>;
    return { version: 1, learner_id: String(value.learner_id || "local"), active_session_id: value.active_session_id || null,
      course_id: String(value.course_id || "ds.c_language.v1"),
      experiment_group: value.experiment_group === "A" || value.experiment_group === "C" ? value.experiment_group : "B" };
  } catch {
    return { version: 1, learner_id: "local", active_session_id: null, course_id: "ds.c_language.v1", experiment_group: "B" };
  }
}

export function saveState(state: CliState): void {
  mkdirSync(home(), { recursive: true });
  writeFileSync(stateFile(), JSON.stringify(state, null, 2), { encoding: "utf8", mode: 0o600 });
}
