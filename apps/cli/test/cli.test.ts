import test from "node:test";
import assert from "node:assert/strict";
import { buildTree, transcriptText } from "../src/session_runner.js";
import { EventClient } from "../../../packages/client_sdk/event_client.js";
import { isLoopbackUrl } from "../../../packages/client_sdk/node/gateway_discovery.js";

test("buildTree groups forked sessions and transcript is deterministic", () => {
  const roots = buildTree([
    { session_id: "root", learner_id: "local", title: "Root", parent_id: null, created_at: "", updated_at: "", last_sequence: 0, message_count: 0 },
    { session_id: "child", learner_id: "local", title: "Child", parent_id: "root", created_at: "", updated_at: "", last_sequence: 0, message_count: 0 },
  ]);
  assert.equal(roots.length, 1);
  assert.equal(roots[0].children[0].session_id, "child");
  assert.equal(transcriptText([{ index: 0, role: "user", content: "hi" }]), "user: hi");
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
