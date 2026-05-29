"""
使用 DrissionPage + 真实 Chrome 完成 Tavily 注册
参考 Grok 注册机方案：连接真实浏览器，Turnstile 自动通过
"""
import os
import re
import sys
import time
import threading
import requests as std_requests

from DrissionPage import Chromium, ChromiumOptions
from config import (
    EMAIL_CODE_TIMEOUT,
    REGISTER_HEADLESS,
    PROXY_SERVERS,
)
from mail_provider import get_email_code, get_verification_link

TURNSTILE_SITEKEY = "0x4AAAAAAAQFNSW6xordsuIq"
_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "accounts.txt")
_SAVE_LOCK = threading.Lock()


_BROWSER_COUNTER = 0
_BROWSER_COUNTER_LOCK = threading.Lock()
_PROXY_COUNTER = 0
_PROXY_COUNTER_LOCK = threading.Lock()
_LOCAL_PROXY_BASE_PORT = 18080
_PROXY_FORWARDER_PROCS = []


def _get_next_proxy():
    """轮询获取下一个代理（host, port, user, pass）"""
    global _PROXY_COUNTER
    if not PROXY_SERVERS:
        return None
    with _PROXY_COUNTER_LOCK:
        proxy_str = PROXY_SERVERS[_PROXY_COUNTER % len(PROXY_SERVERS)]
        _PROXY_COUNTER += 1
    parts = proxy_str.split(":")
    if len(parts) == 4:
        return parts  # host, port, user, pass
    elif len(parts) == 2:
        return parts[0], parts[1], None, None
    return None


def _start_local_proxy(local_port, upstream_host, upstream_port, user, password):
    """启动本地代理转发器进程"""
    import subprocess
    forwarder_script = os.path.join(_HERE, "proxy_forwarder.py")
    upstream = f"{upstream_host}:{upstream_port}:{user}:{password}"
    proc = subprocess.Popen(
        [sys.executable, forwarder_script, str(local_port), upstream],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    _PROXY_FORWARDER_PROCS.append(proc)
    time.sleep(1)  # 等待启动
    return proc


def create_browser_options():
    """创建浏览器配置（每个实例独立端口、用户数据目录、代理）"""
    global _BROWSER_COUNTER
    with _BROWSER_COUNTER_LOCK:
        _BROWSER_COUNTER += 1
        instance_id = _BROWSER_COUNTER

    options = ChromiumOptions()
    # 用不同端口范围避免冲突
    port = 9222 + instance_id * 10
    options.set_local_port(port)
    options.set_timeouts(base=1)
    if REGISTER_HEADLESS:
        options.headless()
    # 每个实例独立用户数据目录
    user_data = os.path.join(_HERE, ".browser_profiles", f"profile_{instance_id}")
    options.set_user_data_path(user_data)
    # 反检测
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--no-first-run')
    options.set_argument('--no-default-browser-check')
    options.set_argument('--disable-infobars')
    options.set_argument('--window-size=1280,900')
    # 代理（通过本地转发器）
    proxy_info = _get_next_proxy()
    if proxy_info:
        host, port_str, user, pwd = proxy_info
        local_port = _LOCAL_PROXY_BASE_PORT + instance_id
        if user:
            _start_local_proxy(local_port, host, port_str, user, pwd)
            options.set_argument(f'--proxy-server=http://127.0.0.1:{local_port}')
            print(f"  [proxy] 本地转发 127.0.0.1:{local_port} → {host}:{port_str}")
        else:
            options.set_argument(f'--proxy-server=http://{host}:{port_str}')
            print(f"  [proxy] 直连 {host}:{port_str}")
    # 加载 Turnstile 反检测扩展
    extension_path = os.path.join(_HERE, "turnstilePatch")
    if os.path.exists(extension_path):
        options.add_extension(extension_path)
    return options


def start_browser():
    """启动 Chrome 浏览器"""
    # 清理旧的浏览器 profile（避免残留 session）
    import shutil
    profiles_dir = os.path.join(_HERE, ".browser_profiles")
    if os.path.exists(profiles_dir):
        shutil.rmtree(profiles_dir, ignore_errors=True)
    proxy_ext_dir = os.path.join(_HERE, ".proxy_ext")
    if os.path.exists(proxy_ext_dir):
        shutil.rmtree(proxy_ext_dir, ignore_errors=True)

    options = create_browser_options()
    browser = Chromium(options)
    tabs = browser.get_tabs()
    page = tabs[-1] if tabs else browser.new_tab()
    return browser, page


def cleanup_proxy_forwarders():
    """清理所有代理转发器进程"""
    for proc in _PROXY_FORWARDER_PROCS:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except:
            try:
                proc.kill()
            except:
                pass
    _PROXY_FORWARDER_PROCS.clear()


def extract_signup_url(html):
    """从登录页提取注册入口"""
    match = re.search(r'href="(/u/signup/identifier[^"]*)"', html)
    if not match:
        return None
    return f"https://auth.tavily.com{match.group(1)}"


def _try_click_turnstile(page):
    """尝试点击 Turnstile checkbox，多种方式。返回 True 如果点击成功。"""
    # 方法1: shadow DOM（Grok 注册机方式）
    try:
        cf_input = page.ele("@name=cf-turnstile-response")
        if cf_input:
            parent = cf_input.parent()
            if parent:
                try:
                    sr = parent.shadow_root
                    if sr:
                        iframe = sr.ele("tag:iframe")
                        if iframe:
                            body = iframe.ele("tag:body")
                            if body:
                                body_sr = body.shadow_root
                                if body_sr:
                                    btn = body_sr.ele("tag:input")
                                    if btn:
                                        btn.click()
                                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # 方法2: 直接找 iframe 通过 src
    try:
        iframe = page.ele('tag:iframe@src*://challenges.cloudflare.com')
        if iframe:
            frame = iframe.frame
            if frame:
                try:
                    btn = frame.ele('tag:input')
                    if btn:
                        btn.click()
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # 方法3: JS 注入点击（通过所有 iframe）
    try:
        clicked = page.run_js("""
            try {
                var iframes = document.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    try {
                        var doc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                        if (!doc) continue;
                        var inputs = doc.querySelectorAll('input[type="checkbox"], input');
                        for (var j = 0; j < inputs.length; j++) {
                            if (inputs[j].type === 'checkbox' || inputs[j].type === 'button') {
                                inputs[j].click();
                                return true;
                            }
                        }
                    } catch(e) { continue; }
                }
            } catch(e) {}
            return false;
        """)
        if clicked:
            return True
    except Exception:
        pass

    return False


def poll_turnstile_token(page, timeout=120):
    """轮询获取 Turnstile token

    真实 Chrome 下 Turnstile 通常会自动通过。
    卡住时尝试点击 checkbox。
    """
    start_time = time.time()
    click_count = 0
    max_clicks = 20

    while time.time() - start_time < timeout:
        # 检查 token
        try:
            token = page.run_js("""
                try {
                    var byInput = String(
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
                print(f"  [turnstile] Token 已获取，长度={len(token)}")
                return token
        except Exception:
            pass

        # 每次循环都尝试点击（频率更高）
        if click_count < max_clicks:
            if _try_click_turnstile(page):
                click_count += 1
                print(f"  [turnstile] 点击 checkbox #{click_count}")

        time.sleep(1)

    print(f"  [turnstile] 超时 ({timeout}s)，点击了 {click_count} 次")
    return None


def wait_for_turnstile_and_submit(page, timeout=120):
    """等待 Turnstile 通过后自动提交表单（参考 Grok 注册机流程）"""
    print("🔐 等待 Turnstile 验证...")
    start_time = time.time()
    click_count = 0
    max_clicks = 15
    last_click_time = 0

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
                print(f"✅ Turnstile 已通过 (token: {token[:30]}...)")
                # token 到手，提交表单
                submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Sign Up")
                if submit_btn:
                    submit_btn.click()
                    print("🖱️  已提交表单")
                return token
        except Exception:
            pass

        # 检查是否已经跳转（Turnstile 自动提交了）
        current_url = page.url
        if "sign-in" not in current_url and "signup" not in current_url:
            print(f"✅ 页面已跳转: {current_url}")
            return "redirected"

        # 每隔 3 秒尝试点击 checkbox
        now = time.time()
        if now - last_click_time >= 3 and click_count < max_clicks:
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
                                last_click_time = now
                                print(f"  [turnstile] 点击 checkbox #{click_count}")
                        except Exception:
                            pass
            except Exception:
                pass

        time.sleep(1)

    print("❌ Turnstile 验证超时")
    return None


def extract_api_key(page):
    """从页面提取 API Key"""
    try:
        html = page.html
    except Exception:
        return None
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


def wait_for_element(page, selector, timeout=30):
    """等待元素出现"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            ele = page.ele(selector)
            if ele:
                return ele
        except Exception:
            pass
        time.sleep(1)
    return None


def register_with_browser_solver(email, password):
    """使用 DrissionPage + 真实 Chrome 注册"""
    print(f"🌐 使用 DrissionPage 浏览器模式注册: {email}")

    browser = None
    try:
        browser, page = start_browser()
        print("✅ 浏览器已启动")

        # 1. 访问登录页
        page.get("https://app.tavily.com/sign-in")
        time.sleep(20)  # 代理加载更慢，需要更长等待

        # 2. 点击 Sign up 链接切换到注册表单（用 JS 点击，DrissionPage 选择器不可靠）
        # 重试几次，因为代理加载可能更慢
        clicked = False
        for attempt in range(3):
            clicked = page.run_js('''
              var link = document.querySelector('a[href*="signup"]');
              if (link) {
                link.click();
                return true;
              }
              return false;
            ''')
            if clicked:
                break
            time.sleep(3)
        
        if clicked:
            print("🖱️  已切换到注册表单")
            time.sleep(5)
        else:
            print("⚠️  未找到 Sign up 链接，可能已在注册表单")

        # 3. 等待 Turnstile 通过
        print("🔐 等待 Turnstile 验证...")
        token = poll_turnstile_token(page, timeout=120)
        if token:
            print(f"✅ Turnstile 已通过 (token: {token[:30]}...)")
        else:
            print("⚠️  Turnstile 未通过，尝试继续...")

        # 4. 填写邮箱
        email_input = page.ele("@name=username") or page.ele("@name=email") or page.ele("@id=username") or page.ele("@type=email")
        if not email_input:
            print("❌ 未找到邮箱输入框")
            return None

        email_input.clear()
        email_input.input(email)
        print(f"📧 已填写邮箱: {email}")

        # 5. 提交表单
        time.sleep(1)
        submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Sign Up")
        if submit_btn:
            submit_btn.click()
            print("🖱️  已提交表单")
        time.sleep(5)

        # 4. 等待密码页面（Tavily 先要密码再要验证码）
        print("⏳ 等待密码页面...")
        time.sleep(2)
        password_input = wait_for_element(page, "@name=password", timeout=10)
        if not password_input:
            password_input = page.ele("@type=password")

        if password_input:
            # 判断是注册密码页还是登录密码页
            page_title = page.title.lower()
            page_url = page.url.lower()
            is_login = "login" in page_title or "log in" in page_title or "/login/" in page_url
            if is_login:
                print("⚠️  邮箱已被注册，进入了登录页面")
                # TODO: 可以换邮箱重试
                return None
            
            print("✅ 到达注册密码页面")
            password_input.clear()
            password_input.input(password)
            print("🔑 已填写密码")

            # 点击提交按钮
            time.sleep(1)
            submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Create Account")
            if submit_btn:
                submit_btn.click()
                print("🖱️  已提交密码")
            time.sleep(5)
        else:
            print("⚠️  未检测到密码页面")

        # 5. 等待验证码页面
        print("⏳ 等待验证码页面...")
        code_input = wait_for_element(page, "@name=code", timeout=10)
        if code_input:
            print("✅ 到达验证码页面")
            code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT)
            if not code:
                print("❌ 获取验证码失败")
                return None

            code_input.clear()
            code_input.input(code)
            print(f"🔢 已填写验证码: {code}")

            # 点击提交
            time.sleep(1)
            submit_btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Verify")
            if submit_btn:
                submit_btn.click()
                print("🖱️  已提交验证码")
            time.sleep(5)
        else:
            print("⚠️  未检测到验证码页面")

        # 6. 密码提交后的页面状态
        time.sleep(3)
        current_url = page.url
        print(f"📍 密码提交后 URL: {current_url}")
        need_verify = False

        # 检查 URL 或页面内容是否提示需要验证
        if "verify" in current_url.lower():
            need_verify = True
        else:
            html = page.html
            if any(kw in html.lower() for kw in ["verify your email", "confirm your email", "验证邮箱", "确认邮箱", "email verification"]):
                need_verify = True
            resend = page.ele("text():Resend") or page.ele("text():resend") or page.ele("text():重新发送")
            if resend:
                need_verify = True

        if need_verify:
            print("📧 需要邮件验证")
            resend = page.ele("text():Resend") or page.ele("text():resend") or page.ele("text():重新发送")
            if resend:
                resend.click()
                print("🖱️  已点击重新发送验证邮件")
                time.sleep(3)

            verify_url = get_verification_link(email, timeout=120)
            if verify_url:
                print(f"✅ 获取到验证链接: {verify_url[:50]}...")
                # 在浏览器中打开验证链接，但设置超时避免卡死在 Auth0 页面
                try:
                    page.get(verify_url, timeout=15)
                except Exception:
                    pass  # 超时是预期的，Auth0 验证页可能加载慢
                time.sleep(5)
                print(f"📍 验证后 URL: {page.url[:60]}")

                # 导航回 dashboard（session 仍有效）
                page.get("https://app.tavily.com/home")
                page.wait.doc_loaded()
                time.sleep(8)
                print(f"📍 Dashboard URL: {page.url}")
            else:
                print("⚠️  未获取到验证链接，尝试继续...")
        else:
            print("✅ 无需邮件验证")
            # 密码提交后直接到了 dashboard/首页，不需要邮件验证
            # 但 dashboard 首页可能不显示 API Key，需要主动导航到设置页
            if "app.tavily.com" in current_url:
                # 已登录状态，直接去 dashboard 获取 key
                print("🔗 已在 app.tavily.com，尝试导航到 dashboard...")
                page.get("https://app.tavily.com/home")
                page.wait.doc_loaded()
                time.sleep(5)
                print(f"📍 Dashboard URL: {page.url}")
            elif "sign-in" in current_url.lower() or "login" in current_url.lower():
                # 密码提交后回到了登录页 — 说明注册成功但需要登录
                print("🔄 密码提交后回到登录页，尝试重新登录...")
                re_email = wait_for_element(page, "@name=username", timeout=10)
                if re_email:
                    re_email.clear()
                    re_email.input(email)
                    time.sleep(1)
                    btn = page.ele("@type=submit")
                    if btn:
                        btn.click()
                    time.sleep(5)
                re_pw = wait_for_element(page, "@name=password", timeout=10)
                if re_pw:
                    re_pw.clear()
                    re_pw.input(password)
                    time.sleep(1)
                    btn = page.ele("@type=submit") or page.ele("text():Continue") or page.ele("text():Log in")
                    if btn:
                        btn.click()
                    time.sleep(8)
                print(f"📍 重新登录后 URL: {page.url}")
                # 登录后导航到 dashboard
                page.get("https://app.tavily.com/home")
                page.wait.doc_loaded()
                time.sleep(5)
            else:
                # 未知页面，尝试导航到 dashboard
                print(f"⚠️  未知页面状态，尝试导航到 dashboard...")
                page.get("https://app.tavily.com/home")
                page.wait.doc_loaded()
                time.sleep(5)

        # 7. 获取 API Key
        print("🔑 获取 API Key...")
        print(f"📍 当前 URL: {page.url}")
        api_key = wait_for_api_key(page, timeout=30)
        if not api_key:
            # 尝试从当前页面找
            try:
                api_key_ele = page.ele("text():tvly-")
                if api_key_ele:
                    match = re.search(r'(tvly-[a-zA-Z0-9_-]{20,})', api_key_ele.text)
                    if match:
                        api_key = match.group(1)
            except Exception:
                pass

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
