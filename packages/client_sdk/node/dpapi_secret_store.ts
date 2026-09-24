import { homedir } from "node:os";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";

export interface SecretStoreLike {
  get(ref: string): string | null;
  set(ref: string, value: string): void;
  has(ref: string): boolean;
}

function dataHome(): string {
  return process.env.DEEPPROF_HOME || join(homedir(), ".deepprof");
}

function protect(value: string, decrypt = false): string {
  if (process.platform !== "win32") throw new Error("dpapi_unavailable");
  // Windows PowerShell 5.1 does not load the ProtectedData assembly by
  // default. STA keeps the operation on a normal single-threaded user context.
  const script = decrypt
    ? "Add-Type -AssemblyName System.Security;$raw=[Console]::In.ReadToEnd();$bytes=[Convert]::FromBase64String($raw);$plain=[Security.Cryptography.ProtectedData]::Unprotect($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);[Console]::Out.Write([Text.Encoding]::UTF8.GetString($plain))"
    : "Add-Type -AssemblyName System.Security;$raw=[Console]::In.ReadToEnd();$bytes=[Text.Encoding]::UTF8.GetBytes($raw);$protected=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);[Console]::Out.Write([Convert]::ToBase64String($protected))";
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-STA", "-Command", script], {
    input: value,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0 || !result.stdout) {
    const diagnostic = `${result.stderr || ""}\n${result.stdout || ""}`.toLowerCase();
    if (diagnostic.includes("user profile") || diagnostic.includes("profile loaded")
      || diagnostic.includes("cryptographicexception") || diagnostic.includes("protecteddata")) {
      throw new Error("dpapi_user_profile_unavailable; run deepprof from a normal Windows user session");
    }
    throw new Error("dpapi_operation_failed");
  }
  return result.stdout.trim();
}

/** Current-user encrypted secrets shared by CLI and Desktop Main on Windows. */
export class DpapiSecretStore implements SecretStoreLike {
  private readonly file: string;
  private values: Record<string, string> = {};

  constructor(file = join(dataHome(), "credentials", "dpapi.json")) {
    this.file = file;
    this.load();
  }

  get(ref: string): string | null {
    const encoded = this.values[ref];
    if (!encoded) return null;
    try {
      return protect(encoded, true);
    } catch {
      return null;
    }
  }

  set(ref: string, value: string): void {
    if (!ref || !value) throw new Error("invalid_secret");
    this.values[ref] = protect(value);
    this.persist();
  }

  has(ref: string): boolean {
    return Boolean(this.values[ref]);
  }

  private load(): void {
    if (!existsSync(this.file)) return;
    try {
      const raw = JSON.parse(readFileSync(this.file, "utf8"));
      if (raw && typeof raw === "object") this.values = raw as Record<string, string>;
    } catch {
      this.values = {};
    }
  }

  private persist(): void {
    mkdirSync(dirname(this.file), { recursive: true });
    const temporary = `${this.file}.${process.pid}.tmp`;
    writeFileSync(temporary, JSON.stringify(this.values, null, 2), { encoding: "utf8", mode: 0o600 });
    renameSync(temporary, this.file);
  }
}

export function secretEnvironmentName(ref: string): string {
  const cleaned = ref.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
  return `DEEPPROF_SECRET_${cleaned}`;
}

export function loadRuntimeSecrets(): Record<string, string> {
  const result: Record<string, string> = {};
  const home = dataHome();
  const providersPath = join(home, "providers.json");
  if (!existsSync(providersPath)) return result;
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(providersPath, "utf8"));
  } catch {
    return result;
  }
  const profiles = raw && typeof raw === "object" && "profiles" in raw ? (raw as { profiles?: unknown }).profiles : raw;
  const store = process.platform === "win32" ? new DpapiSecretStore() : null;
  if (!Array.isArray(profiles)) return result;
  for (const item of profiles) {
    if (!item || typeof item !== "object") continue;
    const ref = typeof (item as { api_key_ref?: unknown }).api_key_ref === "string" ? (item as { api_key_ref: string }).api_key_ref : "";
    if (!ref) continue;
    const value = store?.get(ref) || process.env[secretEnvironmentName(ref)];
    if (value) result[secretEnvironmentName(ref)] = value;
  }
  return result;
}
