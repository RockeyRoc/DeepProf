import { dirname, resolve } from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { RuntimeSupervisor } from "../../../packages/client_sdk/node/runtime_supervisor.js";

export function createRuntime(apiUrl?: string): RuntimeSupervisor {
  const runtimeRoot = process.env.DEEPPROF_RUNTIME_ROOT || findRuntimeRoot();
  return new RuntimeSupervisor({
    owner: "cli",
    apiUrl,
    runtimeRoot,
  });
}

function findRuntimeRoot(): string {
  const moduleDir = dirname(fileURLToPath(import.meta.url));
  for (const seed of [process.cwd(), moduleDir]) {
    let current = resolve(seed);
    for (let attempt = 0; attempt < 8; attempt += 1) {
      if (existsSync(resolve(current, "api", "app.py"))) return current;
      const parent = resolve(current, "..");
      if (parent === current) break;
      current = parent;
    }
  }
  return resolve(process.cwd(), "../..");
}
