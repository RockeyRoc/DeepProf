import { existsSync, readFileSync, mkdirSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export interface GatewayRecord {
  protocol_version: "1";
  base_url: string;
  pid: number;
  owner: "cli";
  started_at: string;
  owner_token: string;
}

export function gatewayFile(): string {
  const home = process.env.DEEPPROF_HOME || join(homedir(), ".deepprof");
  return join(home, "gateway.json");
}

export function isLoopbackUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    const hostname = parsed.hostname.replace(/^\[|\]$/g, "");
    return parsed.protocol === "http:" && ["127.0.0.1", "localhost", "::1"].includes(hostname);
  } catch {
    return false;
  }
}

export function readGateway(file = gatewayFile()): GatewayRecord | null {
  if (!existsSync(file)) return null;
  try {
    const value = JSON.parse(readFileSync(file, "utf8")) as GatewayRecord;
    if (value.protocol_version !== "1" || !isLoopbackUrl(value.base_url) || !value.owner_token) return null;
    return value;
  } catch {
    return null;
  }
}

export function writeGateway(record: GatewayRecord, file = gatewayFile()): void {
  if (!isLoopbackUrl(record.base_url)) throw new Error("gateway_must_bind_loopback");
  mkdirSync(dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify(record, null, 2), { encoding: "utf8", mode: 0o600 });
  renameSync(temporary, file);
}

export function removeGateway(ownerToken: string, file = gatewayFile()): void {
  const current = readGateway(file);
  if (current?.owner_token !== ownerToken) return;
  try { unlinkSync(file); } catch { /* stale cleanup is best effort */ }
}

export async function gatewayHealthy(baseUrl: string): Promise<boolean> {
  if (!isLoopbackUrl(baseUrl)) return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 800);
  try {
    const response = await fetch(`${baseUrl.replace(/\/$/, "")}/health`, { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
