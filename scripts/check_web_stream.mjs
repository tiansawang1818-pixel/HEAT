import assert from "node:assert/strict";

// Read-only smoke check, also run inside the release web container in CI.
const origin = process.argv[2] || "http://127.0.0.1:3000";
const controller = new AbortController();
const deadline = setTimeout(() => controller.abort(), 7000);
try {
  const response = await fetch(new URL("/api/events/stream", origin), {
    headers: { "Accept-Encoding": "gzip, deflate" }, signal: controller.signal,
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("Content-Type") || "", /^text\/event-stream/);
  assert.match(response.headers.get("Cache-Control") || "", /no-transform/);
  assert.equal(response.headers.get("Content-Encoding"), null, "SSE must not be buffered by gzip");
  const reader = response.body.getReader();
  let initial = "";
  while (!initial.includes(": connected")) {
    const { done, value } = await reader.read();
    assert.equal(done, false, "Stream ended before the initial frame");
    initial += new TextDecoder().decode(value);
    assert.ok(initial.length < 16_384, "Unexpected initial SSE payload");
  }
  assert.ok(initial.includes("retry: 5000"));
  await reader.cancel();
  console.log("SSE initial frame arrived without compression through the web proxy.");
} finally {
  clearTimeout(deadline);
  controller.abort();
}
