"""Optional Playwright driver for environments with a local Chromium binary."""
import os, shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def check_browser(lab, screens=None):
    if screens: screens.mkdir(parents=True,exist_ok=True)
    script=Path(__file__).with_name("qt_flow.js").read_text()
    with sync_playwright() as pw:
        candidate=os.environ.get("CAMILLA_TEST_CHROMIUM") or shutil.which("chromium")
        opts={"executable_path":candidate} if candidate else {}
        browser=pw.chromium.launch(**opts)
        try:
            page=browser.new_page(viewport={"width":1280,"height":860})
            page.goto(lab.base)
            page.evaluate("() => { void "+script+" }")
            result=[]
            seen=[]
            while True:
                handle=page.wait_for_function("""seen=>{
                    const v=window.qaState;
                    return v && (v.kind==='error' || v.kind==='reload' ||
                                v.kind==='observation' && !seen.includes(v.label)) && v
                }""",arg=seen,timeout=13000)
                state=handle.json_value()
                if state["kind"]=="error": raise AssertionError(state["error"])
                if state["kind"]=="reload": break
                label=state["label"]
                seen.append(label)
                gains=lab.expect_pcm(*state["rms"]) if state.get("rms") else None
                result.append(dict(state["metrics"],page=label,gain_db=gains))
                if screens: page.screenshot(path=str(screens/(label+".png")))
                if label=="01-ascolto-desktop": page.set_viewport_size({"width":390,"height":844})
                elif label=="03-taratura-mobile": page.set_viewport_size({"width":1280,"height":860})
                page.evaluate("() => window.qaResume()")
            page.reload()
            page.wait_for_function("""()=>document.querySelector('#listenPresets')?.value==='sample-peq'
                                      && !document.querySelector('#listenPresets').disabled
                                      && document.querySelector('#tuneView').hidden""")
            saved=lab.call("api/profile/state")["profile"]["peq"]
            assert [saved[k][0]["gain"] for k in ("L","R")] == [-3,-9]
            return {"engine":browser.version,"observations":result,"reopened":True}
        finally:
            browser.close()
