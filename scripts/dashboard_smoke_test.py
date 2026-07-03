import os
import sys
import time
import urllib.error
import urllib.request
import json

from playwright.sync_api import Error, sync_playwright


BASE_URL = os.environ.get("ACD_BASE_URL", "http://127.0.0.1:9889")
CHROME = os.environ.get(
    "PLAYWRIGHT_EXECUTABLE_PATH",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
PROJECT_ID = os.environ.get("ACD_TEST_PROJECT", f"e2e-{int(time.time())}")

FAILURES: list[str] = []


def ok(message: str) -> None:
    print(f"OK {message}")


def fail(message: str) -> None:
    print(f"FAIL {message}")
    FAILURES.append(message)


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict | str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return exc.code, raw


def click_text(page, *parts: str) -> bool:
    last_error = None
    for part in parts:
        try:
            page.get_by_text(part, exact=False).first.click(timeout=6000)
            ok(f"clicked {part}")
            return True
        except Error as exc:
            last_error = exc
    fail(f"cannot click {'/'.join(parts)}: {last_error}")
    return False


def wait_text(page, *parts: str, timeout: int = 10000) -> bool:
    last_error = None
    for part in parts:
        try:
            page.get_by_text(part, exact=False).first.wait_for(timeout=timeout)
            ok(f"text visible {part}")
            return True
        except Error as exc:
            last_error = exc
    fail(f"text not visible {'/'.join(parts)}: {last_error}")
    return False


def sample_blueprint(project_id: str) -> str:
    return f"""
[meta]
schema_version = "1"
project_id = "{project_id}"
title = "Dashboard smoke graph"
generated_by = "planner"
generated_at = "2026-05-15T10:00:00Z"

[[items]]
id = "setup"
title = "Smoke setup"
description = "Prepare the smoke test baseline."
category = "feature"
priority = 2
effort_hours = 2.0
depends_on = []
proof_requirements = ["git_commit", "test_pass"]
acceptance_criteria = ["Baseline exists"]
files_of_interest = ["README.md"]

[[items]]
id = "review"
title = "Smoke review"
description = "Review the generated graph."
category = "review"
priority = 3
effort_hours = 4.0
depends_on = ["setup"]
proof_requirements = ["reviewer_signoff"]
acceptance_criteria = ["Review is visible"]
files_of_interest = ["docs/dashboard-e2e-test-cases.md"]
""".strip()


def main() -> int:
    status, _ = request("GET", "/providers")
    if status != 200:
        fail(f"/providers returned {status}")
        return 1
    ok("/providers returns 200")

    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if os.path.exists(CHROME):
            launch_kwargs["executable_path"] = CHROME
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.on("pageerror", lambda err: fail(f"page error: {err}"))
        page.on("console", lambda msg: fail(f"console error: {msg.text}") if msg.type == "error" else None)
        page.on("response", lambda resp: fail(f"HTTP {resp.status} {resp.url}") if resp.status >= 500 else None)

        try:
            page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=30000)
            if "/admin" in page.url:
                ok("root redirects to /admin")
            else:
                fail(f"root did not redirect to /admin: {page.url}")

            routes = [
                "/admin/overview",
                "/admin/tasks",
                "/admin/sessions",
                "/admin/prompts",
                "/admin/approvals",
                "/admin/context",
                "/admin/work-graph",
                "/admin/topology",
                "/admin/providers",
                "/admin/handbook",
            ]
            for route in routes:
                page.goto(f"{BASE_URL}{route}", wait_until="networkidle", timeout=30000)
                body = page.locator("body").inner_text(timeout=10000)
                if "404" in body and "Not Found" in body:
                    fail(f"{route} rendered 404")
                else:
                    ok(f"{route} renders")

            page.goto(f"{BASE_URL}/admin/work-graph", wait_until="networkidle", timeout=30000)
            if click_text(page, "智能生成", "Auto-generate"):
                page.locator("textarea").first.fill("Build a small smoke-test dashboard workflow.")
                textareas = page.locator("textarea")
                if textareas.count() > 1:
                    textareas.nth(1).fill("Use a two item dependency chain.")
                with page.expect_response(lambda r: r.url.endswith("/work-graph/generate"), timeout=15000) as resp_info:
                    click_text(page, "生成", "Generate")
                gen_resp = resp_info.value
                if gen_resp.status >= 400:
                    fail(f"UI generate returned {gen_resp.status}: {gen_resp.text()}")
                else:
                    terminal_id = gen_resp.json()["terminal_id"]
                    ok("UI generate request returned terminal_id")
                    status, _ = request(
                        "POST",
                        f"/work-graph/blueprint/{terminal_id}",
                        {"toml_content": sample_blueprint(PROJECT_ID)},
                    )
                    if status != 200:
                        fail(f"blueprint submit returned {status}")
                    else:
                        ok("blueprint submitted for UI polling")
                        wait_text(page, "审核", "Review Blueprint", timeout=10000)
                        with page.expect_response(lambda r: "/work-graph/blueprint/" in r.url and r.url.endswith("/import"), timeout=15000):
                            click_text(page, "导入", "Import selected")
                        wait_text(page, "Smoke setup", timeout=10000)
        finally:
            browser.close()

    if FAILURES:
        print("\nFailures:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("Dashboard smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
