import test from "node:test";
import assert from "node:assert/strict";
import { buildTree, transcriptText } from "../src/session_runner.js";
import { EventClient } from "../../../packages/client_sdk/event_client.js";
import { CommandBus } from "../../../packages/client_sdk/command_bus.js";
import { SessionClient } from "../../../packages/client_sdk/session_client.js";
import { isLoopbackUrl } from "../../../packages/client_sdk/node/gateway_discovery.js";
import { newSessionOptions } from "../src/session_options.js";

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
