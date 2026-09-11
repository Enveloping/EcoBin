"""Headless portal regression using synthetic HTTP facts; never talks to hardware.

Run with a local static server and an optional Python Playwright environment:
  uv run --no-project --python 3.11 --with playwright python \
    hardware/tools/test_factory_measurements_ui.py --url http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--full-chromium", action="store_true", help="Use installed full Chromium in headless mode")
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Synthetic test must use a loopback server")
    web = Path(__file__).resolve().parents[1] / "factory" / "web"
    factory = {
        "status": "RUNNING", "phase": "MCU_CHECK_PASSED", "revision": 2,
        "executorAvailable": True, "allowedActions": ["CAPTURE_EMPTY_WEIGHT"],
        "mcuIdentity": {"firmwareVersion": "fixture-1.0.0"}, "mcuUpdateLineInstalled": False,
        "checks": {
            "mcu": {"status": "PASSED", "resultCode": "MCU_REVISION_2_AND_F1_HEALTHY", "selfTestWeightGrams": 2000, "selfTestInfraredBlocked": False, "selfTestSmokeCode": 0},
            "weight": {"status": "NOT_RUN", "resultCode": "NOT_RUN"},
            "cameras": {"status": "PASSED", "resultCode": "DUAL_CAMERA_FIXED_ROLES_PASSED", "outsideCaptureNonEmpty": True, "insideCaptureNonEmpty": True, "outsideRoleConfirmed": True, "insideRoleConfirmed": True},
            "upgradeLine": {"status": "NOT_APPLICABLE", "resultCode": "MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED", "prepareSendAttempts": 0, "romWritePerformed": False, "romDeviceId": None},
            "delivery": {"status": "PASSED", "resultCode": "DELIVERY_SAFE_VERIFIED", "sendAttempts": 1, "operatorAreaSafeConfirmed": True, "result": {"preWeightGrams": 0, "postWeightGrams": 400, "weightDeltaGrams": 400, "infraredBlocked": False}},
            "clean": {"status": "NOT_RUN", "resultCode": "NOT_RUN"},
        },
    }
    status = {
        "stage": "FACTORY_TEST_RUNNING", "image": {"releaseId": "UI-FIXTURE-NOT-A-DEVICE"},
        "system": {"timeTrusted": False}, "factoryTest": factory,
        "factorySeal": {"statusCode": "CLOUD_ACCEPTANCE_REQUIRED"},
        "network": {"cellular": {"resultCode": "NONE"}},
        "factoryFlow": {
            "currentNode": "LOCAL_HARDWARE_ACCEPTANCE", "overallState": "WAITING_OPERATOR",
            "nodes": [{"id": "LOCAL_HARDWARE_ACCEPTANCE", "state": "WAITING_OPERATOR", "detailCode": "NOT_RUN", "errorCode": "NONE", "steps": []}],
        },
    }
    requests = []
    errors = []

    def trace(value: int) -> dict:
        return {"samplesGrams": [value] * 3, "readCount": 3, "resultCode": "STABLE_WEIGHT_CAPTURED"}

    def route_request(route) -> None:
        path = urlsplit(route.request.url).path
        if not route.request.url.startswith(args.url + "/"):
            errors.append("Unexpected non-local request")
            route.abort()
            return
        if path.startswith("/assets/") and Path(path).name in {"app.js", "app.css"}:
            route.fulfill(path=str(web / Path(path).name))
        elif path == "/api/v1/status":
            route.fulfill(json=status)
        elif path == "/favicon.ico":
            route.fulfill(status=204)
        elif path == "/api/v1/acceptance/action":
            request = route.request.post_data_json
            requests.append(request)
            assert request["expectedRevision"] == factory["revision"]
            operation = request["operation"]
            params = request["parameters"]
            if operation == "CAPTURE_EMPTY_WEIGHT":
                assert params["confirmScaleEmpty"] is True
                factory["checks"]["weight"] = {
                    "status": "RUNNING", "resultCode": "WAITING_FOR_REFERENCE_LOAD",
                    "emptyWeightGrams": 2000, "targetDeltaGrams": params["referenceWeightGrams"],
                    "toleranceGrams": 10, "stableSampleCount": 3, "stableMaxSpreadGrams": 2,
                    "sampleIntervalMs": 100, "sampleTimeoutMs": 3000, "sampling": {"empty": trace(2000)},
                }
                factory["allowedActions"] = ["CAPTURE_LOADED_WEIGHT"]
            elif operation == "CAPTURE_LOADED_WEIGHT":
                assert params == {"confirmReferencePlaced": True}
                weight = factory["checks"]["weight"]
                delta = 320 if weight["targetDeltaGrams"] == 400 else weight["targetDeltaGrams"]
                weight.update(loadedWeightGrams=2000 + delta, deltaGrams=delta)
                weight["sampling"]["loaded"] = trace(2000 + delta)
                failed = delta != weight["targetDeltaGrams"]
                weight.update(status="FAILED" if failed else "RUNNING", resultCode="WEIGHT_DELTA_OUT_OF_RANGE" if failed else "WAITING_FOR_WEIGHT_REMOVAL")
                factory["allowedActions"] = ["CAPTURE_EMPTY_WEIGHT" if failed else "CONFIRM_WEIGHT_REMOVED"]
                if failed:
                    factory["revision"] += 1
                    route.fulfill(status=422, json={"error": "WEIGHT_DELTA_OUT_OF_RANGE"})
                    return
            elif operation == "CONFIRM_WEIGHT_REMOVED":
                assert params == {"confirmReferenceRemoved": True}
                weight = factory["checks"]["weight"]
                weight.update(status="PASSED", resultCode="WEIGHT_REFERENCE_WITHIN_TOLERANCE_AND_REMOVED", removedWeightGrams=2000)
                weight["sampling"]["removed"] = trace(2000)
                factory["allowedActions"] = ["CAPTURE_CAMERAS"]
            else:
                raise AssertionError("This browser test must not request physical action commands")
            factory["revision"] += 1
            route.fulfill(json={"idempotent": False, "revision": factory["revision"]})
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium" if args.full_chromium else None)
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("dialog", lambda dialog: dialog.accept())
        page.route("**/*", route_request)
        page.goto(args.url + "/")
        page.wait_for_load_state("networkidle")
        # Reconnaissance before interactions: verify rendered labels and facts.
        expect(page.get_by_role("heading", name="每一步测到了什么")).to_be_visible()
        expect(page.locator("[data-stage=loaded]")).to_contain_text("尚未采集")
        expect(page.locator("[data-check=delivery]")).to_contain_text("0 g")
        expect(page.locator("[data-check=mcu]")).to_contain_text("无遮挡")
        field = page.get_by_label("测试物的已知参考重量（克 / g）")
        button = page.locator("#primary-action")
        for invalid in ["", "0", "-1", "10", "400.5", "350001"]:
            field.fill(invalid)
            button.click()
            expect(page.locator("#action-result")).to_contain_text("整数克数")
        assert not requests
        field.fill("400")
        page.locator("#refresh").click()
        expect(field).to_have_value("400")
        button.click()
        expect(button).to_have_text("采集 400 克测试物重量")
        expect(field).to_be_disabled()
        page.reload(wait_until="networkidle")
        expect(field).to_have_value("400")
        expect(field).to_be_disabled()
        button.click()
        expect(page.locator("#weight-measurements-title")).to_have_text("称重 · 未通过")
        expect(page.locator("#weight-summary")).to_contain_text("390～410 g")
        expect(page.locator("#weight-summary")).to_contain_text("-80 g")
        expect(page.locator("[data-stage=loaded]")).to_contain_text("2320 / 2320 / 2320 g")
        expect(field).to_be_enabled()
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshots / "weight-failed-mobile.png"), full_page=True)
        field.fill("1000")
        button.click()
        expect(button).to_have_text("采集 1000 克测试物重量")
        button.click()
        expect(button).to_have_text("确认测试物已取下")
        expect(field).to_be_disabled()
        button.click()
        expect(page.locator("#weight-measurements-title")).to_have_text("称重 · 已通过")
        expect(page.locator("#weight-summary")).to_contain_text("990～1010 g")
        expect(page.locator("[data-stage=removed]")).to_contain_text("2000 g")
        assert len(requests) == 5
        factory["checks"]["weight"] = {
            "status": "FAILED", "resultCode": "WEIGHT_READING_NOT_STABLE", "targetDeltaGrams": 1000,
            "sampling": {"empty": {"samplesGrams": [1000, 1010], "readCount": 2, "resultCode": "WEIGHT_READING_NOT_STABLE"}},
        }
        factory["allowedActions"] = ["CAPTURE_EMPTY_WEIGHT"]
        factory["mcuIdentity"]["firmwareVersion"] = "<img src=x onerror=alert(1)>"
        page.locator("#refresh").click()
        expect(page.locator("[data-stage=empty]")).to_contain_text("1000 / 1010 g")
        expect(page.locator("[data-stage=empty]")).to_contain_text("未取得稳定重量")
        expect(page.locator("[data-check=mcu] img")).to_have_count(0)
        for width in [320, 390, 1366]:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if args.screenshots:
            page.screenshot(path=str(args.screenshots / "weight-unstable-desktop.png"), full_page=True)
        # A synthetic 422 is deliberately exercised above; other console errors fail.
        assert all("422" in error for error in errors), errors
        browser.close()
    print(json.dumps({"result": "PASS", "syntheticOnly": True, "actionRequests": len(requests), "viewportWidths": [320, 390, 1366]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
