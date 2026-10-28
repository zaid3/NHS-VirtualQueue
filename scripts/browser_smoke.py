"""Synthetic browser journey; starts an isolated local API and writes screenshots."""
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet
from playwright.sync_api import expect, sync_playwright

from virtualqueue.app import create_app
from virtualqueue.cli import seed
from virtualqueue.config import Settings
from virtualqueue.db import Database
from virtualqueue.models import Base
from virtualqueue.security import Vault
from virtualqueue.worker import tick


def main():
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        settings = Settings(mode="test", database_url="sqlite:///" + folder + "/browser.db",
            public_url="http://127.0.0.1:8017", encryption_key=Fernet.generate_key().decode(),
            signing_key=secrets.token_urlsafe(48), gateway_url="", oidc_issuer="", google_routes_key="")
        db, vault = Database(settings.database_url), Vault(settings)
        Base.metadata.create_all(db.engine)
        seed(db, vault, settings)
        app = create_app(settings, db)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8017, access_log=False, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.getenv("VQ_BROWSER") or None, headless=True,
                                            args=["--no-sandbox"])
                patient_context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = patient_context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(settings.public_url)
                expect(page.get_by_role("heading", name="Know what happens next. Plan your arrival.")).to_be_visible()
                page.screenshot(path=str(output / "homepage-desktop.png"), full_page=True)
                page.goto(settings.public_url + "/patient")
                page.get_by_label("Care service", exact=True).select_option("riverside-urgent-care")
                page.get_by_label("Your name", exact=True).fill("Alex Example")
                page.get_by_label("Mobile number").fill("+447700900123")
                page.get_by_label("Family name as recorded by your care service").fill("Example")
                page.get_by_label("Date of birth").fill("1990-06-02")
                page.get_by_label("Home postcode").fill("E6 1AA")
                page.get_by_label("Reason for requesting a callback").fill("Synthetic browser test only")
                page.locator('input[name="consent"]').check()
                page.locator('input[name="understood"]').check()
                with page.expect_response("**/api/patient/start") as response:
                    page.get_by_role("button", name="Send verification code").click()
                code = response.value.json()["test_code"]
                page.get_by_label("Verification code").fill(code)
                page.get_by_role("button", name="Open my visit").click()
                expect(page.get_by_role("heading", name="Hello, Alex Example")).to_be_visible()
                staff_context = browser.new_context(viewport={"width": 1440, "height": 1000})
                staff = staff_context.new_page()
                staff.on("pageerror", lambda e: errors.append(str(e)))
                staff.goto(settings.public_url + "/staff")
                staff.get_by_role("button", name="Open local workspace").click()
                staff.get_by_role("button", name="Alex Example", exact=False).click()
                staff.get_by_label("Reason or action record").fill("Starting synthetic clinical assessment")
                staff.get_by_label("Next action").select_option("start_assessment")
                staff.get_by_role("button", name="Record action").click()
                staff.get_by_label("Clinical record reference").fill("SYNTHETIC-BROWSER")
                staff.get_by_label("Hospital arrival window").select_option(index=1)
                staff.get_by_label("Clinical assessment record").fill("Synthetic clinical assessment, not patient advice.")
                staff.get_by_label("Instructions for the patient, including worsening symptoms").fill("Follow your agreed plan. For a life-threatening emergency, call 999.")
                for box in staff.locator('#assessment input[type="checkbox"]').all():
                    box.check()
                staff.get_by_role("button", name="Save clinical decision").click()
                expect(staff.locator("#case")).to_contain_text("Booking pending")
                tick(db, vault, settings)
                staff.get_by_role("button", name="Refresh", exact=True).click()
                expect(staff.locator("#case")).to_contain_text("Remote waiting")
                staff.screenshot(path=str(output / "staff-desktop.png"), full_page=True)
                page.reload()
                expect(page.locator(".patient-window")).to_contain_text("Your agreed arrival window")
                page.screenshot(path=str(output / "patient-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(output / "patient-mobile.png"), full_page=True)
                page.get_by_role("button", name="My symptoms have worsened").click()
                expect(page.locator(".page-head")).to_contain_text("Reassessment required")
                assert page.locator(".patient-window").get_by_text("Your agreed arrival window", exact=True).count() == 0
                # Force the next polling request to fail; stale estimates must not remain visible.
                page.route("**/api/patient/visit", lambda route: route.abort())
                expect(page.locator("#offline")).to_be_visible(timeout=16000)
                assert not errors, errors
                browser.close()
                print("PASS: patient intake, OTP, clinician assessment, accepted booking, desktop/mobile layout, worsening, offline fallback; no page errors")
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            db.engine.dispose()


if __name__ == "__main__":
    main()
