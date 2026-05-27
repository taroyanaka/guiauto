// 漫才の台本読み上げのために作ったクロームエクステンション
// ブラウザの文字サイズをデカくしてから実行する

// 導入方法と使い方
// Chromeブラウザを開き、URLバーに chrome://extensions/ と入力して拡張機能管理ページを開きます。
// 右上の「デベロッパー モード」をオンにします。
// 左上にある「パッケージ化されていない拡張機能を読み込む」ボタンをクリックします。
// 今回ファイルを作成した c:\Users\taroyanaka\Downloads\scroller_extension フォルダを選択します。
// ツールバーに「Auto Scroller」のアイコンが追加されるので、スクロールさせたいページを開いた状態でアイコンをクリックします。
// ポップアップでディレイ時間（Delay）とスクロール速度（Speed）を指定して「Start」を押すと、指定秒数後に自動でスクロールが開始されます。途中で止めたい場合は再度アイコンをクリックして「Stop」を押してください。

document.getElementById('start').addEventListener('click', async () => {
  const delaySec = parseInt(document.getElementById('delay').value, 10);
  const speedPxPerSec = parseInt(document.getElementById('speed').value, 10);

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: startScrolling,
    args: [delaySec, speedPxPerSec]
  });
});

document.getElementById('stop').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: stopScrolling
  });
});

function startScrolling(delaySec, speedPxPerSec) {
  if (window.autoScrollTimeout) clearTimeout(window.autoScrollTimeout);
  if (window.autoScrollInterval) clearInterval(window.autoScrollInterval);

  window.autoScrollTimeout = setTimeout(() => {
    const intervalMs = 20; 
    const pxPerInterval = speedPxPerSec * (intervalMs / 1000);
    let accumulatedScroll = 0;
    
    window.autoScrollInterval = setInterval(() => {
      accumulatedScroll += pxPerInterval;
      if (accumulatedScroll >= 1) {
        const scrollAmount = Math.floor(accumulatedScroll);
        window.scrollBy(0, scrollAmount);
        accumulatedScroll -= scrollAmount;
      }
      
      // Stop if reached the bottom
      if ((window.innerHeight + Math.ceil(window.scrollY)) >= document.body.offsetHeight) {
        clearInterval(window.autoScrollInterval);
      }
    }, intervalMs);
  }, delaySec * 1000);
}

function stopScrolling() {
  if (window.autoScrollTimeout) clearTimeout(window.autoScrollTimeout);
  if (window.autoScrollInterval) clearInterval(window.autoScrollInterval);
}
