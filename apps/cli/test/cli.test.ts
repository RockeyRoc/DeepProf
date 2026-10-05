import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildTree, transcriptText, turnFailureError } from "../src/session_runner.js";
import { errorMessage, jsonError, usageStatus } from "../src/output.js";
import { EventClient } from "../../../packages/client_sdk/event_client.js";
import { CommandBus } from "../../../packages/client_sdk/command_bus.js";
import { SessionClient } from "../../../packages/client_sdk/session_client.js";
import { isLoopbackUrl } from "../../../packages/client_sdk/node/gateway_discovery.js";
import { newSessionOptions } from "../src/session_options.js";
import { DpapiSecretStore, loadRuntimeSecrets, secretEnvironmentName } from "../../../packages/client_sdk/node/dpapi_secret_store.js";

test("buildTree groups forked sessions and transcript is deterministic", () => {
  const roots = buildTree([
    { session_id: "root", learner_id: "local", title: "Root", parent_id: null, created_at: "", updated_at: "", last_sequence: 0, message_count: 0 },
    { session_id: "child", learner_id: "local", title: "Child", parent_id: "root", created_at: "", updated_at: "", last_sequence: 0, message_count: 0 },
  ]);
  assert.equal(roots.length, 1);
  assert.equal(roots[0].children[0].session_id, "child");
  assert.equal(transcriptText([{ index: 0, role: "user", content: "hi" }]), "user: hi");
  assert.equal(transcriptText([{ index: 0, role: "assistant", content: "你好", metadata: { turn_mode: "chat" } }]),
    "[常规对话] assistant: 你好");
  assert.equal(transcriptText([{ index: 1, role: "assistant", content: "试着想想", metadata: { turn_mode: "study" } }]),
    "[教学回合] assistant: 试着想想");
  assert.equal(transcriptText([{ index: 2, role: "assistant", content: "答案", metadata: { turn_mode: "chat", reasoning_content: "推理" } }]),
    "[常规对话] assistant: 答案\n思考:\n推理");
});

test("EventClient deduplicates and isolates session cursors", () => {
  const seen: number[] = [];
  const client = new EventClient({ baseUrl: "http://127.0.0.1:1", onEvent: (event) => seen.push(event.sequence) });
  client.connect("s1", 2);
  client.accept(JSON.stringify({ session_id: "s1", sequence: 2, type: "x" }));
  client.accept(JSON.stringify({ session_id: "s1", sequence: 3, type: "x" }));
  client.accept(JSON.stringify({ session_id: "s2", sequence: 99, type: "x" }));
  assert.deepEqual(seen, [3]);
  assert.equal(client.lastSequence, 3);
  client.close();
});

test("gateway discovery rejects non-loopback URLs", () => {
  assert.equal(isLoopbackUrl("http://127.0.0.1:9000"), true);
  assert.equal(isLoopbackUrl("http://0.0.0.0:9000"), false);
  assert.equal(isLoopbackUrl("https://example.com"), false);
});

test("SessionClient sends explicit chat and study modes without leaking study fields into chat", async () => {
  const originalFetch = globalThis.fetch;
  const sent: Array<Record<string, unknown>> = [];
  globalThis.fetch = async (_input, init) => {
    sent.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
    return new Response(JSON.stringify({ session_id: `ses_${sent.length}` }), { status: 200 });
  };
  try {
    const sessions = new SessionClient("http://127.0.0.1", new CommandBus("http://127.0.0.1", "cli", "cli"));
    await sessions.create("常规对话", "B", "ds.c_language.v1", "chat");
    await sessions.create("学习会话", "C", "ds.c_language.v1", "study");

    const chat = sent[0].payload as Record<string, unknown>;
    const study = sent[1].payload as Record<string, unknown>;
    assert.equal(chat.session_mode, "chat");
    assert.equal("group" in chat, false);
    assert.equal("course_id" in chat, false);
    assert.equal(study.session_mode, "study");
    assert.equal(study.group, "C");
    assert.equal(study.course_id, "ds.c_language.v1");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("CLI new-session defaults are chat and B-group study regardless of remembered group", () => {
  assert.deepEqual(newSessionOptions(), { sessionMode: "chat", group: "B" });
  assert.deepEqual(newSessionOptions("study"), { sessionMode: "study", group: "B" });
  assert.deepEqual(newSessionOptions(undefined, "C"), { sessionMode: "study", group: "C" });
  assert.throws(() => newSessionOptions("chat", "A"), /experiment_group_requires_study_mode/);
  assert.throws(() => newSessionOptions("invalid"), /mode_must_be_chat_or_study/);
});

test("web clients can label Gateway commands with the web surface", () => {
  const web = new CommandBus("http://127.0.0.1", "web", "web");
  assert.equal(web.create("session.new", { session_mode: "chat" }).surface, "web");
});

test("unknown model pricing is shown as N/A rather than a fabricated zero", () => {
  assert.match(usageStatus(null, null, { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 }),
    /unknown\/unknown · 16 tokens · cost N\/A/);
});

test("CLI turn failures preserve provider codes and details in JSON", () => {
  const error = turnFailureError({
    code: "provider_error",
    message: "model request failed",
    details: { kind: "timeout", retryable: true, profile_id: "ollama" },
  });
  assert.equal((error as Error & { code?: string }).code, "provider_error");
  assert.deepEqual((error as Error & { details?: Record<string, unknown> }).details,
    { kind: "timeout", retryable: true, profile_id: "ollama" });
  assert.equal(errorMessage(error), "model request failed (timeout)");

  const originalWrite = process.stdout.write;
  let output = "";
  process.stdout.write = ((chunk: string | Uint8Array) => {
    output += typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8");
    return true;
  }) as typeof process.stdout.write;
  try { jsonError("ask", error); }
  finally { process.stdout.write = originalWrite; }
  assert.deepEqual(JSON.parse(output), { ok: false, command: "ask", session_id: null,
    error: { code: "provider_error", message: "model request failed",
      details: { kind: "timeout", retryable: true, profile_id: "ollama" } } });
});

test("Windows current-user credentials survive restart and restore into the Runtime environment", { skip: process.platform !== "win32" }, () => {
  const previousHome = process.env.DEEPPROF_HOME;
  const home = mkdtempSync(join(tmpdir(), "DeepProf 凭据 恢复 "));
  const ref = "dpapi-smoke-profile";
  const value = "not-a-real-provider-credential";
  try {
    process.env.DEEPPROF_HOME = home;
    const credentialFile = join(home, "credentials", "dpapi.json");
    new DpapiSecretStore(credentialFile).set(ref, value);
    assert.equal(new DpapiSecretStore(credentialFile).get(ref), value);
    writeFileSync(join(home, "providers.json"), JSON.stringify({ profiles: [{ api_key_ref: ref }] }));
    assert.equal(loadRuntimeSecrets()[secretEnvironmentName(ref)], value);
  } finally {
    if (previousHome === undefined) delete process.env.DEEPPROF_HOME;
    else process.env.DEEPPROF_HOME = previousHome;
    rmSync(home, { recursive: true, force: true });
  }
});
