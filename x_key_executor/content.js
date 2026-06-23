(() => {
  const STATE_KEY = "x_key_executor_state";
  const state = {
    running: false,
    stopRequested: false,
    urls: [],
    seen: new Set(),
    lastUrl: "",
    stallCount: 0
  };

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || typeof message !== "object") return;

    if (message.type === "XKEY_START") {
      if (state.running) {
        sendResponse({ ok: true, alreadyRunning: true });
        return;
      }
      void startSession()
        .then((result) => sendResponse({ ok: true, ...result }))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message.type === "XKEY_STOP") {
      state.stopRequested = true;
      sendResponse({ ok: true });
      return;
    }

    if (message.type === "XKEY_GET_STATE") {
      sendResponse({ ok: true, running: state.running, urls: [...state.urls] });
    }
  });

  async function startSession() {
    state.running = true;
    state.stopRequested = false;
    state.urls = [];
    state.seen = new Set();
    state.lastUrl = "";
    state.stallCount = 0;

    try {
      for (let i = 0; i < 300 && !state.stopRequested; i += 1) {
        await keyCycle();
        await sleep(700);

        const url = location.href;
        if (isCollectable(url) && !state.seen.has(url)) {
          state.seen.add(url);
          state.urls.push(url);
          persist();
        }

        if (url === state.lastUrl) {
          state.stallCount += 1;
        } else {
          state.lastUrl = url;
          state.stallCount = 0;
        }

        if (url.includes("/media") && state.stallCount >= 2) {
          break;
        }
      }

      persist();
      return { urls: [...state.urls], stopped: state.stopRequested };
    } finally {
      state.running = false;
      state.stopRequested = false;
    }
  }

  async function keyCycle() {
    press("Escape", { code: "Escape", keyCode: 27 });
    await sleep(80);
    press("Tab", { code: "Tab", keyCode: 9, shiftKey: true });
    await sleep(80);
    press("Enter", { code: "Enter", keyCode: 13 });
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

  function persist() {
    chrome.storage.local.set({
      [STATE_KEY]: {
        running: state.running,
        urls: state.urls
      }
    });
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
})();
