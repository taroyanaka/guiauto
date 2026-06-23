const LOG_PREFIX = "[XMD:bg]";
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") return;

  if (message.type === "XMD_FETCH_AND_DOWNLOAD") {
    const requestId = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    console.log(LOG_PREFIX, "download request received", {
      requestId,
      src: message.src,
      tabId: sender?.tab?.id,
      frameId: sender?.frameId,
      url: sender?.url
    });

    void handleFetchAndSave(message, requestId)
      .then((result) => {
        console.log(LOG_PREFIX, "save accepted", { requestId, ...result });
        sendResponse({ ok: true, ...result });
      })
      .catch((error) => {
        console.error(LOG_PREFIX, "save failed", {
          requestId,
          message: String(error?.message || error),
          stack: error?.stack
        });
        sendResponse({ ok: false, error: String(error?.message || error) });
      });
    return true;
  }
});

async function handleFetchAndSave(message, requestId) {
  const { src } = message;
  if (!src) throw new Error("Missing src.");

  console.log(LOG_PREFIX, "photo page fetch start", { requestId, src });
  const pageResponse = await fetch(src, {
    credentials: "omit",
    mode: "cors",
    cache: "force-cache"
  });
  if (!pageResponse.ok) {
    throw new Error(`Photo page fetch failed with HTTP ${pageResponse.status}`);
  }

  const pageHtml = await pageResponse.text();
  console.log(LOG_PREFIX, "photo page received", {
    requestId,
    size: pageHtml.length
  });

  const imageUrl = extractImageUrlFromPhotoPage(pageHtml, src);
  if (!imageUrl) throw new Error("Could not find image URL on photo page.");

  console.log(LOG_PREFIX, "image url extracted", { requestId, imageUrl });
  const imageResponse = await fetch(imageUrl, {
    credentials: "omit",
    mode: "cors",
    cache: "force-cache"
  });
  if (!imageResponse.ok) {
    throw new Error(`Image fetch failed with HTTP ${imageResponse.status}`);
  }

  const blob = await imageResponse.blob();
  const objectUrl = URL.createObjectURL(blob);
  const filename = sanitizeFilename(makeDownloadName(imageUrl));
  console.log(LOG_PREFIX, "download payload ready", {
    requestId,
    filename,
    blobSize: blob.size,
    objectUrlLength: objectUrl.length
  });

  const downloadId = await new Promise((resolve, reject) => {
    chrome.downloads.download(
      {
        url: objectUrl,
        filename,
        saveAs: false,
        conflictAction: "uniquify"
      },
      (id) => {
        if (chrome.runtime.lastError) {
          URL.revokeObjectURL(objectUrl);
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(id);
      }
    );
  }).catch((error) => {
    console.error(LOG_PREFIX, "chrome.downloads.download failed", {
      requestId,
      filename,
      message: String(error?.message || error),
      stack: error?.stack
    });
    throw error;
  });

  setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);

  console.log(LOG_PREFIX, "download queued", {
    requestId,
    downloadId,
    filename
  });

  return { downloadId, savedAs: filename };
}

function extractImageUrlFromPhotoPage(html, pageUrl) {
  const patterns = [
    /<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+name=["']twitter:image["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+property=["']og:image:url["'][^>]+content=["']([^"']+)["']/i
  ];

  for (const pattern of patterns) {
    const match = html.match(pattern);
    if (match?.[1]) return absoluteUrl(match[1], pageUrl);
  }

  return "";
}

function absoluteUrl(url, baseUrl) {
  try {
    return new URL(url, baseUrl).href;
  } catch {
    return url;
  }
}

function makeDownloadName(imageUrl) {
  try {
    const parsed = new URL(imageUrl);
    const last = parsed.pathname.split("/").filter(Boolean).pop() || "image";
    const ext = last.includes(".") ? last.split(".").pop() : inferExtFromUrl(imageUrl);
    return `X_Media_Downloads/${Date.now()}.${ext}`;
  } catch {
    return `X_Media_Downloads/${Date.now()}.jpg`;
  }
}

function inferExtFromUrl(url) {
  try {
    const pathname = new URL(url).pathname.toLowerCase();
    if (pathname.includes(".png")) return "png";
    if (pathname.includes(".webp")) return "webp";
    if (pathname.includes(".gif")) return "gif";
    if (pathname.includes(".jpeg")) return "jpeg";
    if (pathname.includes(".jpg")) return "jpg";
  } catch {
    // ignore
  }
  return "jpg";
}

function sanitizeFilename(filename) {
  return String(filename).replace(/[\\:\*\?"<>\|\r\n\t]/g, "_").trim();
}
