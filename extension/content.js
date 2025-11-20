function injectButton() {
  try {
    const existing = document.querySelector("#local-ytdl-btn");
    if (existing) return;

    const container =
      document.querySelector("#top-level-buttons-computed") ||
      document.querySelector("#top-level-buttons");

    if (!container) return;

    const btn = document.createElement("button");
    btn.id = "local-ytdl-btn";
    btn.textContent = "Download (Local YT-DL)";
    btn.style.cssText = `
      padding: 6px 12px;
      margin-left: 8px;
      border-radius: 16px;
      border: none;
      cursor: pointer;
      background: #ff4b8b;
      color: #fff;
      font-weight: 600;
      font-size: 12px;
    `;

    btn.addEventListener("click", () => {
      chrome.runtime.sendMessage(
        { type: "YTDL_SET_URL", url: window.location.href },
        () => {}
      );
      if (chrome.action && chrome.action.openPopup) {
        chrome.action.openPopup();
      }
    });

    container.appendChild(btn);
  } catch (e) {
    // ignore
  }
}

if (location.href.includes("watch")) {
  const observer = new MutationObserver(() => injectButton());
  observer.observe(document.body, { childList: true, subtree: true });
  injectButton();
}
