"""Playwright Chromium launcher for local and cloud environments."""
import shutil

def launch_chromium(playwright):
    executable = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if executable:
        return playwright.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    return playwright.chromium.launch(headless=True)
