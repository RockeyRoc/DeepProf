import { parseResponse } from "./command_bus.js";
import type { CourseSummary } from "./types.js";

export class CourseClient {
  constructor(private readonly baseUrl: string) {}

  async list(): Promise<CourseSummary[]> {
    const response = await fetch(`${this.baseUrl}/courses`);
    return parseResponse<CourseSummary[]>(response, "courses_failed");
  }

  async import(courseId: string): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/courses/${encodeURIComponent(courseId)}/import`, { method: "POST" });
    return parseResponse<Record<string, unknown>>(response, "course_import_failed");
  }

  async replay(sessionId: string): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/replay/sessions/${encodeURIComponent(sessionId)}`);
    return parseResponse<Record<string, unknown>>(response, "replay_failed");
  }
}
