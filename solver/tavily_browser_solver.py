"""
Tavily Turnstile Solver — DrissionPage 方案

完全参照 Grok 注册机的 DrissionPage 实现：
1. Chromium() 启动新实例
2. page.run_js() 注入 JS
3. Turnstile 通过 shadow DOM 点击 + 等待 token
"""

import asyncio
import logging
import os
import random
import re
import string
import sys
import time

logger = logging.getLogger(__name__)


def _check_drissionpage():
    """检查 DrissionPage 是否可用"""
    try:
        import DrissionPage
        return True
    except ImportError:
        return False


# ========== 邮箱工具（复用 mail_provider 的 cloudflare_temp_email）==========

def _create_email_address(config):
    """创建临时邮箱（使用 Grok 的 /api/new_address 公开 API）"""
    import requests
    cf_api_url = config.get("CF_TEMP_EMAIL_API_URL", "").rstrip("/")
    cf_domain = config.get("CF_TEMP_EMAIL_DOMAIN", "")
    cf_verify = not config.get("CF_TEMP_EMAIL_SKIP_VERIFY", False)

    # 用 /api/new_address（公开 API，不需要 admin 密码）
    url = f"{cf_api_url}/api/new_address"
    payload = {}
    if cf_domain:
        payload["domain"] = cf_domain
    logger.info(f"[CF-Temp-Email] 创建邮箱: POST {url}")
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, verify=cf_verify, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    address = data.get("address")
    jwt_token = data.get("jwt")
    if not address or not jwt_token:
        raise Exception(f"创建邮箱失败: {data}")
    logger.info(f"[CF-Temp-Email] 邮箱创建成功: {address}")
    return address, jwt_token


def _wait_for_verification_code(config, jwt_token, email, timeout=180, poll_interval=3):
    """轮询邮箱等待验证码（使用 Grok 的 /messages + /api/mail/{id} API）"""
    import requests
    cf_api_url = config.get("CF_TEMP_EMAIL_API_URL", "").rstrip("/")
    cf_verify = not config.get("CF_TEMP_EMAIL_SKIP_VERIFY", False)
    headers = {"Authorization": f"Bearer {jwt_token}"}

    deadline = time.time() + timeout
    seen_attempts = {}

    while time.time() < deadline:
        try:
            # 获取邮件列表
            resp = requests.get(f"{cf_api_url}/api/messages", headers=headers, params={"limit": 20, "offset": 0}, verify=cf_verify, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            messages = data if isinstance(data, list) else data.get("data", data.get("result", []))
        except Exception as e:
            logger.warning(f"[CF-Temp-Email] 拉取邮件列表失败: {e}")
            time.sleep(poll_interval)
            continue

        logger.info(f"[CF-Temp-Email] 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            if seen_attempts.get(msg_id, 0) >= 5:
                continue
            seen_attempts[msg_id] = seen_attempts.get(msg_id, 0) + 1

            # 收集邮件内容
            parts = []
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)

            # 尝试 detail 接口补全
            try:
                resp2 = requests.get(f"{cf_api_url}/api/mail/{msg_id}", headers=headers, verify=cf_verify, timeout=15)
                resp2.raise_for_status()
                detail = resp2.json()
                if isinstance(detail, dict) and isinstance(detail.get("data"), dict):
                    detail = detail["data"]
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception:
                pass

            logger.info(f"[CF-Temp-Email] 收到邮件: {subject}")

            # 提取验证码
            code = _extract_verification_code(combined, subject)
            if code:
                logger.info(f"[CF-Temp-Email] 提取到验证码: {code}")
                return code

        time.sleep(poll_interval)

    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def _extract_verification_code(text, subject=""):
    """提取验证码"""
    patterns = [
        r'\b(\d{6})\b',
        r'验证码[：:\s]*(\d{4,8})',
        r'verification code[：:\s]*(\d{4,8})',
        r'code[：:\s]*(\d{4,8})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# ========== DrissionPage 浏览器 ==========

async def solve(config, **kwargs):
    """
    主入口：用 DrissionPage 打开 Tavily 注册页，完成 Turnstile 验证，获取 token。

    返回: {"turnstile_token": "xxx"} 或 None
    """
    if not _check_drissionpage():
        logger.error("[DrissionPage] 未安装，pip install DrissionPage")
        return None

    from DrissionPage import Chromium, ChromiumOptions

    headless = config.get("REGISTER_HEADLESS", False)
    timeout = config.get("TURNSTILE_TIMEOUT", 300)
    user_data_dir = config.get("BROWSER_USER_DATA_DIR", "")

    email = kwargs.get("email")
    email_jwt = kwargs.get("email_jwt")
    if not email or not email_jwt:
        raise ValueError("必须传入 email 和 email_jwt")

    # === 创建浏览器 ===
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)
    if headless:
        options.headless()

    # 用户数据目录
    if user_data_dir:
        options.set_user_data_path(user_data_dir)

    # 反检测参数
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--no-first-run')
    options.set_argument('--no-default-browser-check')
    options.set_argument('--disable-infobars')
    options.set_argument('--window-size=1280,900')

    # 加载 turnstilePatch 扩展（如果存在）
    ext_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "turnstilePatch")
    if os.path.exists(ext_path):
        options.add_extension(ext_path)
        logger.info(f"[DrissionPage] 加载 turnstilePatch 扩展: {ext_path}")

    logger.info("[DrissionPage] 启动浏览器...")
    browser = Chromium(options)
    tabs = browser.get_tabs()
    page = tabs[-1] if tabs else browser.new_tab()
    logger.info(f"[DrissionPage] 浏览器启动成功, user_data_path={getattr(browser, 'user_data_path', 'N/A')}")

    try:
        # === 1. 打开注册页 ===
        url = config.get("REGISTER_URL", "https://app.tavily.com")
        logger.info(f"[DrissionPage] 导航到 {url}")
        page.get(url)
        time.sleep(3)

        # === 2. 点击 Email 注册按钮 ===
        logger.info("[DrissionPage] 查找 Email 注册入口...")
        email_btn = None
        for sel in ['button:has-text("Continue with Email")', 'button:has-text("Sign up with Email")',
                     'button:has-text("Email")', 'button:has-text("Register")',
                     'text=Continue with Email', 'text=Sign up with Email']:
            try:
                email_btn = page.ele(sel)
                if email_btn:
                    logger.info(f"[DrissionPage] 找到注册按钮: {sel}")
                    break
            except Exception:
                continue

        if not email_btn:
            # 尝试点击 "Sign Up" 链接
            for sel in ['text=Sign Up', 'text=Register', 'a:has-text("Sign Up")']:
                try:
                    link = page.ele(sel)
                    if link:
                        link.click()
                        logger.info(f"[DrissionPage] 点击了 {sel}")
                        time.sleep(3)
                        break
                except Exception:
                    continue
            # 再找 Email 按钮
            for sel in ['button:has-text("Email")', 'text=Continue with Email', 'text=Sign up with Email']:
                try:
                    email_btn = page.ele(sel)
                    if email_btn:
                        break
                except Exception:
                    continue

        if not email_btn:
            logger.error("[DrissionPage] 找不到 Email 注册入口")
            _save_debug_screenshot(page, "no_email_button")
            return None

        email_btn.click()
        logger.info("[DrissionPage] 点击了 Email 注册按钮")
        time.sleep(3)

        # === 3. 填写邮箱 ===
        logger.info(f"[DrissionPage] 填写邮箱: {email}")
        email_input = None
        for sel in ['@name=email', '@type=email', '@placeholder=Email', '@name=username']:
            try:
                email_input = page.ele(sel)
                if email_input:
                    break
            except Exception:
                continue

        if not email_input:
            logger.error("[DrissionPage] 找不到邮箱输入框")
            _save_debug_screenshot(page, "no_email_input")
            return None

        email_input.clear()
        email_input.input(email)
        time.sleep(1)

        # 点击 Continue/Next/Submit
        submit_btn = None
        for sel in ['button:has-text("Continue")', 'button:has-text("Next")', 'button:has-text("Submit")',
                     'button:has-text("Register")', 'button:has-text("Sign Up")', 'button[type=submit]']:
            try:
                submit_btn = page.ele(sel)
                if submit_btn:
                    break
            except Exception:
                continue

        if submit_btn:
            submit_btn.click()
            logger.info("[DrissionPage] 提交邮箱")
            time.sleep(3)

        # === 4. 等待验证码输入框 ===
        logger.info("[DrissionPage] 等待验证码输入框...")
        code_input = None
        for _ in range(20):
            for sel in ['@name=code', '@type=text', '@placeholder=Code', '@placeholder=Verification',
                         'input[autocomplete="one-time-code"]']:
                try:
                    code_input = page.ele(sel)
                    if code_input:
                        break
                except Exception:
                    continue
            if code_input:
                break
            time.sleep(1)

        if not code_input:
            logger.error("[DrissionPage] 找不到验证码输入框")
            _save_debug_screenshot(page, "no_code_input")
            return None

        # === 5. 等待邮件验证码 ===
        logger.info("[DrissionPage] 等待邮件验证码...")
        code = _wait_for_verification_code(config, email_jwt, email, timeout=180)

        # === 6. 填写验证码 ===
        logger.info(f"[DrissionPage] 填写验证码: {code}")
        code_input.clear()
        code_input.input(code)
        time.sleep(1)

        # 提交验证码
        verify_btn = None
        for sel in ['button:has-text("Verify")', 'button:has-text("Continue")', 'button:has-text("Submit")',
                     'button[type=submit]']:
            try:
                verify_btn = page.ele(sel)
                if verify_btn:
                    break
            except Exception:
                continue
        if verify_btn:
            verify_btn.click()
            logger.info("[DrissionPage] 提交验证码")
            time.sleep(3)

        # === 7. 等待 Turnstile token（参照 Grok getTurnstileToken）===
        logger.info("[DrissionPage] 等待 Turnstile token...")
        token = _get_turnstile_token(page, timeout=timeout)

        if token:
            logger.info(f"[DrissionPage] 获取到 Turnstile token: {token[:50]}...")
            return {"turnstile_token": token}
        else:
            logger.error("[DrissionPage] Turnstile token 获取失败")
            _save_debug_screenshot(page, "turnstile_failed")
            return None

    except Exception as e:
        logger.error(f"[DrissionPage] 注册流程异常: {e}", exc_info=True)
        try:
            _save_debug_screenshot(page, "exception")
        except Exception:
            pass
        return None
    finally:
        try:
            browser.quit()
        except Exception:
            pass


def _get_turnstile_token(page, timeout=300):
    """
    参照 Grok 注册机的 getTurnstileToken 实现：
    1. 先尝试 JS 直接读取 token
    2. 如果没有，通过 shadow DOM 点击 Turnstile checkbox
    3. 等待 token 出现
    """
    # 先 reset
    try:
        page.run_js('try { if (window.turnstile && typeof turnstile.reset === "function") turnstile.reset(); } catch(e) {}')
    except Exception:
        pass

    deadline = time.time() + timeout
    click_count = 0

    while time.time() < deadline:
        # 1. 尝试直接读取 token
        try:
            token = page.run_js("""
try {
  var byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch(e) { return ''; }
""")
            token = str(token or "").strip()
            if len(token) >= 80:
                logger.info(f"[DrissionPage] Turnstile 已通过，token长度={len(token)}")
                return token
        except Exception:
            pass

        # 2. 尝试通过 shadow DOM 点击 checkbox
        if click_count < 15:
            try:
                challenge_input = page.ele("@name=cf-turnstile-response")
                if challenge_input:
                    wrapper = challenge_input.parent()
                    iframe = None
                    try:
                        iframe = wrapper.shadow_root.ele("tag:iframe")
                    except Exception:
                        iframe = None
                    if iframe:
                        # 注入反检测
                        try:
                            iframe.run_js("""
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
var sx = getRandomInt(800, 1200);
var sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
""")
                        except Exception:
                            pass
                        # 点击 checkbox
                        try:
                            body_sr = iframe.ele("tag:body").shadow_root
                            btn = body_sr.ele("tag:input")
                            if btn:
                                btn.click()
                                click_count += 1
                                logger.info(f"[DrissionPage] 点击 Turnstile checkbox 第 {click_count} 次")
                        except Exception:
                            pass
                else:
                    # 兜底：尝试触发页面上的 Turnstile 容器
                    try:
                        page.run_js("""
var nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter(function(n) {
  var txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute('src') || '');
  return String(txt).toLowerCase().includes('turnstile');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
""")
                        click_count += 1
                    except Exception:
                        pass
            except Exception:
                pass

        time.sleep(1)

    logger.warning(f"[DrissionPage] Turnstile 超时 ({timeout}s)，点击了 {click_count} 次")
    return None


def _save_debug_screenshot(page, name):
    """保存调试截图"""
    try:
        debug_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug")
        os.makedirs(debug_dir, exist_ok=True)
        path = os.path.join(debug_dir, f"{name}_{int(time.time())}.png")
        page.get_screenshot(path=path)
        logger.info(f"[DrissionPage] 调试截图: {path}")
    except Exception as e:
        logger.warning(f"[DrissionPage] 截图失败: {e}")
