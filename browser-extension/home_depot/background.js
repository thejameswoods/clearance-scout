// Minimal MV3 service worker. The real work happens in content.js (the
// actual API-call bridge) and the existing Python backend (scheduling,
// filtering, storage, alerts) -- this file exists because Manifest V3
// requires a declared background worker, not because much logic belongs
// here. Original code, not a fork of any existing scanner extension.

chrome.runtime.onInstalled.addListener(() => {
  console.log("[Clearance Scout bridge] installed");
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "CS_SCOUT_PING") {
    sendResponse({ ok: true });
  }
  return false;
});
