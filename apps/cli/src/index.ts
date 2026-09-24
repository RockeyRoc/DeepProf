#!/usr/bin/env node
import * as readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { existsSync, readFileSync } from "node:fs";
import { appendFile, readFile, writeFile, mkdir } from "node:fs/promises";
import { spawn } from "node:child_process";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { DpapiSecretStore, secretEnvironmentName } from "../../../packages/client_sdk/node/dpapi_secret_store.js";
import { ProviderClient } from "../../../packages/client_sdk/provider_client.js";
import { CourseClient } from "../../../packages/client_sdk/course_client.js";
import { createRuntime } from "./runtime.js";
import { buildTree, ask, clients, transcriptText } from "./session_runner.js";
import { newSessionOptions } from "./session_options.js";
import type { AskResult } from "./session_runner.js";
import { loadState, saveState, type CliState } from "./state.js";
import { ansi, errorMessage, jsonError, jsonResult, logo, paint, usageStatus } from "./output.js";
import {
  findSystemPython,
  isDeveloperRuntime,
  preparePythonEnvironment,
  resolveRuntimePaths,
  type RuntimePaths,
} from "./packaged_runtime.js";

interface Options { json: boolean; apiUrl?: string; home?: string; command: string; args: string[]; }

function parseArgs(argv: string[]): Options {
  let json = false;
  let apiUrl: string | undefined;
  let home: string | undefined;
  const rest: string[] = [];
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--json") json = true;
    else if (value === "--api-url") apiUrl = argv[++index];
    else if (value === "--home") home = argv[++index];
    else rest.push(value);
  }
  return { json, apiUrl, home, command: rest[0] || "", args: rest.slice(1) };
}

function shellWords(line: string): string[] {
  const words: string[] = [];
  const pattern = /"((?:\\.|[^"])*)"|'((?:\\.|[^'])*)'|(\S+)/g;
  for (const match of line.matchAll(pattern)) words.push((match[1] ?? match[2] ?? match[3] ?? "").replace(/\\(["'\\])/g, "$1"));
  return words;
}

function parseInputLine(line: string): Options {
  const trimmed = line.trim();
  if (!trimmed.startsWith("/")) return parseArgs(["ask", "--action", "auto", trimmed]);
  const parsed = parseArgs(shellWords(trimmed.slice(1)));
  if (parsed.command === "ask" && !flag(parsed.args, "--action")) return { ...parsed, args: ["--action", "ask", ...parsed.args] };
  return parsed;
}

function flag(args: string[], name: string): string | undefined {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

function has(args: string[], name: string): boolean { return args.includes(name); }

async function askWithInterrupt(baseUrl: string, learnerId: string, sessionId: string, content: string,
  onDelta?: (text: string) => void, action = "ask"): Promise<Awaited<ReturnType<typeof ask>> | null> {
  const controller = new AbortController();
  const onInterrupt = () => controller.abort();
  process.once("SIGINT", onInterrupt);
  try { return await ask(baseUrl, learnerId, sessionId, content, onDelta, action, controller.signal); }
  catch (error) {
    if (errorMessage(error) === "turn_cancelled") return null;
    throw error;
  } finally { process.off("SIGINT", onInterrupt); }
}

function positionals(args: string[], flagsWithValues: string[] = []): string[] {
  const values: string[] = [];
  let skipValue = false;
  for (const item of args) {
    if (skipValue) { skipValue = false; continue; }
    if (item.startsWith("--")) { if (flagsWithValues.includes(item)) skipValue = true; continue; }
    values.push(item);
  }
  return values;
}

function selectedSession(command: string, args: string[], state: CliState): string | null {
  const explicit = flag(args, "--session");
  if (explicit) return explicit;
  if (["resume", "fork", "compact", "hint", "sources", "trace", "export", "quiz", "answer"].includes(command)) {
    const first = positionals(args, ["--title", "--keep", "--group", "--course", "--action", "--out"])[0];
    if (first?.startsWith("ses_")) return first;
  }
  return state.active_session_id;
}

function dataHome(): string { return resolve(process.env.DEEPPROF_HOME || join(homedir(), ".deepprof")); }
function runtimePaths(): RuntimePaths { return resolveRuntimePaths(); }
function runtimeRoot(): string { return runtimePaths().runtimeRoot; }
function questionBankPath(): string { return resolve(process.env.DEEPPROF_QUESTION_BANK_PATH || join(dataHome(), "course", "question_bank.json")); }

function prepareRuntime(withOcr = false): string {
  const paths = runtimePaths();
  const python = preparePythonEnvironment({
    paths,
    withOcr,
    onProgress: (message) => console.log(message),
  });
  process.env.DEEPPROF_RUNTIME_ROOT = paths.runtimeRoot;
  process.env.DEEPPROF_PYTHON = python;
  return python;
}

function printTopLevelHelp(): void {
  console.log(`DeepProf CLI v0.6.2

快速开始：deepprof （首次运行时自动准备本机环境）
  deepprof setup [--ocr]    安装 DeepProf Runtime（可选安装扫描件 OCR）
  deepprof doctor            检查 Node.js、Python 与本地安装状态
  deepprof --version         显示 CLI 版本
  deepprof --help            显示本帮助

环境变量：DEEPPROF_HOME、DEEPPROF_PYTHON、DEEPPROF_API_URL
在 CLI 中运行 /help 查看学习、题库和会话命令。${isDeveloperRuntime(runtimeRoot()) ? "\n开发环境还提供 /report 与 /acceptance --live。" : ""}`);
}

async function printDoctor(json: boolean): Promise<void> {
  const paths = runtimePaths();
  const home = dataHome();
  let python: { path: string | null; version: string | null; error: string | null };
  try {
    const detected = findSystemPython();
    python = { path: detected.executable, version: detected.version, error: null };
  } catch (error) {
    python = { path: null, version: null, error: errorMessage(error) };
  }
  let installed = false;
  try {
    const state = JSON.parse(readFileSync(join(home, "runtime", "0.6.2", "install-state.json"), "utf8")) as { runtimeHash?: string };
    installed = Boolean(state.runtimeHash && existsSync(join(home, "runtime", "0.6.2", "venv")));
  } catch { /* Setup has not completed. */ }
  const gateway = createRuntime();
  let gatewayStatus: string;
  try {
    const status = await gateway.start();
    gatewayStatus = status.state === "READY" && status.baseUrl ? "ready" : status.state.toLowerCase();
  } finally { gateway.stop(); }
  const report = {
    node: process.versions.node,
    node_supported: Number(process.versions.node.split(".")[0]) >= 22,
    python,
    runtime_root: paths.runtimeRoot,
    user_data: home,
    runtime_installed: installed,
    gateway: gatewayStatus,
    developer_features: isDeveloperRuntime(paths.runtimeRoot),
  };
  if (json) jsonResult("doctor", report);
  else {
    console.log(`Node.js ${report.node}: ${report.node_supported ? "OK" : "需要 22 或更新版本"}`);
    console.log(`Python: ${python.version || python.error}`);
    console.log(`DeepProf Runtime: ${installed ? "已安装" : "未安装；运行 deepprof setup"}`);
    console.log(`本地 Gateway: ${gatewayStatus}`);
    console.log(`用户数据目录: ${home}`);
    if (report.developer_features) console.log("实验开发命令: 可用");
  }
}

function normalizeProviderBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  try {
    const parsed = new URL(trimmed);
    if (parsed.hostname.toLowerCase() === "api.deepseek.com" && parsed.pathname.toLowerCase() === "/v1") {
      parsed.pathname = "";
      parsed.search = "";
      parsed.hash = "";
      return parsed.toString().replace(/\/$/, "");
    }
  } catch { /* preserve the original value so the provider reports a useful URL error */ }
  return trimmed;
}

async function runChild(command: string, args: string[], env: NodeJS.ProcessEnv = process.env, quiet = false): Promise<number> {
  return await new Promise((resolveExit, reject) => {
    const child = spawn(command, args, { cwd: runtimeRoot(), env, stdio: quiet ? ["ignore", "ignore", "inherit"] : "inherit", windowsHide: true });
    child.once("error", reject);
    child.once("exit", (code, signal) => signal ? reject(new Error(`child_process_signal:${signal}`)) : resolveExit(code ?? 1));
  });
}

async function showQuestionBank(json: boolean): Promise<void> {
  try {
    const bank = JSON.parse(await readFile(questionBankPath(), "utf8")) as Record<string, unknown>;
    const questions = Array.isArray(bank.questions) ? bank.questions as Array<Record<string, unknown>> : [];
    const modules: Record<string, number> = {}, statuses: Record<string, number> = {};
    let automaticCount = 0;
    for (const row of questions) {
      const module = String(row.module || "unknown"), review = String(row.source_review_status || "unreviewed");
      modules[module] = (modules[module] || 0) + 1; statuses[review] = (statuses[review] || 0) + 1;
      const grading = row.grading && typeof row.grading === "object" ? row.grading as Record<string, unknown> : {};
      if (review === "verified" && row.grading_review_status === "verified"
        && grading.method === "exact_normalized_match" && Array.isArray(grading.accepted_answers)) automaticCount += 1;
    }
    const report = { path: questionBankPath(), version: bank.version || "", question_count: questions.length,
      automatically_graded_count: automaticCount, module_counts: modules, review_counts: statuses,
      teacher_approval: bank.teacher_approval || "pending" };
    if (json) jsonResult("course.bank", report);
    else console.log(`${report.path}\nversion ${report.version} · ${report.question_count} questions · ${automaticCount} deterministic scoring rules · review ${JSON.stringify(statuses)} · teacher ${report.teacher_approval}`);
  } catch (error) {
    if (json) jsonResult("course.bank", { path: questionBankPath(), configured: false, reason: errorMessage(error) });
    else console.log(`题库未就绪：${questionBankPath()}\n${errorMessage(error)}\nOCR 候选不会自动评分；先逐题核对原页和答案页。`);
  }
}

async function latestTrace(baseUrl: string, learnerId: string, sessionId: string | null): Promise<string | null> {
  if (!sessionId) return null;
  try {
    const messages = await clients(baseUrl, learnerId).sessions.transcript(sessionId);
    const last = [...messages].reverse().find((message) => message.role === "assistant");
    return String(last?.metadata?.trace_id || "") || null;
  } catch { return null; }
}

async function writeFeedback(baseUrl: string, state: CliState, note: string): Promise<void> {
  if (!note.trim()) throw new Error("feedback_text_required");
  const targetDir = join(dataHome(), "experiments");
  await mkdir(targetDir, { recursive: true });
  const row = { feedback_id: crypto.randomUUID(), created_at: new Date().toISOString(), session_id: state.active_session_id,
    trace_id: await latestTrace(baseUrl, state.learner_id, state.active_session_id), text: note.trim(), data_class: "local_developer_feedback" };
  await appendFile(join(targetDir, "feedback.jsonl"), `${JSON.stringify(row)}\n`, { encoding: "utf8" });
  console.log(`反馈已本地记录 · trace ${row.trace_id || "未找到"}`);
}

async function runAcceptance(args: string[], baseUrl = "http://127.0.0.1:8000", json = false): Promise<void> {
  if (!args.includes("--live")) {
    const message = "真实验收不会在启动时自动运行。先运行 deepprof login 配置 Provider，再明确运行 deepprof acceptance --live。";
    if (json) jsonResult("acceptance", { status: "not_started", message }); else console.log(message);
    return;
  }
  const argv = [join(runtimeRoot(), "scripts", "run_ab_acceptance.py"), "--live", "--api-url", baseUrl];
  const repeatIndex = args.indexOf("--repeat-after-fix");
  if (repeatIndex >= 0 && args[repeatIndex + 1]) argv.push("--repeat-after-fix", args[repeatIndex + 1]);
  const code = await runChild(process.env.DEEPPROF_PYTHON || "python", argv);
  if (code === 2) {
    let blocker: unknown = null;
    try { blocker = JSON.parse(await readFile(join(dataHome(), "experiments", "acceptance-blocker.json"), "utf8")); } catch { /* blocker text remains actionable */ }
    if (json) jsonResult("acceptance", { status: "blocked_before_run", blocker });
    else console.log(`真实验收尚未启动，阻塞原因已保存到 ${join(dataHome(), "experiments", "acceptance-blocker.json")}。运行 deepprof login 配置真实 Provider 后再重试。`);
    return;
  }
  if (code !== 0) throw new Error(`acceptance_failed_exit_${code}`);
}

async function showReport(json: boolean): Promise<void> {
  const python = process.env.DEEPPROF_PYTHON || "python";
  const snapshotBuilder = join(runtimeRoot(), "scripts", "build_experiment_snapshot.py");
  const snapshotCode = await runChild(python, [snapshotBuilder], process.env, json);
  if (snapshotCode !== 0) throw new Error(`report_snapshot_failed_exit_${snapshotCode}`);

  const chartScript = join(runtimeRoot(), "docs", "experiments", "generate_charts.R");
  const rscript = process.env.DEEPPROF_RSCRIPT || (process.env.R_HOME
    ? join(process.env.R_HOME, "bin", process.platform === "win32" ? "x64" : "", process.platform === "win32" ? "Rscript.exe" : "Rscript")
    : "Rscript");
  const chartCode = await runChild(rscript, [chartScript], process.env, json);
  if (chartCode !== 0) throw new Error(`report_chart_generation_failed_exit_${chartCode}`);

  const reportBuilder = join(runtimeRoot(), "scripts", "build_experiment_report.py");
  const reportCode = await runChild(python, [reportBuilder], process.env, json);
  if (reportCode !== 0) throw new Error(`report_document_generation_failed_exit_${reportCode}`);

  const markdown = join(runtimeRoot(), "docs", "experiments", "图表库.md");
  const docx = join(runtimeRoot(), "docs", "experiments", "图表库.docx");
  const snapshot = join(runtimeRoot(), "docs", "experiments", "source-data", "experiment-snapshot.json");
  const manifest = join(dataHome(), "experiments", "latest-summary.json");
  let summary: unknown = null;
  try { summary = JSON.parse(await readFile(manifest, "utf8")); } catch { /* no live acceptance yet */ }
  if (json) return jsonResult("report", { markdown, docx, snapshot, summary });
  console.log(`图表库已更新：${markdown}\nDOCX：${docx}\n本次快照：${snapshot}\n本地验收摘要：${manifest}`);
  if (summary) console.log(JSON.stringify(summary, null, 2));
  else console.log("尚无真实 Provider 验收运行；手动登录后运行 /acceptance --live。");
}

async function hiddenQuestion(prompt: string): Promise<string> {
  if (!input.isTTY || !input.setRawMode) throw new Error("secret_input_requires_tty_or_stdin");
  output.write(prompt);
  return new Promise((resolve) => {
    let value = "";
    const onData = (chunk: Buffer) => {
      const text = chunk.toString();
      if (text === "\u0003") { input.setRawMode?.(false); input.off("data", onData); output.write("\n"); resolve(""); return; }
      if (text === "\r" || text === "\n") { input.setRawMode?.(false); input.off("data", onData); output.write("\n"); resolve(value); return; }
      if (text === "\u007f") { value = value.slice(0, -1); return; }
      value += text;
    };
    input.setRawMode(true);
    input.on("data", onData);
  });
}

async function login(baseUrl: string, state: CliState, args: string[], json: boolean,
  activeReadline?: readline.Interface, restoreReadline?: () => void): Promise<void> {
  const providers = new ProviderClient(baseUrl);
  if (has(args, "--api-key")) throw new Error("api_key_command_line_not_supported; use --api-key-stdin or a Secret environment variable");
  if (has(args, "--show-api-key") && has(args, "--hidden-api-key")) throw new Error("choose_one_api_key_display_mode");
  const terminal = input.isTTY && !json && !has(args, "--api-key-stdin");
  const ownsReadline = !activeReadline;
  // The REPL already owns stdin; a second readline.Interface duplicates key input on some Windows terminals.
  const rl = activeReadline || readline.createInterface({ input, output });
  let closedForSecretInput = false;
  try {
    const profileId = flag(args, "--profile") || (terminal ? await rl.question("Profile ID [default]: ") : "default") || "default";
    const displayName = flag(args, "--display-name") || (terminal ? await rl.question("Display name [DeepProf Provider]: ") : "DeepProf Provider") || "DeepProf Provider";
    const requestedBase = flag(args, "--base-url") || (terminal ? await rl.question("Base URL [http://127.0.0.1:11434/v1]: ") : "http://127.0.0.1:11434/v1") || "http://127.0.0.1:11434/v1";
    const base = normalizeProviderBaseUrl(requestedBase);
    if (terminal && base !== requestedBase.trim().replace(/\/+$/, "")) {
      console.log(`DeepSeek Base URL 已规范为 ${base}（DeepSeek OpenAI 兼容接口不使用 /v1 前缀）。`);
    }
    const model = flag(args, "--model") || (terminal ? await rl.question("Model (optional): ") : "");
    let existingProfile = false;
    if (terminal && !has(args, "--show-api-key")) {
      try { existingProfile = (await providers.list()).some((profile) => profile.profile_id === profileId); } catch { /* first-run gateway may not expose profiles yet */ }
    }
    let key = process.env[secretEnvironmentName(`provider:${profileId}`)] || "";
    if (has(args, "--api-key-stdin")) key = (await new Promise<string>((resolve) => { let data = ""; input.setEncoding("utf8"); input.on("data", (chunk) => { data += chunk; }); input.on("end", () => resolve(data.trim())); })).trim();
    else if (terminal && (has(args, "--show-api-key") || (!has(args, "--hidden-api-key") && !existingProfile))) {
      console.log("首次配置：API Key 将在屏幕上显示；输入完成后按 Enter。以后运行 login 默认隐藏输入。");
      key = await rl.question("API Key (visible first setup): ");
    } else if (terminal) {
      // Raw-mode secret input needs readline detached; the REPL recreates it in finally, even on failure.
      rl.close();
      closedForSecretInput = true;
      key = await hiddenQuestion("API Key (hidden; type or paste, then press Enter): ");
    }
    if (!key && !base.startsWith("http://127.0.0.1")) throw new Error("api_key_required_without_local_provider");
    const apiKeyRef = `provider:${profileId}`;
    // Encrypt before sending the key to the local Gateway. A DPAPI failure
    // must not leave a credential in a half-configured runtime process.
    if (key && process.platform === "win32") new DpapiSecretStore().set(apiKeyRef, key);
    const saved = await providers.upsert({ profile_id: profileId, display_name: displayName, base_url: base, default_model: model, api_key: key || undefined, models: [] });
    let models: string[] = [];
    try { models = await providers.models(profileId); } catch { /* manual model entry remains valid */ }
    const chosenModel = model || models[0] || saved.default_model;
    let probe: Record<string, unknown>;
    try {
      probe = await providers.probe(profileId, chosenModel || undefined);
    } catch (error) {
      probe = { status: "failed", code: error instanceof Error ? error.name : "probe_failed", message: errorMessage(error) };
    }
    await providers.setDefault(profileId, chosenModel);
    const persisted = process.platform === "win32" && Boolean(key);
    const data = {
      profile: saved,
      models,
      probe,
      connected: probe.status === "ok",
      persisted_secret: persisted,
      secret_storage: process.platform === "win32" ? "dpapi_current_user" : "environment_only",
      warning: process.platform === "win32" || !key ? null : "credential_not_persisted_on_this_platform",
    };
    if (json) jsonResult("login", data);
    else if (data.connected) console.log(paint(`已连接 ${saved.display_name} · ${chosenModel || "manual model"}`, "green", true));
    else console.log(paint(`连接探测失败 · ${String(probe.kind || probe.status || "unknown")}: ${String(probe.message || "未获得成功响应")}`, "red", true));
    if (!json && data.warning) console.log(paint("Credential is process-only on this platform; use DEEPPROF_SECRET_* for future runs.", "yellow", true));
  } finally {
    if (ownsReadline) rl.close();
    else if (closedForSecretInput) restoreReadline?.();
  }
}

async function runCommand(options: Options): Promise<void> {
  const runtime = createRuntime(options.apiUrl);
  const status = await runtime.start();
  if (!status.baseUrl) throw new Error("runtime_unavailable");
  const state = loadState();
  const { sessions, commands } = clients(status.baseUrl, state.learner_id);
  const provider = new ProviderClient(status.baseUrl);
  const courses = new CourseClient(status.baseUrl);
  const command = options.command;
  try {
    if (command === "login") return await login(status.baseUrl, state, options.args, options.json);
    if (command === "models") {
      const selection = await provider.defaultSelection();
      const profileId = flag(options.args, "--profile") || selection.profile_id;
      const models = await provider.models(profileId);
      if (options.json) jsonResult(command, { profile_id: profileId, models }); else console.log(models.join("\n"));
      return;
    }
    if (command === "doctor") {
      let selection;
      try { selection = await provider.defaultSelection(); }
      catch (error) {
    if (errorMessage(error).includes("no provider profile registered")) throw new Error("尚未配置 Provider。运行 deepprof login 输入服务地址、模型和 API Key；首次配置会显示密钥输入，后续默认隐藏。");
        throw error;
      }
      const result = await provider.probe(selection.profile_id, selection.model || undefined);
      if (options.json) jsonResult(command, { selection, result }); else console.log(`${selection.profile_id}/${selection.model}: ${String(result.status || "unknown")}`);
      return;
    }
    if (command === "new") {
      const { sessionMode, group } = newSessionOptions(flag(options.args, "--mode"), flag(options.args, "--group"));
      const title = flag(options.args, "--title") || positionals(options.args, ["--group", "--course", "--mode", "--title"]).join(" ");
      const courseId = flag(options.args, "--course") || state.course_id;
      const id = await sessions.create(title || (sessionMode === "chat" ? "常规对话" : "学习会话"), group, courseId, sessionMode);
      saveState({ ...state, active_session_id: id, experiment_group: group, course_id: courseId });
      return options.json ? jsonResult(command, {}, id) : console.log(id);
    }
    if (command === "course" || command === "courses") {
      const subcommand = positionals(options.args, ["--course"])[0] || "list";
      if (subcommand === "bank") {
        const action = positionals(options.args, ["--pdf"])[1] || "status";
        if (action === "ocr") {
          const pdf = flag(options.args, "--pdf") || process.env.DEEPPROF_QUESTION_PDF;
          if (!pdf) throw new Error("question_pdf_path_required_use_--pdf_or_DEEPPROF_QUESTION_PDF");
          const code = await runChild(process.env.DEEPPROF_PYTHON || "python", [join(runtimeRoot(), "scripts", "ocr_question_collection.py"), resolve(pdf)]);
          if (code !== 0) throw new Error(`question_pdf_ocr_failed_exit_${code}`);
        } else await showQuestionBank(options.json);
        return;
      }
      if (subcommand === "list") {
        const list = await courses.list();
        if (options.json) return jsonResult("course.list", { courses: list }, state.active_session_id);
        for (const item of list) console.log(`${item.course_id} · ${item.title} · ${item.concept_count} concepts · ${item.imported ? "教材已导入" : item.configured ? "可导入" : "教材路径未配置"}`);
        return;
      }
      if (subcommand === "use") {
        const id = positionals(options.args, ["--course"])[1] || flag(options.args, "--course");
        if (!id) throw new Error("course_id_required");
        const list = await courses.list();
        if (!list.some((item) => item.course_id === id)) throw new Error("course_not_found");
        saveState({ ...state, course_id: id });
        return options.json ? jsonResult("course.use", { course_id: id }, state.active_session_id) : console.log(`course ${id}`);
      }
      if (subcommand === "import") {
        const id = flag(options.args, "--course") || state.course_id;
        const result = await courses.import(id);
        return options.json ? jsonResult("course.import", result, state.active_session_id) : console.log(`imported ${id}: ${String(result.resource_id || "")}${result.duplicate ? " (duplicate)" : ""}`);
      }
      throw new Error(`unknown_course_command:${subcommand}`);
    }
    if (command === "tree") {
      const tree = buildTree(await sessions.list(state.learner_id));
      return options.json ? jsonResult(command, { tree }, state.active_session_id) : printTree(tree);
    }
    const sessionId = selectedSession(command, options.args, state);
    if (command === "resume") {
      if (!sessionId) throw new Error("session_id_required");
      await sessions.resume(sessionId);
      const transcript = await sessions.transcript(sessionId);
      saveState({ ...state, active_session_id: sessionId });
      return options.json ? jsonResult(command, { messages: transcript }, sessionId) : console.log(transcriptText(transcript));
    }
    if (command === "fork") {
      if (!sessionId) throw new Error("session_id_required");
      const id = await sessions.fork(sessionId, flag(options.args, "--title"));
      saveState({ ...state, active_session_id: id });
      return options.json ? jsonResult(command, {}, id) : console.log(id);
    }
    if (command === "compact") {
      if (!sessionId) throw new Error("session_id_required");
      const keep = Number(flag(options.args, "--keep") || 60);
      const result = await sessions.compact(sessionId, keep);
      return options.json ? jsonResult(command, { result }, sessionId) : console.log(`compacted ${sessionId}`);
    }
    if (command === "ask") {
      const content = positionals(options.args, ["--session", "--action"]).filter((item) => item !== sessionId).join(" ").trim();
      let active = sessionId;
      if (!active) { active = await sessions.create("学习会话", state.experiment_group, state.course_id); saveState({ ...state, active_session_id: active }); }
      if (!content) throw new Error("question_required");
      const result = await askWithInterrupt(status.baseUrl, state.learner_id, active, content,
        options.json ? undefined : (text) => process.stdout.write(text), flag(options.args, "--action"));
      if (!result) { if (!options.json) console.log("本轮已取消，会话已保留。"); return; }
      if (options.json) jsonResult(command, { answer: result.answer, usage: result.usage, provider_profile: result.provider_profile, model: result.model,
        group: result.experiment_group, action: result.action, policy_version: result.policy_version,
        session_mode: result.session_mode, turn_mode: result.turn_mode,
        routing_reason: result.routing_reason, routing_version: result.routing_version,
        learner_estimate_status: result.learner_estimate_status, mastery: result.mastery,
        learner_evidence_count: result.learner_evidence_count, learner_updated_at: result.learner_updated_at,
        learner_uncertainty: result.learner_uncertainty, bkt_model_version: result.bkt_model_version,
        evidence_refs: result.evidence_refs, trace_id: result.trace_id,
        sequence: result.last_sequence, last_sequence: result.last_sequence }, active);
      else {
        if (!result.streamed) process.stdout.write(result.answer);
        const turnLabel = result.turn_mode === "chat" ? "常规对话" : `${result.experiment_group} · ${result.action || "turn"}`;
        process.stdout.write(`\n${paint(`${turnLabel} · ${result.provider_profile || "no model"}/${result.model || "unconfigured"} · trace ${result.trace_id}`, "dim", true)}\n`);
        if (result.turn_mode === "study") { printLearningSummary(result); printSources(result.evidence_refs); }
        saveState({ ...state, active_session_id: active });
      }
      return;
    }
    if (command === "hint") {
      const content = positionals(options.args, ["--session"]).filter((item) => item !== sessionId).join(" ") || "请给我一个提示";
      let active = sessionId;
      if (!active) active = await sessions.create("学习会话", state.experiment_group, state.course_id);
      const result = await askWithInterrupt(status.baseUrl, state.learner_id, active, content,
        options.json ? undefined : (text) => process.stdout.write(text), "hint");
      if (!result) { if (!options.json) console.log("本轮已取消，会话已保留。"); return; }
      if (!result.streamed && !options.json) process.stdout.write(result.answer);
      if (options.json) jsonResult("hint", { ...result }, active);
      else {
        process.stdout.write(`\n${result.experiment_group} · ${result.action} · ${result.policy_version || ""} · trace ${result.trace_id}\n`);
        printLearningSummary(result); printSources(result.evidence_refs);
      }
      saveState({ ...state, active_session_id: active });
      return;
    }
    if (command === "quiz" || command === "answer") {
      let active = sessionId;
      if (!active) { active = await sessions.create("学习测验", state.experiment_group, state.course_id); saveState({ ...state, active_session_id: active }); }
      const payload = command === "answer"
        ? { answer: positionals(options.args, ["--session", "--item-id"]).filter((value) => value !== active).join(" "), item_id: flag(options.args, "--item-id") }
        : { concept_id: flag(options.args, "--concept") || "" };
      const accepted = await commands.send(commands.create(command === "answer" ? "quiz.answer" : "quiz.generate", payload, active));
      if (options.json) jsonResult(command, accepted.result || {}, active);
      else if (command === "quiz") console.log(JSON.stringify(accepted.result || {}, null, 2));
      else if (accepted.result?.pending_review) console.log(`待人工判分 · ${String(accepted.result?.item_id || "")} · 本次不更新学情`);
      else console.log(`${accepted.result?.correct ? "正确" : "需要再想一想"} · ${String(accepted.result?.item_id || "")} · ${String(accepted.result?.grading_source || "")}`);
      if (!options.json && command === "answer") printAttemptLearning(accepted.result || {});
      return;
    }
    if (command === "learner") {
      if (!sessionId) throw new Error("session_id_required");
      const result = await sessions.learner(sessionId, flag(options.args, "--concept") || "");
      return options.json ? jsonResult(command, result, sessionId) : console.log(JSON.stringify(result, null, 2));
    }
    if (command === "ocr") {
      const source = positionals(options.args, ["--first-page", "--last-page"])[0];
      if (!source) throw new Error("document_path_required");
      const payload = { path: resolve(source), first_page: flag(options.args, "--first-page") ? Number(flag(options.args, "--first-page")) : null,
        last_page: flag(options.args, "--last-page") ? Number(flag(options.args, "--last-page")) : null,
        force_ocr: has(options.args, "--force-ocr") };
      const accepted = await commands.send(commands.create("document.convert", payload));
      const result = accepted.result || {};
      return options.json ? jsonResult(command, result) : console.log(JSON.stringify(result, null, 2));
    }
    if (command === "acceptance") return await runAcceptance(options.args, status.baseUrl, options.json);
    if (command === "feedback") return await writeFeedback(status.baseUrl, state, positionals(options.args, ["--session"]).join(" "));
    if (command === "report") return await showReport(options.json);
    if (command === "sources") {
      if (!sessionId) throw new Error("session_id_required");
      const messages = await sessions.transcript(sessionId);
      const last = messages.filter((item) => item.role === "assistant").at(-1);
      const refs = Array.isArray(last?.metadata?.evidence_refs) ? last.metadata.evidence_refs as Array<Record<string, unknown>> : [];
      if (options.json) jsonResult(command, { evidence_refs: refs }, sessionId); else printSources(refs);
      return;
    }
    if (command === "trace" || command === "export") {
      if (!sessionId) throw new Error("session_id_required");
      const data = await courses.replay(sessionId);
      const serialized = JSON.stringify(data, null, 2);
      const outPath = flag(options.args, "--out");
      if (command === "export" && outPath) {
        await writeFile(outPath, serialized, { encoding: "utf8" });
        return options.json ? jsonResult(command, { path: outPath, redacted: true }, sessionId) : console.log(`redacted export written: ${outPath}`);
      }
      if (options.json || command === "export") return jsonResult(command, data, sessionId);
      const events = (data.events || []) as Array<Record<string, unknown>>;
      for (const item of events) console.log(`${item.sequence} ${item.type} trace=${item.trace_id} ${JSON.stringify(item.payload)}`);
      return;
    }
    throw new Error(`unknown_command:${command}`);
  } finally {
    runtime.stop();
  }
}

function printSources(refs: Array<Record<string, unknown>>): void {
  if (!refs.length) { console.log("no citations"); return; }
  for (const ref of refs) console.log(`source ${String(ref.chapter || ref.section || "教材")} · PDF page ${String(ref.page)} · book page ${String(ref.printed_page ?? "unmapped")} · ${String(ref.chunk_id || "")}`);
}

function printLearningSummary(result: Pick<AskResult, "experiment_group" | "policy_version" | "learner_estimate_status"
  | "mastery" | "learner_evidence_count" | "learner_updated_at" | "learner_uncertainty" | "bkt_model_version">): void {
  if (result.policy_version) console.log(`策略版本 ${result.policy_version}`);
  if (result.experiment_group !== "C") return;
  const version = result.bkt_model_version || "BKT 未查询";
  if (!result.learner_estimate_status) {
    console.log(`本轮未识别到知识点，${version}`);
  } else if (result.learner_estimate_status !== "available" || result.mastery === null) {
    console.log(`BKT 信息不足 · 证据 ${result.learner_evidence_count}/3 · ${version}`);
  } else {
    const entropy = result.learner_uncertainty === null ? "不可用" : `${result.learner_uncertainty.toFixed(3)} bits`;
    console.log(`BKT 掌握估计 ${result.mastery.toFixed(3)} · 证据 ${result.learner_evidence_count}`
      + ` · 二元熵 ${entropy}（不是置信区间） · 更新 ${result.learner_updated_at || "未知"} · ${version}`);
  }
}

function printAttemptLearning(result: Record<string, unknown>): void {
  const update = String(result.bkt_update || "未返回更新状态");
  if (update === "group_disabled") {
    console.log("学情未启用：本组不读取或更新 BKT。");
    return;
  }
  const estimate = result.learner_estimate && typeof result.learner_estimate === "object"
    ? result.learner_estimate as Record<string, unknown> : {};
  const status = String(estimate.status || "");
  const model = String(estimate.model_version || "");
  const evidence = Number(estimate.evidence_count || 0);
  if (status === "available" && typeof estimate.mastery === "number") {
    const entropy = typeof estimate.uncertainty === "number" ? `${estimate.uncertainty.toFixed(3)} bits` : "不可用";
    console.log(`学情 ${update} · 掌握估计 ${estimate.mastery.toFixed(3)} · 证据 ${evidence}`
      + ` · 二元熵 ${entropy}（不是置信区间） · 更新 ${String(estimate.updated_at || "未知")} · ${model}`);
  } else {
    console.log(`学情 ${update} · 信息不足 · 证据 ${evidence}/3 · ${model}`);
  }
}

function printTree(nodes: Array<{ session_id: string; title: string; children: Array<unknown> }>, depth = 0): void {
  for (const node of nodes) { console.log(`${"  ".repeat(depth)}${node.session_id} ${node.title || "(untitled)"}`); printTree(node.children as Array<{ session_id: string; title: string; children: Array<unknown> }>, depth + 1); }
}

async function repl(apiUrl?: string): Promise<void> {
  const runtime = createRuntime(apiUrl);
  const status = await runtime.start();
  if (!status.baseUrl) throw new Error("runtime_unavailable");
  const state = loadState();
  const { sessions, commands } = clients(status.baseUrl, state.learner_id);
  const provider = new ProviderClient(status.baseUrl);
  let activeAbort: AbortController | null = null;
  let rl = readline.createInterface({ input, output, terminal: true });
  const onReadlineSigint = () => {
    if (activeAbort) {
      console.log(paint("\n正在取消当前回合…", "yellow", true));
      activeAbort.abort();
    } else rl.close();
  };
  const createReadline = () => {
    const current = readline.createInterface({ input, output, terminal: true });
    current.on("SIGINT", onReadlineSigint);
    return current;
  };
  rl.on("SIGINT", onReadlineSigint);
  const cumulativeUsage: Record<string, unknown> = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  console.log(logo(true));
  let modelStatus = "模型未配置 · 使用 /login 配置 Provider";
  try {
    const selection = await provider.defaultSelection();
    if (selection.model) modelStatus = `${selection.profile_id}/${selection.model}`;
  } catch { /* actionable setup message is shown below */ }
  console.log(paint(`${state.course_id} · group ${state.experiment_group} · ${modelStatus}`, "dim", true));
  console.log(paint("/help 查看命令 · 空闲时 Ctrl+C 或 /quit 退出 · 执行中 Ctrl+C 取消本轮", "dim", true));
  if (state.active_session_id) console.log(paint(`session ${state.active_session_id}`, "dim", true));
  try {
    while (true) {
      let line: string;
      try { line = (await rl.question(`${ansi.cyan}you › ${ansi.reset}`)).trim(); }
      catch { break; }
      if (!line) continue;
      try {
      if (["/quit", "/exit", "quit", "exit"].includes(line.toLowerCase())) break;
      const parsed = parseInputLine(line);
      const parts = parsed.args;
      const command = parsed.command;
      if (command === "help") { console.log("/login [--show-api-key|--hidden-api-key] /new [--mode chat|study] [--group A|B|C] /chat <内容> /study <内容> /ask /hint /quiz /answer /learner /ocr <file> /feedback /acceptance --live /report /course list|use|import|bank /sources /trace /export /resume /tree /fork /compact /models /doctor /quit"); continue; }
      if (command === "ask") {
        let active = state.active_session_id;
        if (!active) { active = await sessions.create("常规对话", "B", state.course_id, "chat"); state.active_session_id = active; saveState(state); }
        activeAbort = new AbortController();
        console.log(paint("正在执行 · Ctrl+C 取消本轮", "dim", true));
        let result;
        try { result = await ask(status.baseUrl, state.learner_id, active,
          positionals(parts, ["--action"]).join(" "), (text) => process.stdout.write(text),
          flag(parts, "--action") || "auto", activeAbort.signal); }
        finally { activeAbort = null; }
        for (const key of ["prompt_tokens", "completion_tokens", "total_tokens"]) {
          const current = typeof cumulativeUsage[key] === "number" ? cumulativeUsage[key] as number : 0;
          const next = typeof result.usage[key] === "number" ? result.usage[key] as number : 0;
          cumulativeUsage[key] = current + next;
          result.usage[key] = cumulativeUsage[key];
        }
        if (!result.streamed) process.stdout.write(result.answer);
        const summary = await sessions.get(active);
        const turnLabel = result.turn_mode === "chat" ? "常规对话" : `${result.experiment_group} · ${result.action}`;
        console.log(`\n${paint(`${turnLabel} · ${usageStatus(result.provider_profile, result.model, result.usage)} · ${summary.message_count} messages · trace ${result.trace_id}`, "dim", true)}`);
        if (result.turn_mode === "study") { printLearningSummary(result); printSources(result.evidence_refs); }
      } else if (command === "new") {
        const { sessionMode, group } = newSessionOptions(flag(parts, "--mode"), flag(parts, "--group"));
        state.experiment_group = group;
        state.course_id = flag(parts, "--course") || state.course_id;
        state.active_session_id = await sessions.create((flag(parts, "--title") || positionals(parts, ["--group", "--course", "--mode", "--title"]).join(" ")) || (sessionMode === "chat" ? "常规对话" : "学习会话"), group, state.course_id, sessionMode); saveState(state); console.log(state.active_session_id);
      } else if (command === "chat" || command === "study") {
        const explicitMode = command;
        const content = positionals(parts, ["--session"]).filter((item) => item !== flag(parts, "--session")).join(" ").trim();
        if (!content) throw new Error("message_required");
        let active = state.active_session_id;
        if (!active) {
          const group = explicitMode === "study" ? "B" : state.experiment_group;
          active = await sessions.create(explicitMode === "chat" ? "常规对话" : "学习会话", group, state.course_id, explicitMode);
          if (explicitMode === "study") state.experiment_group = "B";
          state.active_session_id = active; saveState(state);
        }
        const summary = await sessions.get(active);
        if (explicitMode === "study" && summary.session_mode === "chat") throw new Error("当前是常规对话。请先运行 /new --mode study 创建教学会话。");
        activeAbort = new AbortController();
        let result;
        try { result = await ask(status.baseUrl, state.learner_id, active, content,
          (text) => process.stdout.write(text), explicitMode, activeAbort.signal); }
        finally { activeAbort = null; }
        if (!result.streamed) process.stdout.write(result.answer);
        console.log(`\n${result.turn_mode === "chat" ? "常规对话" : `${result.experiment_group} · ${result.action}`} · trace ${result.trace_id}`);
        if (result.turn_mode === "study") { printLearningSummary(result); printSources(result.evidence_refs); }
      } else if (command === "hint") {
        let active = state.active_session_id;
        if (!active) { active = await sessions.create("学习会话", state.experiment_group, state.course_id, "study"); state.active_session_id = active; saveState(state); }
        activeAbort = new AbortController();
        console.log(paint("正在生成提示 · Ctrl+C 取消本轮", "dim", true));
        let result;
        try { result = await ask(status.baseUrl, state.learner_id, active, positionals(parts, ["--session"]).join(" ") || "请给我一个提示", (text) => process.stdout.write(text), "hint", activeAbort.signal); }
        finally { activeAbort = null; }
        if (!result.streamed) process.stdout.write(result.answer);
        console.log(`\n${result.experiment_group} · ${result.action} · trace ${result.trace_id}`);
        printLearningSummary(result); printSources(result.evidence_refs);
      } else if (command === "resume") {
        const id = parts[0] || state.active_session_id; if (!id) throw new Error("session_id_required");
        await sessions.resume(id); state.active_session_id = id; saveState(state); console.log(transcriptText(await sessions.transcript(id)));
      } else if (command === "tree") {
        printTree(buildTree(await sessions.list(state.learner_id)));
      } else if (command === "fork") {
        if (!state.active_session_id) throw new Error("session_id_required"); state.active_session_id = await sessions.fork(state.active_session_id, parts.join(" ") || undefined); saveState(state); console.log(state.active_session_id);
      } else if (command === "compact") {
        if (!state.active_session_id) throw new Error("session_id_required"); await sessions.compact(state.active_session_id, Number(parts[0] || 60)); console.log("compacted");
      } else if (command === "models") {
        const selection = await new ProviderClient(status.baseUrl).defaultSelection(); console.log((await new ProviderClient(status.baseUrl).models(selection.profile_id)).join("\n"));
      } else if (command === "doctor") {
        const selection = await new ProviderClient(status.baseUrl).defaultSelection(); console.log(await new ProviderClient(status.baseUrl).probe(selection.profile_id, selection.model));
      } else if (command === "login") {
        await login(status.baseUrl, state, parts, false, rl, () => { rl = createReadline(); });
      } else if (command === "course" || command === "courses") {
        const sub = positionals(parts, ["--course"])[0] || "list";
        if (sub === "list") for (const item of await new CourseClient(status.baseUrl).list()) console.log(`${item.course_id} · ${item.title} · ${item.concept_count} concepts · ${item.imported ? "教材已导入" : item.configured ? "可导入" : "教材路径未配置"}`);
        else if (sub === "bank") {
          if (positionals(parts, ["--pdf"])[1] === "ocr") {
            const pdf = flag(parts, "--pdf") || process.env.DEEPPROF_QUESTION_PDF;
            if (!pdf) throw new Error("question_pdf_path_required");
            const code = await runChild(process.env.DEEPPROF_PYTHON || "python", [join(runtimeRoot(), "scripts", "ocr_question_collection.py"), resolve(pdf)]);
            if (code !== 0) throw new Error(`question_pdf_ocr_failed_exit_${code}`);
          } else await showQuestionBank(false);
        }
        else if (sub === "use") { const id = positionals(parts, ["--course"])[1] || flag(parts, "--course"); if (!id) throw new Error("course_id_required"); state.course_id = id; saveState(state); console.log(`course ${id}`); }
        else if (sub === "import") { const result = await new CourseClient(status.baseUrl).import(flag(parts, "--course") || state.course_id); console.log(`imported ${String(result.resource_id || state.course_id)}${result.duplicate ? " (duplicate)" : ""}`); }
        else console.log(paint(`unknown course command: ${sub}`, "yellow", true));
      } else if (command === "sources") {
        if (!state.active_session_id) throw new Error("session_id_required");
        const messages = await sessions.transcript(state.active_session_id); const last = messages.filter((item) => item.role === "assistant").at(-1);
        printSources(Array.isArray(last?.metadata?.evidence_refs) ? last.metadata.evidence_refs as Array<Record<string, unknown>> : []);
      } else if (command === "trace" || command === "export") {
        if (!state.active_session_id) throw new Error("session_id_required");
        const data = await new CourseClient(status.baseUrl).replay(state.active_session_id); console.log(JSON.stringify(data, null, 2));
      } else if (command === "quiz" || command === "answer") {
        let active = state.active_session_id;
        if (!active) { active = await sessions.create("学习测验", state.experiment_group, state.course_id); state.active_session_id = active; saveState(state); }
        const payload = command === "answer"
          ? { answer: positionals(parts, ["--item-id"]).join(" "), item_id: flag(parts, "--item-id") }
          : { concept_id: flag(parts, "--concept") || "" };
        const accepted = await commands.send(commands.create(command === "answer" ? "quiz.answer" : "quiz.generate", payload, active));
        console.log(JSON.stringify(accepted.result || {}, null, 2));
      } else if (command === "learner") {
        if (!state.active_session_id) throw new Error("session_id_required");
        console.log(JSON.stringify(await sessions.learner(state.active_session_id, flag(parts, "--concept") || ""), null, 2));
      } else if (command === "ocr") {
        const source = positionals(parts, ["--first-page", "--last-page"])[0];
        if (!source) throw new Error("document_path_required");
        const accepted = await commands.send(commands.create("document.convert", {
          path: resolve(source), first_page: flag(parts, "--first-page") ? Number(flag(parts, "--first-page")) : null,
          last_page: flag(parts, "--last-page") ? Number(flag(parts, "--last-page")) : null,
          force_ocr: has(parts, "--force-ocr"),
        }));
        console.log(JSON.stringify(accepted.result || {}, null, 2));
      } else if (command === "feedback") {
        await writeFeedback(status.baseUrl, state, parts.join(" "));
      } else if (command === "acceptance") {
        await runAcceptance(parts, status.baseUrl, false);
      } else if (command === "report") {
        if (isDeveloperRuntime(runtimeRoot())) await showReport(false);
        else console.log("M1–M3 实验报告与复现数据见 https://github.com/RockeyRoc/DeepProf/releases/tag/v0.6.2");
      } else console.log(paint(`unknown command: /${command}`, "yellow", true));
      } catch (error) {
        const message = errorMessage(error);
        if (message === "turn_cancelled") console.log(paint("本轮已取消，会话已保留。", "yellow", true));
        else console.error(paint(message, "red", true));
      }
    }
  } finally { rl.close(); runtime.stop(); }
}

const options = parseArgs(process.argv.slice(2));
if (options.home) process.env.DEEPPROF_HOME = resolve(options.home);
const requestArgs = process.argv.slice(2);
try {
  if (requestArgs.includes("--version") || options.command === "version") {
    if (options.json) jsonResult("version", { version: "0.6.2" }); else console.log("DeepProf CLI 0.6.2");
  } else if (requestArgs.includes("--help") || options.command === "help") {
    printTopLevelHelp();
  } else if (options.command === "doctor") {
    if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("需要 Node.js 22 或更新版本。升级后重试。");
    prepareRuntime();
    await printDoctor(options.json);
  } else if (options.command === "setup") {
    prepareRuntime(options.args.includes("--ocr"));
    const message = "DeepProf Runtime 已就绪。配置 Provider 后运行 deepprof 开始使用。";
    if (options.json) jsonResult("setup", { status: "ready", user_data: dataHome(), ocr: options.args.includes("--ocr") });
    else console.log(message);
  } else if (!options.command) {
    if (options.json) throw new Error("command_required_when_json");
    if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("需要 Node.js 22 或更新版本。升级后重试。");
    prepareRuntime();
    await repl(options.apiUrl);
  } else {
    if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("需要 Node.js 22 或更新版本。升级后重试。");
    prepareRuntime();
    await runCommand(options);
  }
} catch (error) {
  if (options.json) jsonError(options.command || "repl", error); else { console.error(paint(errorMessage(error), "red", true)); process.exitCode = 1; }
}
