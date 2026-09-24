import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(appRoot, "../..");
const releaseRoot = join(root, ".release");
const stage = join(releaseRoot, "deepprof-cli");
const sourceManifest = JSON.parse(readFileSync(join(appRoot, "package.json"), "utf8"));
const pythonDirectories = [
  "api", "apps/replay", "config", "data/courses", "graph",
  "library", "models", "packages/contracts", "runtime", "skills", "tools",
];

function within(parent, target) {
  const path = relative(resolve(parent), resolve(target));
  return path !== "" && path !== ".." && !path.startsWith(`..${sep}`) && !isAbsolute(path);
}

function copyDirectory(source, destination) {
  cpSync(source, destination, {
    recursive: true,
    filter(path) {
      const name = path.split(/[\\/]/).at(-1) || "";
      return name !== "__pycache__" && name !== "node_modules" && name !== ".pytest_cache"
        && name !== ".env" && !name.endsWith(".pyc") && !/\.(db|sqlite|sqlite3)(-wal|-shm)?$/i.test(name);
    },
  });
}

if (!existsSync(join(appRoot, "dist/apps/cli/src/index.js"))) throw new Error("CLI build is missing. Run npm run build first.");
for (const path of pythonDirectories) {
  if (!existsSync(join(root, path))) throw new Error(`Runtime source is missing: ${path}`);
}
mkdirSync(releaseRoot, { recursive: true });
const parent = resolve(releaseRoot);
if (!within(parent, stage)) throw new Error("Refusing to prepare an npm package outside .release.");
rmSync(stage, { recursive: true, force: true });
mkdirSync(stage, { recursive: true });
cpSync(join(appRoot, "dist"), join(stage, "dist"), { recursive: true });
rmSync(join(stage, "dist/apps/cli/test"), { recursive: true, force: true });
copyDirectory(join(root, "runtime"), join(stage, "runtime", "runtime"));
for (const path of pythonDirectories.filter((item) => item !== "runtime")) {
  const destination = join(stage, "runtime", path);
  mkdirSync(dirname(destination), { recursive: true });
  copyDirectory(join(root, path), destination);
}
const evaluationRuntimeFiles = ["__init__.py", "metrics.py", "question_bank.py", "quiz_service.py"];
mkdirSync(join(stage, "runtime", "evaluation"), { recursive: true });
for (const file of evaluationRuntimeFiles) {
  cpSync(join(root, "evaluation", file), join(stage, "runtime", "evaluation", file));
}
for (const file of ["requirements-ocr.txt"]) cpSync(join(root, file), join(stage, "runtime", file));
mkdirSync(join(stage, "runtime", "scripts"), { recursive: true });
for (const file of ["ocr_question_collection.py"]) {
  cpSync(join(root, "scripts", file), join(stage, "runtime", "scripts", file));
}
cpSync(join(appRoot, "README.md"), join(stage, "README.md"));
cpSync(join(root, "runtime", "requirements-runtime.txt"), join(stage, "runtime", "requirements-runtime.txt"));

for (const forbidden of [
  "runtime/evaluation/dev_cases.json",
  "runtime/evaluation/m2_dev_cases.json",
  "runtime/evaluation/m3_acceptance.py",
  "runtime/docs",
  "runtime/.env",
]) {
  if (existsSync(join(stage, forbidden))) throw new Error(`Refusing to package restricted experiment or credential material: ${forbidden}`);
}
for (const required of ["runtime/api/app.py", "runtime/runtime/core/__init__.py", "runtime/graph/education/builder.py", "runtime/apps/replay/index.html", "runtime/tools/retrieval/search_textbook.py", "runtime/requirements-runtime.txt"]) {
  if (!existsSync(join(stage, required))) throw new Error(`Packaged runtime layout is incomplete: ${required}`);
}

const { private: _private, ...publicFields } = sourceManifest;
const manifest = {
  ...publicFields,
  description: "Local-first adaptive teaching CLI and Python Gateway Runtime",
  bin: { deepprof: "./dist/apps/cli/src/index.js" },
  dependencies: {},
  devDependencies: {},
  engines: { node: ">=22" },
  files: ["dist/**", "runtime/**", "README.md"],
};
writeFileSync(join(stage, "package.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");

const npmCli = process.env.npm_execpath;
if (!npmCli || !existsSync(npmCli)) throw new Error("Run packaging through npm run pack:release so the active npm CLI can be launched safely.");
const packed = spawnSync(process.execPath, [npmCli, "pack", "--pack-destination", releaseRoot], {
  cwd: stage,
  env: { ...process.env, npm_config_cache: join(releaseRoot, "npm-cache") },
  encoding: "utf8",
  windowsHide: true,
});
if (packed.status !== 0) throw new Error(`npm pack failed: ${packed.stderr || packed.stdout || packed.error?.message}`);
const filename = `deepprof-cli-${sourceManifest.version}.tgz`;
const artifact = join(releaseRoot, filename);
const checksum = createHash("sha256").update(readFileSync(artifact)).digest("hex");
writeFileSync(join(releaseRoot, "deepprof-cli-0.6.2.sha256"), `${checksum}  ${filename}\n`, "ascii");
const releaseAssets = [
  artifact,
  join(root, "docs/experiments/M1-M3-experimental-report.pdf"),
  join(root, "docs/experiments/M1-M3-实验报告.md"),
  join(root, "docs/experiments/releases/M1-M3-evidence.zip"),
  join(root, "docs/experiments/releases/M1-M3-SHA256SUMS.txt"),
];
for (const file of releaseAssets) {
  if (!existsSync(file)) throw new Error(`Required v0.6.2 release asset is missing: ${relative(root, file)}`);
}
const releaseSums = releaseAssets
  .map((file) => `${createHash("sha256").update(readFileSync(file)).digest("hex")}  ${relative(root, file).replaceAll("\\", "/")}\n`)
  .join("");
writeFileSync(join(releaseRoot, "DEEPPROF-v0.6.2-SHA256SUMS.txt"), releaseSums, "utf8");
process.stdout.write(`${packed.stdout || ""}\n包位置：${artifact}\nSHA-256：${checksum}\n`);
