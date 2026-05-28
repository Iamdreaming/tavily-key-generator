// Turnstile Patch - 反检测补丁
// 参考 Grok 注册机的 turnstilePatch 扩展

(function() {
    'use strict';

    // 1. 移除 webdriver 标志
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });

    // 2. 伪造 chrome 对象
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {};
    }

    // 3. 伪造 plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            return [
                {
                    name: 'Chrome PDF Plugin',
                    description: 'Portable Document Format',
                    filename: 'internal-pdf-viewer',
                },
                {
                    name: 'Chrome PDF Viewer',
                    description: '',
                    filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                },
                {
                    name: 'Native Client',
                    description: '',
                    filename: 'internal-nacl-plugin',
                },
            ];
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

    // 8. 防止 iframe contentWindow 检测
    const originalAttachShadow = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function() {
        return originalAttachShadow.apply(this, arguments);
    };

    // 9. 伪造 permissions API
    if (navigator.permissions) {
        const originalQuery = navigator.permissions.query;
        navigator.permissions.query = function(parameters) {
            if (parameters.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return originalQuery.call(this, parameters);
        };
    }

    // 10. 移除自动化相关属性
    delete navigator.__proto__.webdriver;

    // 11. 伪造 WebGL 渲染器
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return 'Intel Inc.';
        }
        if (parameter === 37446) {
            return 'Intel Iris OpenGL Engine';
        }
        return getParameter.apply(this, arguments);
    };

    console.log('[Turnstile Patch] Browser fingerprints patched');
})();
