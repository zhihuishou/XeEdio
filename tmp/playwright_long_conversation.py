from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"
TURNS = [
    "你好，我想做一个偏快节奏的产品混剪。",
    "先给我一个30秒版本，并突出开场三秒记忆点。",
    "如果信息不足，请明确问我还差什么。",
    "请总结一下你当前记住的上下文。",
    "基于当前上下文给我下一步可执行建议。",
    "再用一句话确认你会如何继续。",
]


@dataclass
class TurnResult:
    turn: int
    sent: str
    got_reply: bool
    detail: str


def run() -> tuple[list[TurnResult], dict]:
    turn_results: list[TurnResult] = []
    report: dict = {"conversation_id": None, "messages_total": 0, "role_counts": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_url("**/tasks", timeout=30000)

        page.goto(f"{BASE_URL}/mix", wait_until="domcontentloaded", timeout=30000)
        page.click("button:has-text('新建对话')", timeout=30000)
        page.wait_for_timeout(600)

        # Ensure no residual "processing" lock from previous state.
        page.evaluate(
            """() => {
                const node = document.querySelector('[x-data]');
                if (node && node.__x && node.__x.$data) {
                    node.__x.$data.processing = false;
                }
            }"""
        )

        for idx, text in enumerate(TURNS, start=1):
            textarea = page.locator("textarea[placeholder*='输入剪辑指令']")
            textarea.fill(text)
            send_btn = page.locator("button:has(svg path[d*='M12 19l9 2-9-18'])")
            send_btn.click()

            got_reply = False
            detail = "no assistant reply observed"
            try:
                page.wait_for_function(
                    """() => {
                        const node = document.querySelector('[x-data]');
                        if (!node || !node.__x || !node.__x.$data) return false;
                        const msgs = node.__x.$data.messages || [];
                        // wait for at least one non-user message after send
                        return msgs.some(m => m.sender !== 'user');
                    }""",
                    timeout=45000,
                )
                # Wait loop complete / input re-enabled.
                page.wait_for_function(
                    """() => {
                        const node = document.querySelector('[x-data]');
                        if (!node || !node.__x || !node.__x.$data) return false;
                        return node.__x.$data.processing === false;
                    }""",
                    timeout=45000,
                )
                got_reply = True
                detail = "assistant/system response observed"
            except PWTimeoutError:
                pass

            turn_results.append(TurnResult(idx, text, got_reply, detail))
            page.wait_for_timeout(500)

        token = page.evaluate("() => localStorage.getItem('jwt_token')")
        conv_id = page.evaluate(
            """() => {
                const node = document.querySelector('[x-data]');
                if (!node || !node.__x || !node.__x.$data) return null;
                return node.__x.$data.chatConversationId || null;
            }"""
        )
        report["conversation_id"] = conv_id

        if token and conv_id:
            api = p.request.new_context(
                base_url=BASE_URL,
                extra_http_headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            detail = api.get(f"/api/chat/conversations/{conv_id}")
            if detail.status == 200:
                payload = detail.json()
                messages = payload.get("messages") or []
                report["messages_total"] = len(messages)
                roles = [m.get("role") for m in messages]
                report["role_counts"] = {r: roles.count(r) for r in sorted(set(roles))}

        browser.close()
    return turn_results, report


if __name__ == "__main__":
    turns, summary = run()
    failed = []
    for t in turns:
        status = "PASS" if t.got_reply else "FAIL"
        print(f"[{status}] TURN {t.turn}: {t.detail}")
        if not t.got_reply:
            failed.append(t.turn)
    print("[INFO] SUMMARY", json.dumps(summary, ensure_ascii=False))
    if failed:
        sys.exit(1)
