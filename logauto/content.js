const STORAGE_KEY = 'recordedSteps';
const RECORDING_KEY = 'recordingEnabled';

let recordingEnabled = true;
let replayInProgress = false;

init();

async function init() {
  const stored = await chrome.storage.local.get([RECORDING_KEY]);
  recordingEnabled = stored[RECORDING_KEY] !== false;
}

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === 'local' && changes[RECORDING_KEY]) {
    recordingEnabled = changes[RECORDING_KEY].newValue !== false;
  }
});

document.addEventListener(
  'click',
  async (event) => {
    if (!shouldRecord()) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    await appendStep({
      action: 'click',
      selector: getUniqueSelector(target),
      meta: summarizeElement(target)
    });
  },
  true
);

document.addEventListener(
  'input',
  async (event) => {
    if (!shouldRecord()) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    const value = readValue(target);
    if (shouldRedact(target)) {
      await appendStep({
        action: 'input',
        selector: getUniqueSelector(target),
        value: '[REDACTED]',
        meta: summarizeElement(target, { redacted: true })
      });
      return;
    }

    await appendStep({
      action: 'input',
      selector: getUniqueSelector(target),
      value,
      meta: summarizeElement(target)
    });
  },
  true
);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'EXECUTE_COMMANDS') {
    executeCommands(message.commands)
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === 'EXECUTE_CODE') {
    executeCode(message.code)
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  return false;
});

function shouldRecord() {
  return recordingEnabled && !replayInProgress;
}

async function appendStep(step) {
  const current = await chrome.storage.local.get([STORAGE_KEY]);
  const steps = current[STORAGE_KEY] || [];

  steps.push({
    ...step,
    url: location.href,
    timestamp: new Date().toISOString()
  });

  if (steps.length > 300) {
    steps.splice(0, steps.length - 300);
  }

  await chrome.storage.local.set({ [STORAGE_KEY]: steps });
}

function summarizeElement(element, extra = {}) {
  const attrs = ['id', 'class', 'placeholder', 'value', 'name', 'href', 'type', 'role'];
  const ariaAttrs = Array.from(element.attributes)
    .filter((attr) => attr.name.startsWith('aria-'))
    .reduce((acc, attr) => {
      acc[attr.name] = truncate(attr.value, 80);
      return acc;
    }, {});

  const attrMap = {};
  for (const name of attrs) {
    const value = element.getAttribute(name);
    if (value) attrMap[name] = truncate(value, 80);
  }

  const text = truncate((element.textContent || '').replace(/\s+/g, ' ').trim(), 120);

  return {
    tag: element.tagName.toLowerCase(),
    text,
    attributes: { ...attrMap, ...ariaAttrs },
    compactHtml: compactOuterHtml(element),
    ...extra
  };
}

function compactOuterHtml(element) {
  const clone = element.cloneNode(true);
  pruneNode(clone, 0);
  let html = clone.outerHTML;
  if (html.length > 600) {
    html = `${html.slice(0, 600)}...`;
  }
  return html;
}

function pruneNode(node, depth) {
  if (!(node instanceof Element)) return;
  const allowedAttrs = ['id', 'class', 'placeholder', 'value', 'name', 'href', 'type', 'role'];
  for (const attr of Array.from(node.attributes)) {
    if (!allowedAttrs.includes(attr.name) && !attr.name.startsWith('aria-')) {
      node.removeAttribute(attr.name);
    }
  }

  if (depth >= 2) {
    node.innerHTML = '';
    return;
  }

  for (const child of Array.from(node.children).slice(3)) {
    child.remove();
  }

  for (const child of Array.from(node.children)) {
    pruneNode(child, depth + 1);
  }
}

function getUniqueSelector(element) {
  if (!(element instanceof Element)) return '';
  if (element.id) {
    const idSel = `#${CSS.escape(element.id)}`;
    if (isUnique(idSel)) return idSel;
  }

  const attrs = ['name', 'aria-label', 'placeholder', 'type', 'role'];
  for (const attr of attrs) {
    const value = element.getAttribute(attr);
    if (!value) continue;
    const sel = `${element.tagName.toLowerCase()}[${attr}="${CSS.escape(value)}"]`;
    if (isUnique(sel)) return sel;
  }

  const classList = Array.from(element.classList).filter(Boolean);
  if (classList.length) {
    const sel = `${element.tagName.toLowerCase()}.${classList.slice(0, 2).map((c) => CSS.escape(c)).join('.')}`;
    if (isUnique(sel)) return sel;
  }

  const path = [];
  let node = element;
  while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.body) {
    let segment = node.tagName.toLowerCase();
    if (node.id) {
      segment = `#${CSS.escape(node.id)}`;
      path.unshift(segment);
      break;
    }

    const parent = node.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter((el) => el.tagName === node.tagName);
      if (siblings.length > 1) {
        const index = siblings.indexOf(node) + 1;
        segment += `:nth-of-type(${index})`;
      }
    }

    path.unshift(segment);
    const selector = path.join(' > ');
    if (isUnique(selector)) return selector;
    node = parent;
  }

  return path.join(' > ');
}

function isUnique(selector) {
  try {
    return document.querySelectorAll(selector).length === 1;
  } catch {
    return false;
  }
}

async function executeCommands(commands) {
  const list = Array.isArray(commands) ? commands : [commands];
  replayInProgress = true;
  try {
    const results = [];
    for (const command of list) {
      const outcome = await executeSingle(command);
      results.push({ command, outcome });
    }
    return results;
  } finally {
    replayInProgress = false;
  }
}

async function executeCode(code) {
  if (typeof code !== 'string' || !code.trim()) {
    throw new Error('実行コードが空です。');
  }

  const normalizedCode = stripCodeFences(code.trim());
  const helpers = createHelpers();

  replayInProgress = true;
  try {
    const runner = new Function(
      'helpers',
      '"use strict"; const { click, input, wait, query, queryAll, byText, byLabel } = helpers; return (async () => { ' +
        normalizedCode +
        '\n})().catch((error) => { throw error; });'
    );
    return await runner(helpers);
  } finally {
    replayInProgress = false;
  }
}

function createHelpers() {
  return {
    click: async (selector) => {
      await executeSingle({ action: 'click', selector });
    },
    input: async (selector, value) => {
      await executeSingle({ action: 'input', selector, value });
    },
    wait: (ms) => new Promise((resolve) => setTimeout(resolve, Number(ms) || 0)),
    query: (selector) => document.querySelector(selector),
    queryAll: (selector) => Array.from(document.querySelectorAll(selector)),
    byText: (text) =>
      Array.from(document.querySelectorAll('*')).find((el) => (el.textContent || '').includes(text)) || null,
    byLabel: (label) =>
      Array.from(document.querySelectorAll('input, textarea, button, [role="button"], [contenteditable="true"]')).find((el) => {
        const aria = (el.getAttribute('aria-label') || '').trim();
        const title = (el.getAttribute('title') || '').trim();
        return aria === label || title === label;
      }) || null
  };
}

function stripCodeFences(text) {
  const fenced = text.match(/```(?:javascript|js)?\s*([\s\S]*?)```/i);
  return fenced ? fenced[1].trim() : text;
}

async function executeSingle(command) {
  if (!command?.selector) {
    throw new Error('selector が指定されていないコマンドがあります。');
  }

  const element = document.querySelector(command.selector);
  if (!element) {
    throw new Error(`要素が見つかりません: ${command.selector}`);
  }

  const action = command.action || 'click';

  if (action === 'click') {
    element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    element.click();
    return 'clicked';
  }

  if (action === 'input') {
    const value = command.value ?? '';
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      element.focus();
      element.value = value;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return 'input-set';
    }

    if (element.getAttribute('contenteditable') === 'true') {
      element.focus();
      element.textContent = value;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      return 'contenteditable-set';
    }

    throw new Error(`input actionに未対応の要素です: ${command.selector}`);
  }

  if (action === 'wait') {
    const ms = Number(command.ms || 1000);
    await new Promise((resolve) => setTimeout(resolve, ms));
    return `waited ${ms}ms`;
  }

  throw new Error(`未対応action: ${action}`);
}

function readValue(element) {
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    return element.value;
  }
  return element.textContent?.trim() || '';
}

function shouldRedact(element) {
  if (element instanceof HTMLInputElement) {
    const type = (element.getAttribute('type') || '').toLowerCase();
    if (type === 'password') return true;
  }

  const hints = [
    element.getAttribute('name'),
    element.getAttribute('id'),
    element.getAttribute('aria-label'),
    element.getAttribute('placeholder'),
    element.getAttribute('autocomplete')
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();

  return /(password|passwd|passcode|secret|token|otp|2fa|mfa|auth|verification|code)/.test(hints);
}

function truncate(text, max) {
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max)}...` : text;
}
