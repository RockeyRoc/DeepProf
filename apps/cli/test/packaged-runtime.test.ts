import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { environmentPythonPath, environmentRequirementsHash, findSystemPython, preparePythonEnvironment,
  pythonEnvironmentPath, pythonInstallStatePath, resolveRuntimePaths } from "../src/packaged_runtime.js";

test("runtime discovery supports both a source checkout and an installed runtime bundle", () => {
  const root = mkdtempSync(join(tmpdir(), "deepprof-runtime-"));
  try {
    mkdirSync(join(root, "apps", "cli", "dist", "apps", "cli", "src"), { recursive: true });
    writeFileSync(join(root, "apps", "cli", "package.json"), "{}");
    mkdirSync(join(root, "api"), { recursive: true });
    writeFileSync(join(root, "api", "app.py"), "");
    mkdirSync(join(root, "runtime"), { recursive: true });
    writeFileSync(join(root, "runtime", "requirements-runtime.txt"), "# source requirements\n");
    const source = resolveRuntimePaths(join(root, "apps", "cli", "dist", "apps", "cli", "src"), root);
    assert.equal(source.runtimeRoot, root);

    const configuredRuntimeRoot = process.env.DEEPPROF_RUNTIME_ROOT;
    process.env.DEEPPROF_RUNTIME_ROOT = root;
    try {
      const configured = resolveRuntimePaths(join(root, "apps", "cli", "dist", "apps", "cli", "src"), tmpdir());
      assert.equal(configured.runtimeRoot, root);
      assert.equal(configured.packageRoot, root);
      assert.doesNotThrow(() => environmentRequirementsHash(configured.packageRoot, false));
    } finally {
      if (configuredRuntimeRoot === undefined) delete process.env.DEEPPROF_RUNTIME_ROOT;
      else process.env.DEEPPROF_RUNTIME_ROOT = configuredRuntimeRoot;
    }

    rmSync(join(root, "api"), { recursive: true, force: true });
    mkdirSync(join(root, "runtime", "api"), { recursive: true });
    writeFileSync(join(root, "runtime", "api", "app.py"), "");
    const installed = resolveRuntimePaths(join(root, "apps", "cli", "dist", "apps", "cli", "src"), tmpdir());
    assert.equal(installed.packageRoot, root);
    assert.equal(installed.runtimeRoot, join(root, "runtime"));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("Python discovery requires a supported system interpreter", () => {
  const configured = process.env.DEEPPROF_PYTHON;
  process.env.DEEPPROF_PYTHON = process.execPath;
  try { assert.throws(() => findSystemPython(), /Python 3\.12/); }
  finally {
    if (configured === undefined) delete process.env.DEEPPROF_PYTHON;
    else process.env.DEEPPROF_PYTHON = configured;
  }
});

test("interrupted dependency installation can be retried without recreating the Python environment", () => {
  const root = mkdtempSync(join(tmpdir(), "DeepProf 安装 重试 "));
  const runtimeRequirements = join(root, "runtime", "requirements-runtime.txt");
  const home = join(root, "用户 数据");
  mkdirSync(join(root, "runtime"), { recursive: true });
  writeFileSync(runtimeRequirements, "fastapi==0.1\n");
  const commands: string[] = [];
  let installAttempts = 0;
  const dependencies = {
    findPython: () => ({ executable: "python", prefixArgs: [], version: "3.12.0" }),
    runCommand: (_command: string, args: string[]) => {
      commands.push(args.join(" "));
      if (args[0] === "-m" && args[1] === "venv") {
        const executable = environmentPythonPath(args[2]);
        mkdirSync(join(args[2], "Scripts"), { recursive: true });
        writeFileSync(executable, "fake interpreter");
        return { status: 0 };
      }
      installAttempts += 1;
      return { status: installAttempts === 1 ? 1 : 0 };
    },
  };
  const options = { paths: { packageRoot: root, runtimeRoot: root }, home, dependencies };
  try {
    assert.throws(() => preparePythonEnvironment(options), /安装 DeepProf 依赖失败/);
    assert.equal(existsSync(pythonInstallStatePath(pythonEnvironmentPath(home))), false);

    const python = preparePythonEnvironment(options);

    assert.equal(python, environmentPythonPath(pythonEnvironmentPath(home)));
    assert.equal(commands.filter((command) => command.includes("-m venv")).length, 1);
    assert.equal(installAttempts, 2);
    assert.equal(existsSync(pythonInstallStatePath(pythonEnvironmentPath(home))), true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
