"""Drive the UI in headless Chromium against a running dev server; saves screenshots."""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = Path("/home/claude/url.txt").read_text().strip()
BASE, TOKEN = URL.split("/?t=")
SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/claude/shots")
SHOTS.mkdir(exist_ok=True)
errors = []


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-Token": TOKEN, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: m.type in ("error", "warning") and errors.append(f"console {m.type}: {m.text}"))
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto(URL)
    pg.wait_for_timeout(600)
    pg.screenshot(path=SHOTS / "01_welcome.png")

    # import two rolls through the API (native picker isn't available headless)
    r1 = api("POST", "/api/import", {"path": "/home/claude/samples/roll16"})
    r2 = api("POST", "/api/import", {"path": "/home/claude/samples/rolljpg"})
    pg.reload()
    for _ in range(80):
        if not [j for j in api("GET", "/api/jobs") if j["running"]]:
            break
        time.sleep(0.25)
    pg.reload(); pg.wait_for_timeout(800)
    pg.screenshot(path=SHOTS / "02_contact.png")
    assert pg.locator(".roll").count() == 2
    # rename roll 2 in the sidebar
    pg.locator(".roll").nth(1).locator(".r-name").click()     # focusing a field selects the roll
    pg.wait_for_timeout(300)
    pg.keyboard.press("Control+a"); pg.keyboard.type("Pentax17 #002"); pg.keyboard.press("Enter")
    pg.locator(".roll.active .r-film").click(); pg.keyboard.type("Cinestill 800T"); pg.keyboard.press("Tab")
    pg.wait_for_timeout(500)
    st = api("GET", "/api/state")
    assert st["rolls"][1]["name"] == "Pentax17 #002" and st["rolls"][1]["film"] == "Cinestill 800T", st["rolls"][1]["name"]

    # selection + batch actions on contact sheet
    cards = pg.locator("#grid .fc")
    assert cards.count() == 9
    cards.nth(0).click(); cards.nth(3).click(modifiers=["Shift"])
    assert pg.locator("#grid .fc.sel").count() == 4
    pg.screenshot(path=SHOTS / "03_selection.png")
    pg.get_by_role("button", name="SPLIT", exact=True).click()
    pg.wait_for_timeout(400)
    assert pg.locator("#grid .chip.st-split").count() == 4
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(400)
    assert pg.locator("#grid .chip.st-split").count() == 0, "undo failed"
    pg.keyboard.press("Control+Shift+z"); pg.wait_for_timeout(400)
    assert pg.locator("#grid .chip.st-split").count() == 4, "redo failed"

    # crop tab via double-click
    cards.nth(4).dblclick()
    pg.wait_for_timeout(900)
    pg.screenshot(path=SHOTS / "04_crop.png")
    assert "FRAME 05" in pg.locator(".bar").inner_text()
    cut0 = pg.locator("#cutD").inner_text()
    pg.keyboard.press("."); pg.keyboard.press("Shift+."); pg.wait_for_timeout(200)
    cut1 = pg.locator("#cutD").inner_text()
    assert cut0 != cut1, (cut0, cut1)
    # drag the cut line
    box = pg.locator("#cv").bounding_box()
    st = api("GET", "/api/state")["rolls"][1]
    f = st["frames"][4]
    # find line x: draw rect inside stage with 14px padding
    cw, ch = box["width"], box["height"]
    ia = f["w"] / f["h"]; P = 14
    dW = cw - 2 * P if ia > (cw - 2 * P) / (ch - 2 * P) else (ch - 2 * P) * ia
    dX = (cw - dW) / 2
    pct = float(cut1.rstrip("%"))
    lx = box["x"] + dX + pct / 100 * dW
    ly = box["y"] + ch / 2
    pg.mouse.move(lx, ly); pg.mouse.down(); pg.mouse.move(lx - 60, ly, steps=5); pg.mouse.up()
    pg.wait_for_timeout(500)
    cut2 = float(pg.locator("#cutD").inner_text().rstrip("%"))
    assert cut2 < pct - 2, (pct, cut2)
    # clicking away from the line must NOT move it
    pg.mouse.click(box["x"] + dX + 20, ly)
    pg.wait_for_timeout(200)
    assert float(pg.locator("#cutD").inner_text().rstrip("%")) == cut2
    pg.keyboard.press("a"); pg.wait_for_timeout(800)          # auto-detect back
    cut3 = float(pg.locator("#cutD").inner_text().rstrip("%"))
    assert abs(cut3 - 51.0) < 0.6, cut3
    pg.screenshot(path=SHOTS / "05_crop_auto.png")
    pg.keyboard.press("Enter"); pg.wait_for_timeout(700)
    assert "FRAME 06" in pg.locator(".bar").inner_text()
    pg.keyboard.press("f"); pg.wait_for_timeout(700)
    pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(700)
    pg.screenshot(path=SHOTS / "06_crop_full.png")
    pg.keyboard.press("r"); pg.wait_for_timeout(900)
    pg.screenshot(path=SHOTS / "07_rotated.png")
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(600)
    # border edit
    bi = pg.locator("#borderIn"); bi.fill("60"); bi.press("Enter"); pg.wait_for_timeout(500)
    assert api("GET", "/api/state")["rolls"][1]["border"] == 60
    pg.screenshot(path=SHOTS / "08_border.png")
    # batch split dialog
    pg.get_by_role("button", name="SPLIT ALL…").click(); pg.wait_for_timeout(300)
    pg.screenshot(path=SHOTS / "09_batch_modal.png")
    pg.get_by_role("button", name="KEEP DETECTED CUTS").click(); pg.wait_for_timeout(500)
    # export tab
    pg.keyboard.press("3"); pg.wait_for_timeout(900)
    pg.screenshot(path=SHOTS / "10_export.png")
    rows = pg.locator(".exp-row")
    assert rows.count() == 9, rows.count()
    rows.nth(2).locator("input").click(); pg.wait_for_timeout(300)
    assert pg.locator(".exp-row.off").count() == 1
    pg.locator("[data-naming=source]").click(); pg.wait_for_timeout(300)
    assert "_L.tif" in pg.locator(".exp-name").first.inner_text()
    # set export dir via settings and export
    api("PATCH", "/api/settings", {"exportDir": "/home/claude/export_test"})
    pg.reload(); pg.wait_for_timeout(500); pg.keyboard.press("3"); pg.wait_for_timeout(600)
    pg.locator(".roll").nth(1).locator(".r-stats").click(); pg.wait_for_timeout(600)
    pg.locator("[data-act=export]").click()
    for _ in range(80):
        if pg.locator(".toast", has_text="Exported").count():
            break
        pg.wait_for_timeout(250)
    pg.screenshot(path=SHOTS / "11_exported.png")
    n_files = len(list(Path("/home/claude/export_test").glob("*")))
    # export again → conflict dialog
    pg.locator("[data-act=export]").click(); pg.wait_for_timeout(600)
    pg.screenshot(path=SHOTS / "12_conflict.png")
    assert pg.locator(".modal", has_text="already exist").count() == 1
    pg.get_by_role("button", name="SKIP EXISTING").click(); pg.wait_for_timeout(1500)
    # 16-bit roll in contact view + missing file handling
    pg.locator(".roll").nth(0).locator(".r-stats").click(); pg.wait_for_timeout(700)
    pg.keyboard.press("1"); pg.wait_for_timeout(700)
    pg.screenshot(path=SHOTS / "13_roll16.png")
    pg.keyboard.press("?"); pg.wait_for_timeout(300)
    pg.screenshot(path=SHOTS / "14_shortcuts.png")
    pg.keyboard.press("Escape")
    pg.locator("[data-act=menu]").click(); pg.wait_for_timeout(200)
    pg.screenshot(path=SHOTS / "15_menu.png")
    pg.keyboard.press("Escape")
    pg.set_viewport_size({"width": 1000, "height": 640})
    pg.keyboard.press("2"); pg.wait_for_timeout(900)
    pg.screenshot(path=SHOTS / "16_small_crop.png")
    pg.keyboard.press("3"); pg.wait_for_timeout(600)
    pg.screenshot(path=SHOTS / "17_small_export.png")
    b.close()

print("exported files:", n_files)
print("ERRORS:" if errors else "no console errors", *errors, sep="\n")
