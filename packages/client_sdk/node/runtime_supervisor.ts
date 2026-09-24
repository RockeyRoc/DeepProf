import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { gatewayHealthy, isLoopbackUrl, readGateway, removeGateway, writeGateway } from "./gateway_discovery.js";
import { loadRuntimeSecrets } from "./dpapi_secret_store.js";

export type RuntimeState = "STARTING" | "READY" | "UNHEALTHY" | "STOPPED";
export interface RuntimeStatus { state: RuntimeState; host: "127.0.0.1"; port: number | null; baseUrl: string | null; owner: boolean; }

async function freePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolvePort(port));
    });
  });
}

export interface RuntimeSupervisorOptions {
  owner: "cli";
  runtimeRoot?: string;
  python?: string;
  apiUrl?: string;
}

export class RuntimeSupervisor {
  private process: ChildProcess | null = null;
  private ownerToken = "";
  private current: RuntimeStatus = { state: "STOPPED", host: "127.0.0.1", port: null, baseUrl: null, owner: false };

  constructor(private readonly options: RuntimeSupervisorOptions) {}

  get status(): RuntimeStatus { return { ...this.current }; }

  async start(): Promise<RuntimeStatus> {
    if (this.current.state === "READY") return this.status;
    const explicit = this.options.apiUrl || process.env.DEEPPROF_API_URL || "";
    if (explicit) {
      if (!isLoopbackUrl(explicit) || !(await gatewayHealthy(explicit))) throw new Error("configured_runtime_unavailable");
      this.current = this.statusFor(explicit, false);
      return this.status;
    }
    const discovered = readGateway();
    if (discovered && await gatewayHealthy(discovered.base_url)) {
      this.current = this.statusFor(discovered.base_url, false);
      return this.status;
    }
    const port = await freePort();
    const baseUrl = `http://127.0.0.1:${port}`;
    this.ownerToken = randomUUID();
    this.current = { state: "STARTING", host: "127.0.0.1", port, baseUrl, owner: true };
    const environment = { ...process.env, ...loadRuntimeSecrets(), DEEPPROF_API_HOST: "127.0.0.1", DEEPPROF_API_PORT: String(port) };
    this.process = spawn(
      this.options.python || process.env.DEEPPROF_PYTHON || "python",
      ["-m", "uvicorn", "api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", String(port)],
      { cwd: this.options.runtimeRoot || resolve(process.cwd(), "../.."), env: environment, stdio: ["ignore", "ignore", "ignore"] },
    );
    this.process.once("exit", () => {
      this.process = null;
      if (this.current.state !== "STOPPED") this.current = { ...this.current, state: "UNHEALTHY" };
    });
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (await gatewayHealthy(baseUrl)) {
        writeGateway({ protocol_version: "1", base_url: baseUrl, pid: this.process?.pid || process.pid, owner: this.options.owner, started_at: new Date().toISOString(), owner_token: this.ownerToken });
        this.current = { ...this.current, state: "READY" };
        return this.status;
      }
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
    }
    this.current = { ...this.current, state: "UNHEALTHY" };
    this.stop();
    throw new Error("runtime_health_timeout");
  }

  stop(): void {
    if (this.process && !this.process.killed) this.process.kill();
    this.process = null;
    if (this.ownerToken) removeGateway(this.ownerToken);
    this.ownerToken = "";
    this.current = { state: "STOPPED", host: "127.0.0.1", port: null, baseUrl: null, owner: false };
  }

  private statusFor(baseUrl: string, owner: boolean): RuntimeStatus {
    const parsed = new URL(baseUrl);
    return { state: "READY", host: "127.0.0.1", port: Number(parsed.port) || null, baseUrl: baseUrl.replace(/\/$/, ""), owner };
  }
}
