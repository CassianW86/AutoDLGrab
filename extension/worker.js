chrome.action.onClicked.addListener(tab => {
  const valid = tab.url && new URL(tab.url).origin === "https://www.autodl.com";
  chrome.tabs.create({url: chrome.runtime.getURL("connect.html") + (valid ? "#" + tab.id : "")});
});
