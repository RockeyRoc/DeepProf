import { app, safeStorage } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { DpapiSecretStore } from "../../../packages/client_sdk/node/dpapi_secret_store";

/** Main 进程专用的加密凭据存储；Renderer 只能调用 save，不会得到 secret。 */
export class SecretStore {
  private readonly file: string;
  private readonly shared: DpapiSecretStore | null;
  private values: Record<string, string> = {};

  constructor() {
    this.file = join(app.getPath("userData"), "runtime", "secrets.json");
    this.shared = process.platform === "win32" ? new DpapiSecretStore() : null;
    mkdirSync(dirname(this.file), { recursive: true });
    this.load();
  }

  private load(): void {
    if (!existsSync(this.file)) return;
    try {
      this.values = JSON.parse(readFileSync(this.file, "utf8")) as Record<string, string>;
    } catch {
      this.values = {};
    }
  }

  set(ref: string, value: string): void {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("system_secret_store_unavailable");
    this.values[ref] = safeStorage.encryptString(value).toString("base64");
    writeFileSync(this.file, JSON.stringify(this.values), { encoding: "utf8", mode: 0o600 });
    try { this.shared?.set(ref, value); } catch { /* legacy Electron storage remains available */ }
  }

  get(ref: string): string | null {
    const shared = this.shared?.get(ref);
    if (shared) return shared;
    const encoded = this.values[ref];
    if (!encoded || !safeStorage.isEncryptionAvailable()) return null;
    try {
      const value = safeStorage.decryptString(Buffer.from(encoded, "base64"));
      try { this.shared?.set(ref, value); } catch { /* best-effort migration */ }
      return value;
    } catch {
      return null;
    }
  }

  has(ref: string): boolean {
    return Boolean(this.shared?.has(ref) || this.values[ref]);
  }

  remove(ref: string): void {
    delete this.values[ref];
    writeFileSync(this.file, JSON.stringify(this.values), { encoding: "utf8", mode: 0o600 });
  }

}
