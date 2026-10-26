import os
import sys
import urllib.error
import urllib.request

from playwright.sync_api import Error, sync_playwright


BASE_URL = os.environ.get("ACD_BASE_URL", "http://127.0.0.1:9889")
CHROME = os.environ.get(
    "PLAYWRIGHT_EXECUTABLE_PATH",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
FAILURES: list[str] = []


def ok(message: str) -> None:
    print(f"OK {message}")


def fail(message: str) -> None:
    print(f"FAIL {message}")
    FAILURES.append(message)


def server_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/security/v1alpha1/overview", timeout=10) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def wait_heading(page, pattern: str, route: str) -> None:
    try:
        page.get_by_role("heading", name=pattern).wait_for(timeout=8000)
        ok(f"{route} renders")
    except Error as exc:
        fail(f"{route} did not render {pattern}: {exc}")


def main() -> int:
    if not server_ready():
        fail("security overview API is unavailable")
        return 1
    ok("security overview API returns 200")

    with sync_playwright() as playwright:
        launch_kwargs = {"headless": True}
        if os.path.exists(CHROME):
            launch_kwargs["executable_path"] = CHROME
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.on("pageerror", lambda error: fail(f"page error: {error}"))
        page.on("console", lambda message: fail(f"console error: {message.text}") if message.type == "error" else None)
        page.on("response", lambda response: fail(f"HTTP {response.status} {response.url}") if response.status >= 500 else None)

        try:
            pages = [
                ("/admin/#/security", "安全总览"),
                ("/admin/#/security/audit", "安全审计"),
                ("/admin/#/security/mcp", "MCP 网关"),
                ("/admin/#/security/sandboxes", "Agent 沙箱"),
            ]
            for route, heading in pages:
                page.goto(f"{BASE_URL}{route}", wait_until="networkidle", timeout=30000)
                wait_heading(page, heading, route)

            page.goto(f"{BASE_URL}/admin/#/work-graph", wait_until="networkidle", timeout=30000)
            page.wait_for_url("**/admin/#/security")
            if page.url.endswith("/#/security"):
                ok("legacy orchestration route redirects")
            else:
                fail(f"legacy route did not redirect: {page.url}")
        finally:
            browser.close()

    if FAILURES:
        return 1
    print("Dashboard smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
