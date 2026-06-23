const STATE_KEY = "x_key_executor_state";

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function runOnActiveTab(func, args = []) {
  const tab = await getActiveTab();
  if (!tab?.id) return null;
  return chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func,
    args
  });
}

async function refresh() {
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
  document.getElementById("status").textContent = "Preparing...";
  await activateAndDelay(3000);
  await runOnActiveTab(startSession);
  await refresh();
});

document.getElementById("stop").addEventListener("click", async () => {
  await runOnActiveTab(stopSession);
  await refresh();
});

async function startSession() {
  const STATE_KEY = "x_key_executor_state";
  const state = window.__xKeyExecutorState || {
    running: false,
    stopRequested: false,
    urls: [],
    seen: [],
    lastUrl: "",
    stallCount: 0
  };
  if (state.running) return;
  state.running = true;
  state.stopRequested = false;
  state.urls = [];
  state.seen = [];
  state.lastUrl = "";
  state.stallCount = 0;
  window.__xKeyExecutorState = state;

  try {
    for (let i = 0; i < 300 && !state.stopRequested; i += 1) {
      press("Escape", { code: "Escape", keyCode: 27 });
      await sleep(80);
      press("Tab", { code: "Tab", keyCode: 9, shiftKey: true });
      await sleep(80);
      press("Enter", { code: "Enter", keyCode: 13 });
      await sleep(700);

      const url = location.href;
      if (isCollectable(url) && !state.seen.includes(url)) {
        state.seen.push(url);
        state.urls.push(url);
      }

      if (url === state.lastUrl) {
        state.stallCount += 1;
      } else {
        state.lastUrl = url;
        state.stallCount = 0;
      }

      chrome.storage.local.set({
        [STATE_KEY]: {
          running: true,
          urls: state.urls
        }
      });

      if (url.includes("/media") && state.stallCount >= 2) {
        break;
      }
    }
  } finally {
    state.running = false;
    chrome.storage.local.set({
      [STATE_KEY]: {
        running: false,
        urls: state.urls
      }
    });
  }
}

function stopSession() {
  const state = window.__xKeyExecutorState;
  if (state) state.stopRequested = true;
  chrome.storage.local.get(STATE_KEY, (result) => {
    const current = result[STATE_KEY] || {};
    chrome.storage.local.set({
      [STATE_KEY]: {
        running: false,
        urls: Array.isArray(current.urls) ? current.urls : []
      }
    });
  });
}

function press(key, opts) {
  const eventInit = {
    key,
    code: opts.code,
    keyCode: opts.keyCode,
    which: opts.keyCode,
    bubbles: true,
    cancelable: true,
    shiftKey: Boolean(opts.shiftKey)
  };
  document.dispatchEvent(new KeyboardEvent("keydown", eventInit));
  document.dispatchEvent(new KeyboardEvent("keypress", eventInit));
  document.dispatchEvent(new KeyboardEvent("keyup", eventInit));
}

function isCollectable(url) {
  return /https:\/\/x\.com\/[^/]+\/status\/\d+(?:\/photo\/\d+)?/i.test(url) || /https:\/\/x\.com\/[^/]+\/media/i.test(url);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

refresh();
window.setInterval(refresh, 1000);

async function activateAndDelay(ms) {
  const tab = await getActiveTab();
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { active: true });
    if (tab.windowId != null) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  }
  await new Promise((resolve) => setTimeout(resolve, ms));
}
