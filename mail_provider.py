"""
统一邮箱 provider 抽象。
当前支持：
1. Cloudflare 自定义邮件 API
2. DuckMail API
3. Cloudflare Temp Email (cloudflare_temp_email 项目)
"""
import email
import email.policy
import html as html_module
import random
import re
import string
import time
import urllib3

import requests as std_requests

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import (
    CF_TEMP_EMAIL_ADMIN_PASSWORD,
    CF_TEMP_EMAIL_API_URL,
    CF_TEMP_EMAIL_DOMAIN,
    CF_TEMP_EMAIL_DOMAINS,
    DUCKMAIL_API_KEY,
    DUCKMAIL_API_URL,
    DUCKMAIL_DOMAIN,
    DUCKMAIL_DOMAINS,
    EMAIL_API_TOKEN,
    EMAIL_API_URL,
    EMAIL_DOMAIN,
    EMAIL_DOMAINS,
    EMAIL_POLL_INTERVAL,
    EMAIL_PROVIDER,
)

_DUCKMAIL_DOMAIN_PRIORITY = (
    "baldur.edu.kg",
    "duckmail.sbs",
)
_DUCKMAIL_DOMAIN_CACHE = None
_DUCKMAIL_MAILBOX_CACHE = {}
_SELECTED_DOMAIN = ""
_SUPPORTED_SERVICES = ("tavily", "firecrawl", "exa")


def rand_str(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def get_configured_domains():
    """返回当前 provider 在配置里声明的可选域名。"""
    if EMAIL_PROVIDER == "duckmail":
        return DUCKMAIL_DOMAINS[:]
    if EMAIL_PROVIDER == "cloudflare_temp_email":
        return CF_TEMP_EMAIL_DOMAINS[:]
    return EMAIL_DOMAINS[:]

def get_active_domain():
    """返回当前实际使用的域名。"""
    if _SELECTED_DOMAIN:
        return _SELECTED_DOMAIN

    configured = get_configured_domains()
    if configured:
        return configured[0]

    if EMAIL_PROVIDER == "duckmail":
        return DUCKMAIL_DOMAIN
    if EMAIL_PROVIDER == "cloudflare_temp_email":
        return CF_TEMP_EMAIL_DOMAIN
    return EMAIL_DOMAIN

def set_selected_domain(domain):
    """设置本轮运行使用的域名。"""
    global _SELECTED_DOMAIN
    _SELECTED_DOMAIN = (domain or "").strip()


def _normalize_service(service):
    service = (service or "tavily").strip().lower()
    if service not in _SUPPORTED_SERVICES:
        return "tavily"
    return service


def _username_prefix(service):
    service = _normalize_service(service)
    if service == "firecrawl":
        return "fc"
    if service == "exa":
        return "exa"
    return "tavily"


def create_email(service="tavily"):
    """按当前 provider 生成邮箱与强密码。"""
    password = f"Tv{rand_str(6)}{random.randint(100, 999)}!A"
    prefix = _username_prefix(service)

    if EMAIL_PROVIDER == "duckmail":
        email = _create_duckmail_mailbox(password, prefix)
    elif EMAIL_PROVIDER == "cloudflare_temp_email":
        email = _create_cf_temp_email_mailbox(prefix)
    else:
        username = f"{prefix}-{rand_str()}"
        email = f"{username}@{get_active_domain()}"

    print(f"✅ 邮箱({EMAIL_PROVIDER}): {email}")
    return email, password


def get_verification_link(email, timeout=120):
    """等待验证邮件并提取验证链接。"""
    print(f"⏳ 等待验证邮件（最多 {timeout} 秒）...", end="", flush=True)
    return _poll_mailbox(
        email=email,
        timeout=timeout,
        extractor=_extract_verification_link,
        found_message="\n✅ 找到验证链接",
        timeout_message="\n❌ 验证邮件超时",
        error_prefix="检查验证邮件失败",
        dot_progress=True,
    )


def get_email_code(email, timeout=120, service="tavily"):
    """等待邮箱里的 6 位验证码。"""
    print(f"📨 等待邮箱验证码（最多 {timeout} 秒）...")
    return _poll_mailbox(
        email=email,
        timeout=timeout,
        extractor=lambda message: _extract_email_code(message, service=service),
        found_message="✅ 收到 6 位验证码",
        timeout_message="❌ 等待邮箱验证码超时",
        error_prefix="读取邮箱验证码失败",
        dot_progress=False,
    )


def _poll_mailbox(email, timeout, extractor, found_message, timeout_message, error_prefix, dot_progress):
    start_time = time.time()
    seen_ids = set()

    while time.time() - start_time < timeout:
        try:
            for message in _iter_messages(email):
                message_id = _message_id(message)
                if message_id and message_id in seen_ids:
                    continue
                if message_id:
                    seen_ids.add(message_id)

                result = extractor(message)
                if result:
                    print(found_message)
                    return result
        except Exception as exc:
            print(f"⚠️  {error_prefix}: {exc}")

        time.sleep(EMAIL_POLL_INTERVAL)
        if dot_progress:
            print(".", end="", flush=True)

    print(timeout_message)
    return None


def _extract_verification_link(message):
    subject = (message.get("subject") or "").lower()
    sender = (message.get("from") or message.get("message_from") or "").lower()
    content = _message_content(message)
    urls = [
        html_module.unescape(raw).rstrip(").,;")
        for raw in re.findall(r'https://[^\s<>"\']+', content, re.IGNORECASE)
    ]

    # 过滤掉图片和静态资源链接
    skip_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.css', '.js')
    skip_domains = ('cdn.auth0.com', 'cdn.', 'static.', 'images.', 'img.')
    urls = [
        url for url in urls
        if not any(url.lower().endswith(ext) for ext in skip_extensions)
        and not any(domain in url.lower() for domain in skip_domains)
    ]

    primary_link_hints = ("verif", "confirm", "magic", "auth", "callback", "signin", "signup")
    primary_host_hints = ("tavily", "firecrawl", "clerk", "stytch", "auth", "login")
    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in primary_link_hints) and any(host in lowered for host in primary_host_hints):
            return url

    combined = f"{sender} {subject} {content[:4000]}".lower()
    message_hints = ("verify", "verification", "confirm", "magic link", "sign in", "tavily", "firecrawl")
    if not any(token in combined for token in message_hints):
        return None

    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in primary_link_hints):
            return url

    return None


def _extract_email_code(message, service="tavily"):
    service = _normalize_service(service)
    subject = (message.get("subject") or "").lower()
    text = message.get("text") or ""
    content = _message_content(message)
    combined = f"{subject}\n{content}".lower()

    if service == "exa":
        if "exa" not in combined:
            return None
        if "verification code" not in combined and "sign in" not in combined:
            return None
        for source in (text, content):
            match = re.search(
                r"verification code(?:\s+for\s+exa)?(?:\s+is)?[^0-9]*(\d{6})",
                source,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
    else:
        if "verify your identity" not in subject and "verify" not in subject and "tavily" not in combined:
            return None

    for source in (text, content):
        match = re.search(r"\b(\d{6})\b", source)
        if match:
            return match.group(1)
    return None


def _iter_messages(email):
    if EMAIL_PROVIDER == "duckmail":
        yield from _duckmail_iter_messages(email)
        return

    if EMAIL_PROVIDER == "cloudflare_temp_email":
        yield from _cf_temp_email_iter_messages(email)
        return

    yield from _cloudflare_iter_messages(email)


def _cloudflare_iter_messages(email):
    response = std_requests.get(
        f"{EMAIL_API_URL}/messages",
        params={"address": email},
        headers={"Authorization": f"Bearer {EMAIL_API_TOKEN}"},
        timeout=10,
    )
    response.raise_for_status()

    for message in response.json().get("messages", []):
        yield message


def _duckmail_iter_messages(email):
    token = _duckmail_get_token(email)
    response = _duckmail_request("GET", "/messages", token=token)

    if response.status_code == 401:
        token = _duckmail_get_token(email, refresh=True)
        response = _duckmail_request("GET", "/messages", token=token)

    response.raise_for_status()

    for message in response.json().get("hydra:member", []):
        message_id = message.get("id")
        if not message_id:
            continue

        detail = _duckmail_request("GET", f"/messages/{message_id}", token=token)
        if detail.status_code == 401:
            token = _duckmail_get_token(email, refresh=True)
            detail = _duckmail_request("GET", f"/messages/{message_id}", token=token)
        detail.raise_for_status()
        yield detail.json()


def _create_duckmail_mailbox(password, prefix):
    domain = _choose_duckmail_domain()

    for _ in range(5):
        username = f"{prefix}-{rand_str()}"
        email = f"{username}@{domain}"
        response = _duckmail_request(
            "POST",
            "/accounts",
            json={"address": email, "password": password},
            use_api_key=True,
        )

        if response.status_code == 201:
            account = response.json()
            token = _duckmail_issue_token(email, password)
            _DUCKMAIL_MAILBOX_CACHE[email] = {
                "account_id": account.get("id", ""),
                "password": password,
                "token": token,
            }
            return email

        if response.status_code not in (409, 422):
            response.raise_for_status()

        message = _response_error_message(response).lower()
        if "exists" in message or "already" in message or response.status_code == 409:
            continue

        raise RuntimeError(f"DuckMail 创建邮箱失败: {_response_error_message(response)}")

    raise RuntimeError("DuckMail 邮箱创建失败：随机地址重复次数过多")


def _choose_duckmail_domain():
    domains = _duckmail_domains()
    selected = get_active_domain()
    configured = get_configured_domains()

    if selected:
        if selected not in domains:
            raise RuntimeError(
                f"配置的 DuckMail 域名不可用: {selected}，当前可用域名: {', '.join(domains)}"
            )
        return selected

    for domain in configured:
        if domain in domains:
            return domain

    for domain in _DUCKMAIL_DOMAIN_PRIORITY:
        if domain in domains:
            return domain

    return domains[0]


def _duckmail_domains():
    global _DUCKMAIL_DOMAIN_CACHE
    if _DUCKMAIL_DOMAIN_CACHE is not None:
        return _DUCKMAIL_DOMAIN_CACHE

    response = _duckmail_request("GET", "/domains", use_api_key=True)
    response.raise_for_status()
    domains = [
        item.get("domain")
        for item in response.json().get("hydra:member", [])
        if item.get("domain")
    ]

    if not domains:
        raise RuntimeError("DuckMail 未返回可用域名")

    _DUCKMAIL_DOMAIN_CACHE = domains
    return domains


def _duckmail_get_token(email, refresh=False):
    mailbox = _DUCKMAIL_MAILBOX_CACHE.get(email)
    if not mailbox:
        raise RuntimeError("DuckMail 邮箱上下文不存在，请重新生成邮箱后再试")

    if mailbox.get("token") and not refresh:
        return mailbox["token"]

    mailbox["token"] = _duckmail_issue_token(email, mailbox["password"])
    return mailbox["token"]


def _duckmail_issue_token(email, password):
    response = _duckmail_request(
        "POST",
        "/token",
        json={"address": email, "password": password},
    )
    response.raise_for_status()

    token = response.json().get("token")
    if not token:
        raise RuntimeError("DuckMail 登录成功但未返回 token")
    return token


def _duckmail_request(method, path, token=None, use_api_key=False, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif use_api_key and DUCKMAIL_API_KEY:
        headers["Authorization"] = f"Bearer {DUCKMAIL_API_KEY}"

    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json")

    return std_requests.request(
        method,
        f"{DUCKMAIL_API_URL.rstrip('/')}{path}",
        headers=headers,
        timeout=kwargs.pop("timeout", 15),
        **kwargs,
    )


def _message_id(message):
    return message.get("id") or message.get("msgid")


def _message_content(message):
    html = message.get("html") or ""
    if isinstance(html, list):
        html = " ".join(str(item) for item in html)
    text = message.get("text") or ""
    return f"{html} {text}"


# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Temp Email provider
# ─────────────────────────────────────────────────────────────────────────────

# 缓存邮箱地址 -> jwt 的映射
_CF_TEMP_EMAIL_JWT_CACHE = {}


def _create_cf_temp_email_mailbox(prefix):
    """通过 cloudflare_temp_email 的 admin API 创建邮箱。"""
    if not CF_TEMP_EMAIL_API_URL:
        raise RuntimeError("未配置 CF_TEMP_EMAIL_API_URL")
    if not CF_TEMP_EMAIL_ADMIN_PASSWORD:
        raise RuntimeError("未配置 CF_TEMP_EMAIL_ADMIN_PASSWORD")

    domain = get_active_domain()
    if not domain:
        raise RuntimeError("未配置 CF_TEMP_EMAIL_DOMAIN")

    username = f"{prefix}-{rand_str()}"
    api_url = f"{CF_TEMP_EMAIL_API_URL.rstrip('/')}/admin/new_address"

    print(f"  [cf_temp_email] 创建邮箱: {username}@{domain}")
    print(f"  [cf_temp_email] API: {api_url}")

    try:
        response = std_requests.post(
            api_url,
            json={
                "enablePrefix": True,
                "name": username,
                "domain": domain,
            },
            headers={
                "x-admin-auth": CF_TEMP_EMAIL_ADMIN_PASSWORD,
                "Content-Type": "application/json",
            },
            timeout=15,
            verify=False,
        )
    except std_requests.exceptions.SSLError:
        print("  [cf_temp_email] SSL 验证失败，重试 (verify=False)...")
        response = std_requests.post(
            api_url,
            json={
                "enablePrefix": True,
                "name": username,
                "domain": domain,
            },
            headers={
                "x-admin-auth": CF_TEMP_EMAIL_ADMIN_PASSWORD,
                "Content-Type": "application/json",
            },
            timeout=15,
            verify=False,
        )

    print(f"  [cf_temp_email] 响应状态: {response.status_code}")
    if response.status_code != 200:
        print(f"  [cf_temp_email] 响应内容: {response.text[:500]}")
    response.raise_for_status()

    data = response.json()
    addr = data.get("address")
    jwt_token = data.get("jwt")

    if not addr or not jwt_token:
        raise RuntimeError(f"cloudflare_temp_email 创建邮箱失败: {data}")

    print(f"  [cf_temp_email] 创建成功: {addr}")
    _CF_TEMP_EMAIL_JWT_CACHE[addr] = jwt_token
    return addr


def _cf_temp_email_iter_messages(addr):
    """通过 cloudflare_temp_email 的 /api/mails 读取邮件。

    返回的每条 message 兼容原有格式：包含 id / subject / from / text / html 字段。
    cloudflare_temp_email 返回的是 raw RFC822，需要客户端解析。
    """
    jwt_token = _CF_TEMP_EMAIL_JWT_CACHE.get(addr)
    if not jwt_token:
        raise RuntimeError(
            f"cloudflare_temp_email 未找到 {addr} 的 JWT，请重新创建邮箱"
        )

    api_url = f"{CF_TEMP_EMAIL_API_URL.rstrip('/')}/api/mails"
    print(f"  [cf_temp_email] 读取邮件: {addr}")

    try:
        response = std_requests.get(
            api_url,
            params={"limit": 50, "offset": 0},
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json",
            },
            timeout=15,
            verify=False,
        )
    except std_requests.exceptions.SSLError:
        print("  [cf_temp_email] SSL 验证失败，重试 (verify=False)...")
        response = std_requests.get(
            api_url,
            params={"limit": 50, "offset": 0},
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json",
            },
            timeout=15,
            verify=False,
        )

    print(f"  [cf_temp_email] 邮件响应: {response.status_code}")
    if response.status_code != 200:
        print(f"  [cf_temp_email] 响应内容: {response.text[:500]}")
    response.raise_for_status()

    data = response.json()
    # /api/mails 返回格式: {"results": [...]} 或直接是列表
    raw_mails = data.get("results", data) if isinstance(data, dict) else data

    print(f"  [cf_temp_email] 收到 {len(raw_mails)} 封邮件")
    for raw_mail in raw_mails:
        parsed = _cf_temp_email_parse_raw(raw_mail)
        if parsed:
            yield parsed


def _cf_temp_email_parse_raw(raw_mail):
    """将 cloudflare_temp_email 返回的原始邮件数据解析为标准格式。

    raw_mail 可能包含:
    - raw: RFC822 原始邮件内容
    - source: 同 raw
    - id / mail_id: 邮件 ID
    """
    raw_content = raw_mail.get("raw") or raw_mail.get("source") or ""
    mail_id = raw_mail.get("id") or raw_mail.get("mail_id") or raw_mail.get("msgid")

    if not raw_content:
        # 如果没有 raw 内容，尝试直接使用（可能已经是解析好的格式）
        return {
            "id": mail_id,
            "subject": raw_mail.get("subject", ""),
            "from": raw_mail.get("from") or raw_mail.get("message_from", ""),
            "text": raw_mail.get("text", ""),
            "html": raw_mail.get("html", ""),
        }

    # 解析 RFC822 邮件
    try:
        msg = email.message_from_string(raw_content, policy=email.policy.default)
        subject = str(msg.get("subject", ""))
        from_addr = str(msg.get("from", ""))
        message_id = str(msg.get("message-id", "")) or mail_id

        # 提取正文
        text_body = ""
        html_body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    text_body = part.get_content()
                elif content_type == "text/html":
                    html_body = part.get_content()
        else:
            content_type = msg.get_content_type()
            if content_type == "text/plain":
                text_body = msg.get_content()
            elif content_type == "text/html":
                html_body = msg.get_content()

        return {
            "id": message_id or mail_id,
            "subject": subject,
            "from": from_addr,
            "text": text_body,
            "html": html_body,
        }
    except Exception as e:
        print(f"⚠️  解析邮件失败: {e}")
        return {
            "id": mail_id,
            "subject": "",
            "from": "",
            "text": raw_content[:5000],
            "html": "",
        }


def _response_error_message(response):
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(data, dict):
        return data.get("message") or data.get("detail") or data.get("error") or str(data)
    return str(data)
