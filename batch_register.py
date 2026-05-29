#!/usr/bin/env python3
"""批量注册测试 - 运行 20 次"""
import sys, os, time, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
os.environ['PROXY_SERVERS'] = ''  # 禁用代理，Turnstile 对代理IP敏感

from mail_provider import create_email
from tavily_browser_solver import register_with_browser_solver, save_account

results = []
success = 0
fail = 0

for i in range(1, 21):
    print(f"\n{'='*60}", flush=True)
    print(f"[{i}/20] 开始注册... {datetime.now().strftime('%H:%M:%S')}", flush=True)
    print('='*60, flush=True)
    
    t0 = time.time()
    email = pw = api_key = None
    try:
        email, pw = create_email(service="tavily")
        api_key = register_with_browser_solver(email, pw)
        elapsed = time.time() - t0
        if api_key:
            success += 1
            save_account(email, pw, api_key)
            print(f"\n✅ [{i}/20] 成功 ({elapsed:.0f}s) {api_key}", flush=True)
        else:
            fail += 1
            print(f"\n❌ [{i}/20] 失败 ({elapsed:.0f}s)", flush=True)
    except Exception as e:
        elapsed = time.time() - t0
        fail += 1
        print(f"\n💥 [{i}/20] 异常: {e} ({elapsed:.0f}s)", flush=True)
    
    results.append({"i": i, "ok": bool(api_key), "email": email, "key": api_key, "t": round(elapsed)})
    print(f"📊 统计: {success}成功 / {fail}失败 / {i}总 = {success/(i)*100:.0f}%", flush=True)
    if i < 20: time.sleep(3)

print(f"\n{'='*60}", flush=True)
print(f"📋 最终: {success}/20 成功 ({success/20*100:.0f}%)", flush=True)
for r in results:
    print(f"  {r['i']:2d}. {'✅' if r['ok'] else '❌'} {r.get('key','N/A')[:30] if r.get('key') else '---'}", flush=True)

fn = f"batch_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(fn, 'w') as f:
    json.dump({"ts": datetime.now().isoformat(), "success": success, "total": 20, "results": results}, f, indent=2)
print(f"\n报告: {fn}", flush=True)