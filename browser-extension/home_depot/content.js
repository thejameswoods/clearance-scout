// Bridges the page's main-world JS (Playwright's page.evaluate() always
// runs there) to this content script's own execution context, which is
// what actually performs the fetch(). Original code -- not a fork of any
// existing scanner extension; see manifest.json.
//
// Why this exists at all: Playwright driving `context.request.post()` and
// even a genuine in-page `fetch()` via page.evaluate() both got a generic
// degraded response from Home Depot's real API (see docs/architecture.md
// for the full debugging trail) -- a real, installed content script is a
// different execution context than either of those, and is the one thing
// not yet tried. This file's only job is to be that different context; all
// scheduling, filtering, storage, and alerting logic stays in the existing
// Python backend, not reimplemented here.

window.addEventListener("message", async (event) => {
  if (event.source !== window) return;
  const msg = event.data;
  if (!msg || msg.type !== "CS_SCOUT_GRAPHQL_REQUEST") return;

  const { requestId, url, body } = msg;
  try {
    const response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "x-experience-name": "general-merchandise",
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    window.postMessage(
      { type: "CS_SCOUT_GRAPHQL_RESPONSE", requestId, status: response.status, body: text },
      "*"
    );
  } catch (e) {
    window.postMessage(
      { type: "CS_SCOUT_GRAPHQL_RESPONSE", requestId, status: 0, body: null, error: String(e) },
      "*"
    );
  }
});

window.postMessage({ type: "CS_SCOUT_CONTENT_SCRIPT_READY" }, "*");
