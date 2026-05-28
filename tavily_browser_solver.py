"""
使用 DrissionPage + 真实 Chrome 完成 Tavily 注册
参考 Grok 注册机方案：连接真实浏览器，Turnstile 自动通过
"""
import os
import re
import time
import threading
import requests as std_requests

from DrissionPage import Chromium, ChromiumOptions
from config import (
    EMAIL_CODE_TIMEOUT,
    REGISTER_HEADLESS,
)
from mail_provider import get_email_code, get_verification_link

TURNSTILE_SITEKEY = "0x4AAAAAAAQFNSW6xordsuIq"
_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "accounts.txt")
_SAVE_LOCK = threading.Lock()


def create_browser_options():
    """创建浏览器配置"""
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)
    # 有头模式 - Turnstile 在真实浏览器下通过率更高
    if REGISTER_HEADLESS:
        options.headless()
    return options


def start_browser():
    """启动 Chrome 浏览器"""
    options = create_browser_options()
    browser = Chromium(options)
    tabs = browser.get_tabs()
    page = tabs[-1] if tabs else browser.new_tab()
    return browser, page


def extract_signup_url(html):
    """从登录页提取注册入口"""
    match = re.search(r'href="(/u/signup/identifier[^"]*)"', html)
    if not match:
        return None
    return f"https://auth.tavily.com{match.group(1)}"


def get_turnstile_token(page, timeout=60):
    """从页面获取 Turnstile token（学自 Grok 注册机）

    真实 Chrome 下 Turnstile 通常会自动通过，等待即可。
    如果卡住，尝试点击 checkbox。
    """
    # 先重置 turnstile
    try:
        page.run_js("""
            try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}
        """)
    except Exception:
        pass

    start_time = time.time()
    click_count = 0
    max_clicks = 10

    while time.time() - start_time < timeout:
        # 检查 token
        try:
            token = page.run_js("""
                try {
                    const byInput = String(
                        (document.querySelector('input[name="cf-turnstile-response"]') || {}).value || ''
                    ).trim();
                    if (byInput) return byInput;
                    if (window.turnstile && typeof turnstile.getResponse === 'function') {
                        return String(turnstile.getResponse() || '').trim();
                    }
                    return '';
                } catch(e) { return ''; }
            """)
            token = str(token or "").strip()
            if len(token) >= 80:
                return token
        except Exception:
            pass

        # 每隔 3 秒尝试点击一次 checkbox
        elapsed = time.time() - start_time
        if int(elapsed) % 3 == 0 and click_count < max_clicks and elapsed > 2:
            try:
                challenge_input = page.ele("@name=cf-turnstile-response")
                if challenge_input:
                    wrapper = challenge_input.parent()
                    iframe = None
                    try:
                        iframe = wrapper.shadow_root.ele("tag:iframe")
                    except Exception:
                        pass
                    if iframe:
                        try:
                            body_sr = iframe.ele("tag:body").shadow_root
                            btn = body_sr.ele("tag:input")
                            if btn:
                                btn.click()
                                click_count += 1
                                print(f"  [turnstile] 点击 checkbox #{click_count}")
                        except Exception:
                            pass
            except Exception:
                pass

        time.sleep(1)

    return None


def wait_for_turnstile(page, timeout=60):
    """等待 Turnstile 自动通过或手动点击通过"""
    print("🔐 等待 Turnstile 验证...")
    token = get_turnstile_token(page, timeout=timeout)
    if token:
        print(f"✅ Turnstile 已通过 (token: {token[:30]}...)")
        return token
    print("❌ Turnstile 验证超时")
    return None


def extract_api_key(page):
    """从页面提取 API Key"""
    html = page.html
    api_key_matches = re.findall(r'tvly-[a-zA-Z0-9_-]{20,}', html)
    api_keys = [k for k in api_key_matches if k != "tvly-YOUR_API_KEY"]
    if not api_keys:
        return None
    return max(api_keys, key=len)


def wait_for_api_key(page, timeout=30):
    """等待 API Key 出现"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        api_key = extract_api_key(page)
        if api_key:
            return api_key
        time.sleep(1)
    return None


def save_account(email, password, api_key):
    """保存账号信息"""
    with _SAVE_LOCK:
        with open(_SAVE_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{email},{password},{api_key}\n")


def verify_api_key(api_key, timeout=30):
    """验证 API Key 是否可用"""
    try:
        response = std_requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": "api key verification",
                "max_results": 1,
            },
            timeout=timeout,
        )
    except Exception as exc:
        print(f"❌ API Key 调用测试失败: {exc}")
        return False

    if response.status_code == 200:
        print("✅ API Key 调用测试通过")
        return True

    preview = response.text.strip().replace("\n", " ")[:160]
    print(f"❌ API Key 调用测试失败: HTTP {response.status_code}")
    if preview:
        print(f"   响应: {preview}")
    return False


def register_with_browser_solver(email, password):
    """使用 DrissionPage + 真实 Chrome 注册"""
    print(f"🌐 使用 DrissionPage 浏览器模式注册: {email}")

    browser = None
    try:
        browser, page = start_browser()
        print("✅ 浏览器已启动")

        # 1. 访问 Tavily 登录/注册页
        page.get("https://app.tavily.com/sign-in")
        page.wait.doc_loaded()
        time.sleep(2)

        # 检查是否有注册入口
        signup_url = extract_signup_url(page.html)
        if signup_url:
            print("🧭 进入注册页...")
            page.get(signup_url)
            page.wait.doc_loaded()
            time.sleep(2)
        else:
            # 可能是新版登录/注册合一入口
            email_input = page.ele("@name=email") or page.ele("@name=username") or page.ele("@type=email")
            if not email_input:
                print(f"❌ 未找到注册入口: {page.url}")
                return None
            print("ℹ️  检测到登录/注册合一入口")

        # 2. 填写邮箱
        email_input = page.ele("@name=email") or page.ele("@name=username") or page.ele("@type=email")
        if not email_input:
            print("❌ 未找到邮箱输入框")
            return None

        email_input.clear()
        email_input.input(email)
        print(f"📧 已填写邮箱: {email}")

        # 3. 等待 Turnstile 验证
        time.sleep(3)
        token = wait_for_turnstile(page, timeout=60)
        if not token:
            print("⚠️  Turnstile 未通过，尝试继续...")

        # 4. 提交邮箱
        submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Sign Up")
        if submit_btn:
            submit_btn.click()
            print("🖱️  已点击提交")
        else:
            # 尝试按回车
            page.run_js("document.querySelector('form')?.submit()")
            print("⏎  已提交表单")

        time.sleep(5)

        # 5. 等待验证码页面
        code_input = page.ele("@name=code")
        password_input = page.ele("@name=password") or page.ele("@type=password")

        if not code_input and not password_input:
            # 可能需要等待
            for _ in range(10):
                time.sleep(2)
                code_input = page.ele("@name=code")
                password_input = page.ele("@name=password") or page.ele("@type=password")
                if code_input or password_input:
                    break

        if code_input:
            print("✅ 到达验证码页面")
            code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT)
            if not code:
                print("❌ 获取验证码失败")
                return None

            code_input.clear()
            code_input.input(code)
            print(f"🔢 已填写验证码: {code}")

            # 提交验证码
            submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Verify")
            if submit_btn:
                submit_btn.click()
            time.sleep(3)

        # 6. 等待密码页面
        password_input = page.ele("@name=password") or page.ele("@type=password")
        if not password_input:
            for _ in range(10):
                time.sleep(2)
                password_input = page.ele("@name=password") or page.ele("@type=password")
                if password_input:
                    break

        if password_input:
            print("✅ 到达密码页面")
            password_input.clear()
            password_input.input(password)
            print("🔑 已填写密码")

            # 等待 Turnstile
            time.sleep(3)
            token = wait_for_turnstile(page, timeout=60)
            if not token:
                print("⚠️  密码页 Turnstile 未通过，尝试继续...")

            # 提交密码
            submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Sign Up")
            if submit_btn:
                submit_btn.click()
            time.sleep(5)
        else:
            print("⚠️  未检测到密码页面")

        # 7. 检查是否需要邮件验证
        current_url = page.url
        if "verify" in current_url.lower():
            print("📧 需要邮件验证")
            verify_url = get_verification_link(email, timeout=60)
            if verify_url:
                page.get(verify_url)
                page.wait.doc_loaded()
                time.sleep(3)

        # 8. 获取 API Key
        print("🔑 获取 API Key...")
        api_key = wait_for_api_key(page, timeout=30)
        if not api_key:
            print("⚠️  未找到 API Key")
            return None

        print("🧪 验证 API Key 可用性...")
        if not verify_api_key(api_key):
            return None

        save_account(email, password, api_key)

        print(f"🎉 注册成功")
        print(f"   邮箱: {email}")
        print(f"   密码: {password}")
        print(f"   Key : {api_key}")
        return api_key

    except Exception as e:
        print(f"❌ 注册失败: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        if browser:
            try:
                browser.quit()
            except Exception:
                pass


if __name__ == "__main__":
    from mail_provider import create_email
    email, password = create_email()
    register_with_browser_solver(email, password)
