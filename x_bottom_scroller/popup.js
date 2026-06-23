async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function runScript(func, args = []) {
  const tab = await getActiveTab();
  if (!tab?.id) return;
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args });
}

document.getElementById("start").addEventListener("click", async () => {
  await runScript(startScrolling);
  document.getElementById("status").textContent = "Scrolling...";
});

document.getElementById("stop").addEventListener("click", async () => {
  await runScript(stopScrolling);
  document.getElementById("status").textContent = "Stopped";
});

function startScrolling() {
  if (window.autoScrollInterval) clearInterval(window.autoScrollInterval);
  window.autoScrollInterval = setInterval(() => {
    window.scrollBy(0, Math.max(180, Math.floor(window.innerHeight * 0.8)));
    const bottom = Math.ceil(window.scrollY + window.innerHeight) >= document.body.scrollHeight;
    if (bottom) {
      clearInterval(window.autoScrollInterval);
      window.autoScrollInterval = null;
    }
  }, 250);
}

function stopScrolling() {
  if (window.autoScrollInterval) clearInterval(window.autoScrollInterval);
  window.autoScrollInterval = null;
}
