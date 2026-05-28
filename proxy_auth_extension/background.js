// Proxy credentials will be injected by Python before loading
// _PROXY_USER_ and _PROXY_PASS_ are placeholders

chrome.webRequest.onAuthRequired.addListener(
  function(details, callbackFn) {
    if (details.isProxy) {
      callbackFn({authCredentials: {username: _PROXY_USER_, password: _PROXY_PASS_}});
    } else {
      callbackFn({});
    }
  },
  {urls: ["<all_urls>"]},
  ["asyncBlocking"]
);
