"""Drive a real local QtWebEngine over HTTP using software rendering."""
import json, os, subprocess
from pathlib import Path


def check_qt(lab, screens):
    work=lab.pcm.parent
    runtime=work/"qt-run";runtime.mkdir(mode=0o700)
    here=Path(__file__).resolve().parent
    settings=work/"qt-lab.json"
    settings.write_text(json.dumps(dict(work=str(work),url=lab.base,pcm=str(lab.pcm),
                            screens=str(screens.resolve()) if screens else None,
                            script=str(here/"qt_flow.js"))))
    env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(work/'qt-config'),
             XDG_CACHE_HOME=str(work/'qt-cache'),XDG_DATA_HOME=str(work/'qt-data'),
             QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORM_PLUGIN_PATH='/usr/lib/x86_64-linux-gnu/qt5/plugins/platforms',
             QT_QUICK_BACKEND='software', QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',CAMILLA_TEST_QT=str(settings))
    out=subprocess.run(['/opt/slicer/5.10.0/Slicer','--no-main-window','--no-splash','--disable-modules',
                            '--python-script',str(here/"qt_page.py")],cwd=work,env=env,timeout=50,
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    result=work/'qt-result.json'
    if not result.exists():
        raise RuntimeError(f"QtWebEngine exited {out.returncode}: {out.stdout[-600:]}")
    data=json.loads(result.read_text())
    if not data["success"]:raise RuntimeError(f"QtWebEngine flow failed: {data['error']}; completed {[v['page'] for v in data['observations']]}")
    return data
