import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const ansi = {
  reset: "\x1b[0m",
  dim: "\x1b[2m",
  cyan: "\x1b[36m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  red: "\x1b[31m",
  bold: "\x1b[1m",
};

export function paint(value: string, color: keyof typeof ansi, enabled: boolean): string {
  return enabled ? `${ansi[color]}${value}${ansi.reset}` : value;
}

export function logo(enabled: boolean): string {
  const mark = ["  ◇ DeepProf", "  ──────────", "  learn · reflect · grow"].join("\n");
  return paint(mark, "cyan", enabled);
}

export function jsonResult(command: string, data: Record<string, unknown> = {}, sessionId: string | null = null): void {
  process.stdout.write(`${JSON.stringify({ ok: true, command, session_id: sessionId, data })}\n`);
}

export function jsonError(command: string, error: unknown): void {
  const candidate = error && typeof error === "object" ? error as { code?: unknown; message?: unknown } : {};
  const code = typeof candidate.code === "string" && candidate.code ? candidate.code : (error instanceof Error ? "cli_error" : "error");
  const message = typeof candidate.message === "string" ? candidate.message : String(error);
  const value = { code, message };
  process.stdout.write(`${JSON.stringify({ ok: false, command, session_id: null, error: value })}\n`);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usageStatus(profile: string | null, model: string | null, usage: Record<string, unknown>): string {
  const total = typeof usage.total_tokens === "number" ? String(usage.total_tokens) : "—";
  let cost = "N/A";
  const pricingFile = process.env.DEEPPROF_PRICING_FILE || join(process.env.DEEPPROF_HOME || join(homedir(), ".deepprof"), "pricing.json");
  if (profile && model && existsSync(pricingFile)) {
    try {
      const raw = JSON.parse(readFileSync(pricingFile, "utf8")) as { profiles?: Record<string, { models?: Record<string, { input_per_million?: number; output_per_million?: number }> }> };
      const rates = raw.profiles?.[profile]?.models?.[model];
      if (rates && typeof usage.prompt_tokens === "number" && typeof usage.completion_tokens === "number" && typeof rates.input_per_million === "number" && typeof rates.output_per_million === "number") {
        cost = String((usage.prompt_tokens * rates.input_per_million + usage.completion_tokens * rates.output_per_million) / 1_000_000);
      }
    } catch { /* missing or invalid pricing is explicitly shown as N/A */ }
  }
  return `${profile || "unknown"}/${model || "unknown"} · ${total} tokens · cost ${cost}`;
}
