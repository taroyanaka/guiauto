const STATE_KEY = "x_media_url_collector_state";

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function sendToActiveTab(message) {
  const tab = await getActiveTab();
  if (!tab?.id) return null;
  return chrome.tabs.sendMessage(tab.id, message).catch(() => null);
}

async function refreshOutput() {
  const result = await chrome.storage.local.get(STATE_KEY);
  const state = result[STATE_KEY] || {};
  const urls = Array.isArray(state.urls) ? state.urls : [];
  document.getElementById("output").value = urls.join("\n");
  document.getElementById("status").textContent = state.running
    ? `Collecting: ${urls.length} URLs`
    : urls.length
      ? `Done: ${urls.length} URLs`
      : "Idle";
}

document.getElementById("start").addEventListener("click", async () => {
  await sendToActiveTab({ type: "XMU_START" });
  await refreshOutput();
});

document.getElementById("stop").addEventListener("click", async () => {
  await sendToActiveTab({ type: "XMU_STOP" });
  await refreshOutput();
});

document.getElementById("refresh").addEventListener("click", refreshOutput);

document.getElementById("copy").addEventListener("click", async () => {
  const text = document.getElementById("output").value;
  if (!text) return;
  await navigator.clipboard.writeText(text);
  document.getElementById("status").textContent = "Copied URL list";
});

refreshOutput();
window.setInterval(refreshOutput, 1000);
