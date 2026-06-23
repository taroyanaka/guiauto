(() => {
  const STATE_KEY = "x_media_url_collector_state";
  const LOG_PREFIX = "[XMU]";
  const state = {
    running: false,
    stopRequested: false,
    urls: [],
    seen: new Set(),
    lastSignature: "",
    stallCount: 0
  };

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || typeof message !== "object") return;

    if (message.type === "XMU_START") {
      if (state.running) {
        sendResponse({ ok: true, alreadyRunning: true });
        return;
      }
      void startSession()
        .then((result) => sendResponse({ ok: true, ...result }))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message.type === "XMU_STOP") {
      state.stopRequested = true;
      sendResponse({ ok: true });
      return;
    }

    if (message.type === "XMU_GET_STATE") {
      sendResponse({
        ok: true,
        running: state.running,
        urls: [...state.urls],
        stallCount: state.stallCount,
        lastSignature: state.lastSignature
      });
      return;
    }
  });

  async function startSession() {
    state.running = true;
    state.stopRequested = false;
    state.urls = [];
    state.seen = new Set();
    state.lastSignature = "";
    state.stallCount = 0;

    try {
      await sleep(250);
      for (let index = 0; index < 300 && !state.stopRequested; index += 1) {
        await performKeyCycle();
        await sleep(600);

        const currentUrl = location.href;
        const signature = currentUrl;
        if (signature === state.lastSignature) {
          state.stallCount += 1;
        } else {
          state.stallCount = 0;
          state.lastSignature = signature;
        }

        if (isCollectableUrl(currentUrl) && !state.seen.has(currentUrl)) {
          state.seen.add(currentUrl);
          state.urls.push(currentUrl);
          persistState();
        }

        if (shouldStop(currentUrl)) {
          break;
        }
      }

      persistState();
      return { urls: [...state.urls], stopped: state.stopRequested || shouldStop(location.href) };
    } finally {
      state.running = false;
      state.stopRequested = false;
    }
  }

  async function performKeyCycle() {
    dispatchKey("Escape", { keyCode: 27, code: "Escape" });
    await sleep(80);
    dispatchKey("Tab", { keyCode: 9, code: "Tab", shiftKey: true });
    await sleep(80);
    dispatchKey("Enter", { keyCode: 13, code: "Enter" });
  }

  function dispatchKey(key, { keyCode, code, shiftKey = false }) {
    const options = {
      key,
      code,
      keyCode,
      which: keyCode,
      bubbles: true,
      cancelable: true,
      shiftKey
    };
    const down = new KeyboardEvent("keydown", options);
    const press = new KeyboardEvent("keypress", options);
    const up = new KeyboardEvent("keyup", options);
    document.dispatchEvent(down);
    document.dispatchEvent(press);
    document.dispatchEvent(up);
    window.dispatchEvent(down);
    window.dispatchEvent(press);
    window.dispatchEvent(up);
  }

  function shouldStop(currentUrl) {
    if (!currentUrl.includes("/media")) return false;
    return state.stallCount >= 2;
  }

  function isCollectableUrl(url) {
    if (!url) return false;
    return /https:\/\/x\.com\/[^/]+\/status\/\d+(?:\/photo\/\d+)?/i.test(url) || /https:\/\/x\.com\/[^/]+\/media/i.test(url);
  }

  function persistState() {
    chrome.storage.local.set({
      [STATE_KEY]: {
        running: state.running,
        urls: state.urls,
        lastSignature: state.lastSignature,
        stallCount: state.stallCount
      }
    });
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
})();
