chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "YTDL_GET_ACTIVE_URL") {
    if (sender.tab && sender.tab.url) {
      sendResponse({ url: sender.tab.url });
    } else {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs[0];
        sendResponse({ url: tab ? tab.url : "" });
      });
      return true; // async
    }
  }
});
