#!/usr/bin/env node
import * as readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { DpapiSecretStore, secretEnvironmentName } from "../../../packages/client_sdk/node/dpapi_secret_store.js";
import { ProviderClient } from "../../../packages/client_sdk/provider_client.js";
import { createRuntime } from "./runtime.js";
import { buildTree, ask, clients, transcriptText } from "./session_runner.js";
import { loadState, saveState, type CliState } from "./state.js";
import { ansi, errorMessage, jsonError, jsonResult, logo, paint, usageStatus } from "./output.js";

interface Options { json: boolean; apiUrl?: string; command: string; args: string[]; }

function parseArgs(argv: string[]): Options {
  let json = false;
  let apiUrl: string | undefined;
  const rest: string[] = [];
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--json") json = true;
    else if (value === "--api-url") apiUrl = argv[++index];
    else rest.push(value);
  }
  return { json, apiUrl, command: rest[0] || "", args: rest.slice(1) };
}

function flag(args: string[], name: string): string | undefined {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

function has(args: string[], name: string): boolean { return args.includes(name); }

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

async function login(baseUrl: string, state: CliState, args: string[], json: boolean): Promise<void> {
  const providers = new ProviderClient(baseUrl);
  if (has(args, "--api-key")) throw new Error("api_key_command_line_not_supported; use --api-key-stdin or a Secret environment variable");
  const terminal = input.isTTY && !json && !has(args, "--api-key-stdin");
  const rl = readline.createInterface({ input, output });
  try {
    const profileId = flag(args, "--profile") || (terminal ? await rl.question("Profile ID [default]: ") : "default") || "default";
    const displayName = flag(args, "--display-name") || (terminal ? await rl.question("Display name [DeepProf Provider]: ") : "DeepProf Provider") || "DeepProf Provider";
    const base = flag(args, "--base-url") || (terminal ? await rl.question("Base URL [http://127.0.0.1:11434/v1]: ") : "http://127.0.0.1:11434/v1") || "http://127.0.0.1:11434/v1";
    const model = flag(args, "--model") || (terminal ? await rl.question("Model (optional): ") : "");
    rl.close();
    let key = process.env[secretEnvironmentName(`provider:${profileId}`)] || "";
    if (has(args, "--api-key-stdin")) key = (await new Promise<string>((resolve) => { let data = ""; input.setEncoding("utf8"); input.on("data", (chunk) => { data += chunk; }); input.on("end", () => resolve(data.trim())); })).trim();
    else if (terminal) key = await hiddenQuestion("API Key (hidden): ");
    if (!key && !base.startsWith("http://127.0.0.1")) throw new Error("api_key_required_without_local_provider");
    const saved = await providers.upsert({ profile_id: profileId, display_name: displayName, base_url: base, default_model: model, api_key: key || undefined, models: [] });
    if (key && process.platform === "win32") new DpapiSecretStore().set(saved.api_key_ref, key);
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
      persisted_secret: persisted,
      secret_storage: process.platform === "win32" ? "dpapi_current_user" : "environment_only",
      warning: process.platform === "win32" || !key ? null : "credential_not_persisted_on_this_platform",
    };
    if (json) jsonResult("login", data); else console.log(paint(`Connected ${saved.display_name} · ${chosenModel || "manual model"}`, "green", true));
    if (!json && data.warning) console.log(paint("Credential is process-only on this platform; use DEEPPROF_SECRET_* for future runs.", "yellow", true));
  } finally { rl.close(); }
}

async function runCommand(options: Options): Promise<void> {
  const runtime = createRuntime(options.apiUrl);
  const status = await runtime.start();
  if (!status.baseUrl) throw new Error("runtime_unavailable");
  const state = loadState();
  const { sessions } = clients(status.baseUrl, state.learner_id);
  const provider = new ProviderClient(status.baseUrl);
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
      const selection = await provider.defaultSelection();
      const result = await provider.probe(selection.profile_id, selection.model || undefined);
      if (options.json) jsonResult(command, { selection, result }); else console.log(`${selection.profile_id}/${selection.model}: ${String(result.status || "unknown")}`);
      return;
    }
    if (command === "new") {
      const title = flag(options.args, "--title") || options.args.filter((item) => !item.startsWith("--")).join(" ");
      const id = await sessions.create(title || "学习会话");
      saveState({ ...state, active_session_id: id });
      return options.json ? jsonResult(command, {}, id) : console.log(id);
    }
    if (command === "tree") {
      const tree = buildTree(await sessions.list(state.learner_id));
      return options.json ? jsonResult(command, { tree }, state.active_session_id) : printTree(tree);
    }
    const sessionId = options.args[0] && !options.args[0].startsWith("--") ? options.args[0] : state.active_session_id;
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
      const content = options.args.filter((item) => !item.startsWith("--") && item !== sessionId).join(" ").trim();
      let active = sessionId;
      if (!active) { active = await sessions.create("学习会话"); saveState({ ...state, active_session_id: active }); }
      if (!content) throw new Error("question_required");
      const result = await ask(status.baseUrl, state.learner_id, active, content, options.json ? undefined : (text) => process.stdout.write(text));
      if (options.json) jsonResult(command, { answer: result.answer, usage: result.usage, provider_profile: result.provider_profile, model: result.model, sequence: result.last_sequence, last_sequence: result.last_sequence }, active);
      else { process.stdout.write("\n"); saveState({ ...state, active_session_id: active }); }
      return;
    }
    throw new Error(`unknown_command:${command}`);
  } finally {
    runtime.stop();
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
  const { sessions } = clients(status.baseUrl, state.learner_id);
  const rl = readline.createInterface({ input, output, terminal: true });
  const cumulativeUsage: Record<string, unknown> = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  console.log(logo(true));
  console.log(paint("/help for commands; Ctrl+C or /quit to exit", "dim", true));
  if (state.active_session_id) console.log(paint(`session ${state.active_session_id}`, "dim", true));
  try {
    while (true) {
      const line = (await rl.question(`${ansi.cyan}you › ${ansi.reset}`)).trim();
      if (!line) continue;
      if (["/quit", "/exit", "quit", "exit"].includes(line.toLowerCase())) break;
      const parts = line.startsWith("/") ? line.slice(1).split(/\s+/) : ["ask", line];
      const command = parts.shift() || "";
      if (command === "help") { console.log("/login /new /ask /resume /tree /fork /compact /models /doctor /quit"); continue; }
      if (command === "ask") {
        let active = state.active_session_id;
        if (!active) { active = await sessions.create("学习会话"); state.active_session_id = active; saveState(state); }
        const result = await ask(status.baseUrl, state.learner_id, active, parts.join(" "), (text) => process.stdout.write(text));
        for (const key of ["prompt_tokens", "completion_tokens", "total_tokens"]) {
          const current = typeof cumulativeUsage[key] === "number" ? cumulativeUsage[key] as number : 0;
          const next = typeof result.usage[key] === "number" ? result.usage[key] as number : 0;
          cumulativeUsage[key] = current + next;
          result.usage[key] = cumulativeUsage[key];
        }
        const summary = await sessions.get(active);
        console.log(`\n${paint(`${usageStatus(result.provider_profile, result.model, result.usage)} · ${summary.message_count} messages · sequence ${result.last_sequence}`, "dim", true)}`);
      } else if (command === "new") {
        state.active_session_id = await sessions.create((flag(parts, "--title") || parts.filter((item) => !item.startsWith("--")).join(" ")) || "学习会话"); saveState(state); console.log(state.active_session_id);
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
        await login(status.baseUrl, state, parts, false);
      } else console.log(paint(`unknown command: /${command}`, "yellow", true));
    }
  } finally { rl.close(); runtime.stop(); }
}

const options = parseArgs(process.argv.slice(2));
try {
  if (!options.command) {
    if (options.json) throw new Error("command_required_when_json");
    await repl(options.apiUrl);
  }
  else await runCommand(options);
} catch (error) {
  if (options.json) jsonError(options.command || "repl", error); else { console.error(paint(errorMessage(error), "red", true)); process.exitCode = 1; }
}
