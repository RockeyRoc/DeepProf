import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const CLI_VERSION = "0.6.2";
const moduleDirectory = dirname(fileURLToPath(import.meta.url));

export interface RuntimePaths {
  packageRoot: string;
  runtimeRoot: string;
}

export interface PythonCommand {
  executable: string;
  prefixArgs: string[];
  version: string;
}

interface InstallState {
  runtimeHash: string;
  ocrHash?: string;
}

export function resolveRuntimePaths(moduleDir = moduleDirectory, cwd = process.cwd()): RuntimePaths {
  const configured = process.env.DEEPPROF_RUNTIME_ROOT;
  if (configured) {
    const runtimeRoot = resolve(configured);
    if (existsSync(join(runtimeRoot, "api", "app.py"))) {
      return { runtimeRoot, packageRoot: findPackageRoot(moduleDir, runtimeRoot) };
    }
    throw new Error(`配置的 Gateway 源码目录不完整：${runtimeRoot}`);
  }

  const seeds = [moduleDir, cwd];
  for (const seed of seeds) {
    let current = resolve(seed);
    for (let depth = 0; depth < 10; depth += 1) {
      if (existsSync(join(current, "api", "app.py"))) {
        return { runtimeRoot: current, packageRoot: current };
      }
      const bundled = join(current, "runtime");
      if (existsSync(join(bundled, "api", "app.py"))) {
        return { runtimeRoot: bundled, packageRoot: current };
      }
      const parent = dirname(current);
      if (parent === current) break;
      current = parent;
    }
  }
  throw new Error("DeepProf Runtime 文件缺失；请从 README 快速开始重新安装。");
}

function findPackageRoot(moduleDir: string, runtimeRoot: string): string {
  if (existsSync(join(runtimeRoot, "package.json"))) return runtimeRoot;
  let current = resolve(moduleDir);
  for (let depth = 0; depth < 10; depth += 1) {
    if (existsSync(join(current, "package.json"))) return current;
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return dirname(runtimeRoot);
}

export function findSystemPython(): PythonCommand {
  const configured = process.env.DEEPPROF_PYTHON;
  const candidates = configured
    ? [{ executable: configured, prefixArgs: [] as string[] }]
    : process.platform === "win32"
      ? [
          { executable: "python", prefixArgs: [] as string[] },
          { executable: "py", prefixArgs: ["-3"] },
        ]
      : [
          { executable: "python3", prefixArgs: [] as string[] },
          { executable: "python", prefixArgs: [] as string[] },
        ];

  for (const candidate of candidates) {
    const result = spawnSync(candidate.executable, [...candidate.prefixArgs, "--version"], { encoding: "utf8", windowsHide: true });
    const output = `${result.stdout || ""} ${result.stderr || ""}`;
    const match = output.match(/Python\s+(\d+\.\d+\.\d+)/);
    if (!match || result.status !== 0) continue;
    const [major, minor] = match[1].split(".").map(Number);
    if (major > 3 || (major === 3 && minor >= 12)) return { ...candidate, version: match[1] };
    if (configured) throw new Error(`需要 Python 3.12 或更新版本；当前版本为 ${match[1]}。安装 Python 3.12+ 后重试。`);
  }
  throw new Error("首次运行需要 Python 3.12 或更新版本。请安装 Python 3.12+，或设置 DEEPPROF_PYTHON 指定解释器后运行 deepprof setup。");
}

export function pythonEnvironmentPath(home = process.env.DEEPPROF_HOME || join(homedir(), ".deepprof")): string {
  return join(resolve(home), "runtime", CLI_VERSION, "venv");
}

export function environmentPythonPath(venvPath = pythonEnvironmentPath()): string {
  return process.platform === "win32"
    ? join(venvPath, "Scripts", "python.exe")
    : join(venvPath, "bin", "python");
}

export function environmentRequirementsHash(packageRoot: string, withOcr: boolean): string {
  const digest = createHash("sha256");
  digest.update(readFileSync(join(packageRoot, "runtime", "requirements-runtime.txt")));
  digest.update("\u0000");
  if (withOcr) digest.update(readFileSync(join(packageRoot, "runtime", "requirements-ocr.txt")));
  return digest.digest("hex");
}

export function pythonInstallStatePath(venvPath: string): string {
  return join(dirname(venvPath), "install-state.json");
}

function pauseSync(milliseconds: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

function acquireSetupLock(venvPath: string): () => void {
  const lockDirectory = join(dirname(venvPath), "setup.lock");
  const ownerFile = join(lockDirectory, "owner.json");
  const startedWaiting = Date.now();
  mkdirSync(dirname(lockDirectory), { recursive: true });
  while (true) {
    try {
      mkdirSync(lockDirectory);
      writeFileSync(ownerFile, JSON.stringify({ pid: process.pid, createdAt: Date.now() }), { encoding: "utf8", mode: 0o600 });
      return () => rmSync(lockDirectory, { recursive: true, force: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
        rmSync(lockDirectory, { recursive: true, force: true });
        throw error;
      }
    }

    let stale = false;
    try {
      const owner = JSON.parse(readFileSync(ownerFile, "utf8")) as { pid?: number; createdAt?: number };
      stale = typeof owner.pid === "number" && typeof owner.createdAt === "number"
        && Date.now() - owner.createdAt > 30 * 60_000 && !processIsAlive(owner.pid);
    } catch {
      try { stale = Date.now() - statSync(lockDirectory).mtimeMs > 60_000; } catch { stale = true; }
    }
    if (stale) {
      rmSync(lockDirectory, { recursive: true, force: true });
      continue;
    }
    if (Date.now() - startedWaiting > 10 * 60_000) {
      throw new Error("另一个 DeepProf 进程仍在准备运行环境。请稍后重试 deepprof setup。");
    }
    pauseSync(250);
  }
}

export function preparePythonEnvironment(options: {
  paths: RuntimePaths;
  withOcr?: boolean;
  onProgress?: (message: string) => void;
  home?: string;
}): string {
  const withOcr = options.withOcr === true;
  const python = findSystemPython();
  const venvPath = pythonEnvironmentPath(options.home);
  const venvPython = environmentPythonPath(venvPath);
  const statePath = pythonInstallStatePath(venvPath);
  const runtimeHash = environmentRequirementsHash(options.paths.packageRoot, false);
  const ocrHash = withOcr ? environmentRequirementsHash(options.paths.packageRoot, true) : undefined;

  const fail = (label: string, result: ReturnType<typeof spawnSync>): never => {
    if (result.error) throw new Error(`${label}：${result.error.message}`);
    if (result.status !== 0) throw new Error(`${label}（退出码 ${String(result.status)}）。网络不可用时可稍后重试，环境目录为 ${dirname(venvPath)}。`);
    throw new Error(`${label}：未知错误`);
  };

  mkdirSync(dirname(venvPath), { recursive: true });
  const releaseLock = acquireSetupLock(venvPath);
  try {
    let previous: InstallState | undefined;
    try { previous = JSON.parse(readFileSync(statePath, "utf8")) as InstallState; } catch { /* first run or interrupted preparation */ }

    if (previous?.runtimeHash === runtimeHash && existsSync(venvPython) && (!withOcr || previous.ocrHash === ocrHash)) {
      return venvPython;
    }

    options.onProgress?.(`正在为 DeepProf 准备 Python ${python.version} 环境…`);
    if (!existsSync(venvPython)) {
      const venv = spawnSync(python.executable, [...python.prefixArgs, "-m", "venv", venvPath], { stdio: "inherit", windowsHide: true });
      if (venv.status !== 0) fail("创建 Python 环境失败", venv);
    }

    options.onProgress?.("正在安装 DeepProf 的固定 Python 依赖（首次运行需要网络）…");
    const requirements = join(options.paths.packageRoot, "runtime", "requirements-runtime.txt");
    const runtimeInstall = spawnSync(venvPython, ["-m", "pip", "install", "--disable-pip-version-check", "-r", requirements], { stdio: "inherit", windowsHide: true });
    if (runtimeInstall.status !== 0) fail("安装 DeepProf 依赖失败", runtimeInstall);
    if (withOcr) {
      const ocrInstall = spawnSync(venvPython, ["-m", "pip", "install", "--disable-pip-version-check", "-r", join(options.paths.packageRoot, "runtime", "requirements-ocr.txt")], { stdio: "inherit", windowsHide: true });
      if (ocrInstall.status !== 0) fail("安装 OCR 可选依赖失败", ocrInstall);
    } else if (previous?.ocrHash && previous.runtimeHash !== runtimeHash) {
      options.onProgress?.("已有环境包含的可选 OCR 包将保留。");
    }

    const state: InstallState = {
      runtimeHash,
      ...(withOcr && ocrHash ? { ocrHash } : previous?.ocrHash ? { ocrHash: previous.ocrHash } : {}),
    };
    const temporary = `${statePath}.${process.pid}.tmp`;
    writeFileSync(temporary, JSON.stringify(state, null, 2), { encoding: "utf8", mode: 0o600 });
    renameSync(temporary, statePath);
    return venvPython;
  } finally {
    releaseLock();
  }
}

export function isDeveloperRuntime(runtimeRoot: string): boolean {
  return existsSync(join(runtimeRoot, "docs", "experiments", "source-data", "experiment-snapshot.json"))
    && existsSync(join(runtimeRoot, "scripts", "build_experiment_report.py"));
}
