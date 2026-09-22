import { resolve } from "node:path";
import { RuntimeSupervisor as SharedRuntimeSupervisor, type RuntimeState, type RuntimeStatus } from "../../../packages/client_sdk/node/runtime_supervisor";

export type { RuntimeState, RuntimeStatus };
export const LOOPBACK_HOST = "127.0.0.1";

/** Desktop Main uses the same attach-or-start coordinator as the CLI. */
export class RuntimeSupervisor {
  private readonly inner: SharedRuntimeSupervisor;

  constructor(runtimeRoot = resolve(__dirname, "../../../../")) {
    this.inner = new SharedRuntimeSupervisor({ owner: "desktop", runtimeRoot });
  }

  get status(): RuntimeStatus { return this.inner.status; }

  async start(): Promise<RuntimeStatus> { return this.inner.start(); }

  stop(): void { this.inner.stop(); }
}
