const STORAGE_KEY = 'recordedSteps';
const RECORDING_KEY = 'recordingEnabled';
const EXTRA_KEY = 'extraInstruction';

const elements = {
  stepCount: document.getElementById('stepCount'),
  steps: document.getElementById('steps'),
  status: document.getElementById('status'),
  recordingToggle: document.getElementById('recordingToggle'),
  extraInstruction: document.getElementById('extraInstruction'),
  finalPrompt: document.getElementById('finalPrompt'),
  sendToGemini: document.getElementById('sendToGemini'),
  refresh: document.getElementById('refresh'),
  clearSteps: document.getElementById('clearSteps'),
  executionPayload: document.getElementById('executionPayload'),
  runPayload: document.getElementById('runPayload'),
  loadExample: document.getElementById('loadExample')
};

init();

async function init() {
  await loadState();
  bindEvents();
  await render();
}

function bindEvents() {
  elements.refresh.addEventListener('click', () => render());

  elements.recordingToggle.addEventListener('change', async () => {
    await chrome.storage.local.set({ [RECORDING_KEY]: elements.recordingToggle.checked });
    setStatus(elements.recordingToggle.checked ? '記録モードをONにしました。' : '記録モードをOFFにしました。');
  });

  elements.extraInstruction.addEventListener('input', async () => {
    await chrome.storage.local.set({ [EXTRA_KEY]: elements.extraInstruction.value });
    await updatePrompt();
  });

  elements.sendToGemini.addEventListener('click', sendPromptToGemini);
  elements.clearSteps.addEventListener('click', clearSteps);
  elements.runPayload.addEventListener('click', runPayload);
  elements.loadExample.addEventListener('click', loadExamplePayload);

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'local') return;

    if (changes[STORAGE_KEY] || changes[EXTRA_KEY] || changes[RECORDING_KEY]) {
      render();
    }
  });
}

async function loadState() {
  const data = await chrome.storage.local.get([STORAGE_KEY, RECORDING_KEY, EXTRA_KEY]);
  elements.recordingToggle.checked = data[RECORDING_KEY] !== false;
  elements.extraInstruction.value = data[EXTRA_KEY] || '';
}

async function render() {
  const data = await chrome.storage.local.get([STORAGE_KEY, RECORDING_KEY, EXTRA_KEY]);
  const steps = Array.isArray(data[STORAGE_KEY]) ? data[STORAGE_KEY] : [];

  elements.recordingToggle.checked = data[RECORDING_KEY] !== false;
  elements.extraInstruction.value = data[EXTRA_KEY] || elements.extraInstruction.value || '';

  renderSteps(steps);
  await updatePrompt(steps, elements.extraInstruction.value);
}

function renderSteps(steps) {
  elements.stepCount.textContent = `${steps.length} steps`;
  elements.steps.innerHTML = '';

  if (!steps.length) {
    const empty = document.createElement('div');
    empty.className = 'step';
    empty.textContent = 'まだ記録がありません。';
    elements.steps.appendChild(empty);
    return;
  }

  const items = [...steps].reverse().map((step, index) => {
    const wrap = document.createElement('div');
    wrap.className = 'step';

    const head = document.createElement('div');
    head.className = 'step-head';
    head.innerHTML = `<span>#${steps.length - index}</span><span>${escapeHtml(step.action || '')}</span>`;

    const code = document.createElement('code');
    code.textContent = JSON.stringify(
      {
        action: step.action,
        selector: step.selector,
        value: step.value,
        url: step.url,
        timestamp: step.timestamp
      },
      null,
      2
    );

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = JSON.stringify(step.meta || {}, null, 2);

    wrap.append(head, code, meta);
    return wrap;
  });

  for (const item of items) {
    elements.steps.appendChild(item);
  }
}

async function updatePrompt(stepsArg, extraArg) {
  const data = await chrome.storage.local.get([STORAGE_KEY, EXTRA_KEY]);
  const steps = Array.isArray(stepsArg) ? stepsArg : Array.isArray(data[STORAGE_KEY]) ? data[STORAGE_KEY] : [];
  const extra = typeof extraArg === 'string' ? extraArg : data[EXTRA_KEY] || '';
  const prompt = buildPrompt(steps, extra);
  elements.finalPrompt.value = prompt;
  return prompt;
}

function buildPrompt(steps, extraInstruction) {
  const logJson = JSON.stringify(
    steps.map(({ action, selector, value, url, timestamp, meta }) => ({
      action,
      selector,
      value,
      url,
      timestamp,
      meta
    })),
    null,
    2
  );

  return `あなたは優秀なブラウザ自動化AIです。以下の【操作ログ】と【追加指示】を元に、この自動化を再現・拡張するためのJavaScriptコードを生成してください。
出力は、Markdownのコードブロック（\`\`\`javascript ~ \`\`\`）の中に、即時実行可能な関数（非同期処理 async/await や待機処理 setTimeout を含む）の形で出力してください。

【操作ログ（JSON）】
${logJson}

【ユーザーからの追加指示】
${extraInstruction || '(なし)'}
`;
}

async function sendPromptToGemini() {
  const prompt = await updatePrompt();
  setStatus('Geminiを開いてプロンプトを送信しています...');

  const response = await chrome.runtime.sendMessage({
    type: 'SEND_TO_WEB_GEMINI',
    prompt
  });

  if (!response?.ok) {
    setStatus(`送信に失敗しました: ${response?.error || 'unknown error'}`);
    return;
  }

  setStatus(`Geminiタブを開きました。${response.injection?.autoSent ? '自動送信しました。' : '手動送信してください。'}`);
}

async function clearSteps() {
  await chrome.storage.local.set({ [STORAGE_KEY]: [] });
  setStatus('記録を消去しました。');
  await render();
}

async function runPayload() {
  const payload = elements.executionPayload.value.trim();
  if (!payload) {
    setStatus('実行する内容を貼り付けてください。');
    return;
  }

  const tabId = await getActiveTabId();
  if (!tabId) {
    setStatus('対象タブを取得できませんでした。');
    return;
  }

  const parsed = parsePayload(payload);
  if (parsed.kind === 'json') {
    const response = await chrome.runtime.sendMessage({
      type: 'EXECUTE_ON_TAB',
      tabId,
      commands: parsed.value
    });

    if (!response?.ok) {
      setStatus(`JSON実行に失敗しました: ${response?.error || 'unknown error'}`);
      return;
    }

    setStatus('JSONコマンドを実行しました。');
    return;
  }

  const response = await chrome.runtime.sendMessage({
    type: 'EXECUTE_CODE_ON_TAB',
    tabId,
    code: parsed.value
  });

  if (!response?.ok) {
    setStatus(`コード実行に失敗しました: ${response?.error || 'unknown error'}`);
    return;
  }

  setStatus('JavaScriptコードを実行しました。');
}

async function loadExamplePayload() {
  elements.executionPayload.value = JSON.stringify(
    [
      { action: 'click', selector: 'button[aria-label*="送信"]' },
      { action: 'wait', ms: 1000 },
      { action: 'input', selector: 'input[name="query"]', value: 'hello' }
    ],
    null,
    2
  );
  setStatus('JSON例を挿入しました。');
}

function parsePayload(text) {
  const trimmed = text.trim();
  const fenced = trimmed.match(/```(?:json|javascript|js)?\s*([\s\S]*?)```/i);
  const inner = (fenced ? fenced[1] : trimmed).trim();

  try {
    const value = JSON.parse(inner);
    if (Array.isArray(value)) {
      return { kind: 'json', value };
    }
    if (value && typeof value === 'object' && value.action && value.selector) {
      return { kind: 'json', value: [value] };
    }
    if (value && Array.isArray(value.commands)) {
      return { kind: 'json', value: value.commands };
    }
  } catch {
    // not JSON, continue as code
  }

  return { kind: 'code', value: inner };
}

async function getActiveTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id ?? null;
}

function setStatus(message) {
  elements.status.textContent = message;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
