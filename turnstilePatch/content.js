// Turnstile Patch - 增强版反检测补丁
(function() {
    'use strict';

    // 1. 移除 webdriver 标志（最核心）
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });
    delete navigator.__proto__.webdriver;

    // 2. 伪造 chrome 对象
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            connect: function() {},
            sendMessage: function() {},
        };
    }
    if (!window.chrome.loadTimes) {
        window.chrome.loadTimes = function() {
            return {
                commitLoadTime: Date.now() / 1000,
                connectionInfo: "h2",
                finishDocumentLoadTime: Date.now() / 1000,
                finishLoadTime: Date.now() / 1000,
                firstPaintAfterLoadTime: 0,
                firstPaintTime: Date.now() / 1000,
                navigationType: "Other",
                npnNegotiatedProtocol: "h2",
                requestTime: Date.now() / 1000,
                startLoadTime: Date.now() / 1000,
                wasAlternateProtocolAvailable: false,
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true,
            };
        };
    }
    if (!window.chrome.csi) {
        window.chrome.csi = function() {
            return {
                onloadT: Date.now(),
                pageT: Date.now() - performance.timing.navigationStart,
                startE: performance.timing.navigationStart,
                tran: 15,
            };
        };
    }

    // 3. 伪造 plugins
    const fakePlugins = [
        {
            name: 'Chrome PDF Plugin',
            description: 'Portable Document Format',
            filename: 'internal-pdf-viewer',
            length: 1,
        },
        {
            name: 'Chrome PDF Viewer',
            description: '',
            filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
            length: 1,
        },
        {
            name: 'Native Client',
            description: '',
            filename: 'internal-nacl-plugin',
            length: 2,
        },
    ];
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = Array.from(fakePlugins);
            arr.item = (index) => arr[index] || null;
            arr.namedItem = (name) => arr.find(p => p.name === name) || null;
            arr.refresh = () => {};
            return arr;
        },
    });

    // 4. 伪造 languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en-US', 'en'],
    });

    // 5. 伪造 platform
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
    });

    // 6. 伪造 hardwareConcurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
    });

    // 7. 伪造 deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
    });

    // 8. 伪造 maxTouchPoints
    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 0,
    });

    // 9. 伪造 connection
    if (navigator.connection) {
        Object.defineProperty(navigator.connection, 'rtt', {
            get: () => 50,
        });
    }

    // 10. 伪造 permissions API
    if (navigator.permissions) {
        const originalQuery = navigator.permissions.query;
        navigator.permissions.query = function(parameters) {
            if (parameters.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return originalQuery.call(this, parameters);
        };
    }

    // 11. 伪造 WebGL 渲染器
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return 'Intel Inc.';
        }
        if (parameter === 37446) {
            return 'Intel(R) UHD Graphics 630';
        }
        return getParameter.apply(this, arguments);
    };

    // 12. 伪造 WebGL2
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) {
                return 'Intel Inc.';
            }
            if (parameter === 37446) {
                return 'Intel(R) UHD Graphics 630';
            }
            return getParameter2.apply(this, arguments);
        };
    }

    // 13. 防止 toString 检测
    const nativeToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === navigator.permissions.query) {
            return 'function query() { [native code] }';
        }
        return nativeToString.call(this);
    };

    // 14. 伪造 screen 属性
    Object.defineProperty(screen, 'colorDepth', {
        get: () => 24,
    });

    // 15. 伪造 Date 时区
    const originalGetTimezoneOffset = Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset = function() {
        return -480; // UTC+8
    };

    console.log('[Turnstile Patch] Enhanced anti-detection loaded');
})();
