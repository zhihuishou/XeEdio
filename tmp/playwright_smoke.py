from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def run_smoke() -> list[CheckResult]:
    results: list[CheckResult] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) Login page available
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
        title_ok = "登录" in page.title()
        results.append(
            CheckResult("login_page", title_ok, f"title={page.title()!r}")
        )

        # 2) Login flow
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_url("**/tasks", timeout=30000)
        login_ok = page.url.endswith("/tasks")
        results.append(CheckResult("login_flow", login_ok, f"url={page.url}"))

        # 3) Mix page render
        page.goto(f"{BASE_URL}/mix", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("textarea[placeholder*='输入剪辑指令']", timeout=30000)
        mix_ok = "/mix" in page.url
        results.append(CheckResult("mix_page_render", mix_ok, f"url={page.url}"))

        # 4) Chat conversations API
        token = page.evaluate("() => localStorage.getItem('jwt_token')")
        if not token:
            results.append(CheckResult("auth_token", False, "jwt_token missing in localStorage"))
            browser.close()
            return results

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        api = p.request.new_context(base_url=BASE_URL, extra_http_headers=headers)

        resp_list = api.get("/api/chat/conversations?page=1&page_size=5")
        list_ok = resp_list.status == 200
        list_body = resp_list.json() if list_ok else {"status": resp_list.status}
        results.append(
            CheckResult("chat_conversation_list_api", list_ok, json.dumps(list_body, ensure_ascii=False)[:300])
        )

        # 5) Chat send API
        payload = {"message": "smoke test message", "asset_ids": []}
        resp_send = api.post("/api/chat/send", data=json.dumps(payload))
        send_ok = resp_send.status == 200
        send_body = resp_send.json() if send_ok else {"status": resp_send.status}
        conversation_id = send_body.get("conversation_id") if isinstance(send_body, dict) else None
        details = f"status={resp_send.status}, conversation_id={conversation_id}"
        results.append(CheckResult("chat_send_api", send_ok and bool(conversation_id), details))

        browser.close()
    return results


if __name__ == "__main__":
    report = run_smoke()
    failed = [r for r in report if not r.ok]
    for r in report:
        flag = "PASS" if r.ok else "FAIL"
        print(f"[{flag}] {r.name}: {r.detail}")
    if failed:
        sys.exit(1)
