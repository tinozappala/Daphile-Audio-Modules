"""Executed inside the installed Slicer QtWebEngine test client."""
import json
import math
import os
import struct
import time
from pathlib import Path

params = json.loads(Path(os.environ["CAMILLA_TEST_QT"]).read_text())
work = Path(params["work"])
screens = Path(params["screens"]) if params.get("screens") else None
if screens:
    screens.mkdir(parents=True, exist_ok=True)
w = slicer.qSlicerWebWidget()
w.resize(1280, 860)
seen, result, reloading = set(), [], False
waiting_for_paint = {}
deadline = time.monotonic() + 38
probe = "JSON.stringify(window.qaState || {kind:'waiting'})"


def finish(error=None):
    data = {"success": not error, "error": error, "observations": result}
    (work / "qt-result.json").write_text(json.dumps(data, ensure_ascii=False))
    slicer.util.exit()


def gains():
    frames = 1536
    with open(params["pcm"],"rb") as f:
        end = f.seek(0,2)
        f.seek(end - end%4 - frames*4)
        samples = list(struct.iter_unpack("<hh", f.read(frames*4)))
    return [20*math.log10(max(1e-12,math.sqrt(sum(s[i]**2 for s in samples)/frames)/(7000/math.sqrt(2))))
            for i in (0,1)]


def observe(state):
    if time.monotonic()>deadline:
        return finish("Timeout at "+state.get("label",state["kind"]))
    if state["kind"]=="error":
        return finish(state["error"])
    if state["kind"]=="reload":
        global reloading
        if not reloading:
            reloading=True
            w.evalJS("location.reload()")
        return
    if state["kind"]=="success":
        return finish()
    if state["kind"]=="observation" and state["label"] not in seen:
        first = waiting_for_paint.setdefault(state["label"], time.monotonic())
        if time.monotonic() - first < 0.3:
            return later()
        actual = gains() if state["rms"] else None
        if actual and any(abs(a-b)>.065 for a,b in zip(actual,state["rms"])):
            return later()
        label=state["label"]
        seen.add(label)
        metric=dict(state["metrics"], page=label, gain_db=[round(v,4) for v in actual] if actual else None)
        result.append(metric)
        if screens and label.startswith(("01-","02-","03-","04-","07-","08-")):
            if not w.grab().save(str(screens/(label+".png"))):
                return finish("Capture failed")
        if label=="01-ascolto-desktop":
            w.resize(390,844)
        elif label=="03-taratura-mobile":
            w.resize(1280,860)
        qt.QTimer.singleShot(180,lambda:w.evalJS("window.qaResume()"))
    later()


def later():
    qt.QTimer.singleShot(60,lambda:w.evalJS(probe))


def evaluated(js, answer):
    if js==probe:
        try: observe(json.loads(answer))
        except Exception as exc: finish(str(exc))


def loaded(ok):
    if not ok:
        return finish("Cannot load local module")
    if reloading:
        w.evalJS("""(async()=>{try{
          const wait=()=>new Promise(resolve=>setTimeout(resolve,40));
          for(let n=0;n<125;n++){
            const ready=document.getElementById('listenPresets');
            if(ready.value==='sample-peq' && !ready.disabled && document.getElementById('tuneView').hidden){
              const res=await fetch('./api/profile/state'); const p=(await res.json()).profile;
              if(p.peq.L[0].gain!==-3 || p.peq.R[0].gain!==-9) throw Error('Profilo non ripristinato');
              window.qaState={kind:'success'}; return;
            }
            await wait();
          } throw Error('Riapertura non verificata');
        }catch(e){window.qaState={kind:'error',error:String(e)}}})()""")
    else:
        w.evalJS(Path(params["script"]).read_text())
    later()


w.connect("loadFinished(bool)",loaded)
w.connect("evalResult(QString,QString)",evaluated)
w.show()
w.setUrl(params["url"])
