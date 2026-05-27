const GEMINI_URL = 'https://gemini.google.com/app';

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'SEND_TO_WEB_GEMINI') {
    openGeminiAndInjectPrompt(message.prompt)
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === 'GET_ACTIVE_TAB_ID') {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      sendResponse({ tabId: tabs?.[0]?.id ?? null });
    });
    return true;
  }

  if (message?.type === 'EXECUTE_ON_TAB') {
    relayExecuteCommandToTab(message.tabId, message.commands)
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === 'EXECUTE_CODE_ON_TAB') {
    relayExecuteCodeToTab(message.tabId, message.code)
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  return false;
});

async function openGeminiAndInjectPrompt(prompt) {
  const tab = await chrome.tabs.create({ url: GEMINI_URL, active: true });
  if (!tab?.id) {
    throw new Error('Geminiタブの作成に失敗しました。');
  }

  await waitForTabComplete(tab.id);

  const [execResult] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    args: [prompt],
    func: async (promptText) => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const findEditor = () =>
        document.querySelector('div[contenteditable="true"]') ||
        Array.from(document.querySelectorAll('[contenteditable="true"]')).find((el) => {
          const role = (el.getAttribute('role') || '').toLowerCase();
          return role === 'textbox' || role === 'main' || role === '';
        });

      let editor = findEditor();
      for (let i = 0; i < 20 && !editor; i += 1) {
        await wait(500);
        editor = findEditor();
      }

      if (!editor) {
        return { success: false, reason: '入力欄が見つかりませんでした。' };
      }

      editor.focus();
      editor.innerText = promptText;
      editor.dispatchEvent(
        new InputEvent('input', {
          bubbles: true,
          cancelable: true,
          inputType: 'insertText',
          data: promptText
        })
      );
      editor.dispatchEvent(new Event('change', { bubbles: true }));

      const findSendButton = () =>
        Array.from(document.querySelectorAll('button')).find((button) => {
          const aria = (button.getAttribute('aria-label') || '').toLowerCase();
          const text = (button.textContent || '').toLowerCase();
          return aria.includes('送信') || aria.includes('send') || text.includes('送信') || text.includes('send');
        });

      let sendButton = findSendButton();
      for (let i = 0; i < 10 && !sendButton; i += 1) {
        await wait(300);
        sendButton = findSendButton();
      }

      if (sendButton) {
        sendButton.click();
        return { success: true, autoSent: true };
      }

      alert('プロンプトを入力しました。送信ボタンが見つからないため、手動で送信してください。');
      return { success: true, autoSent: false };
    }
  });

  return { tabId: tab.id, injection: execResult?.result ?? { success: false } };
}

function waitForTabComplete(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error('Geminiタブの読み込みがタイムアウトしました。'));
    }, 30000);

    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };

    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function relayExecuteCommandToTab(tabId, commands) {
  if (!tabId) {
    throw new Error('対象タブIDが指定されていません。');
  }

  const normalized = Array.isArray(commands) ? commands : [commands];
  const response = await chrome.tabs.sendMessage(tabId, {
    type: 'EXECUTE_COMMANDS',
    commands: normalized
  });

  return { execution: response };
}

async function relayExecuteCodeToTab(tabId, code) {
  if (!tabId) {
    throw new Error('対象タブIDが指定されていません。');
  }

  if (typeof code !== 'string' || !code.trim()) {
    throw new Error('実行コードが空です。');
  }

  const response = await chrome.tabs.sendMessage(tabId, {
    type: 'EXECUTE_CODE',
    code
  });

  return { execution: response };
}
