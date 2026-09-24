import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { findSystemPython, resolveRuntimePaths } from "../src/packaged_runtime.js";

test("runtime discovery supports both a source checkout and an installed runtime bundle", () => {
  const root = mkdtempSync(join(tmpdir(), "deepprof-runtime-"));
  try {
    mkdirSync(join(root, "apps", "cli", "dist", "apps", "cli", "src"), { recursive: true });
    mkdirSync(join(root, "api"), { recursive: true });
    writeFileSync(join(root, "api", "app.py"), "");
    const source = resolveRuntimePaths(join(root, "apps", "cli", "dist", "apps", "cli", "src"), root);
    assert.equal(source.runtimeRoot, root);

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
