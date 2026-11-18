"""Validate the Camilla module at its HTTP boundary with actual stereo PCM.

Run from the source tree with python3 tools/lab/check_release.py --package file.zip.
Add --browser where a Chromium build compatible with Playwright is installed.
All processes, filenames, profiles and sample data are confined to this test.
"""
import argparse
from contextlib import contextmanager, ExitStack
import copy
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
import wave

RATE = 48000
CHUNK = 512
PREFIX = "/cgi-bin/PEQ"
# One complete 1 kHz period per channel at 48 kHz, same level on both channels.
LEVEL = 7000
PERIOD = b"".join(struct.pack("<hh", sample, sample) for n in range(48)
                  for sample in [round(LEVEL * math.sin(2 * math.pi * n / 48))])
PCM = PERIOD * 10
REFERENCE_RMS = math.sqrt(sum(v * v for v, in struct.iter_unpack("<h", PCM))
                          / (len(PCM) // 2))


def until(probe, limit=6, reason="postcondition"):
    deadline = time.monotonic() + limit
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for " + reason)
        time.sleep(0.035)


class FilePlayer:
    """Replace LMS/ALSA only. Engine queries, writes and PCM remain real."""
    def __init__(self):
        self.enabled = True

    def attach(self, app):
        self.app = app

    def source_snapshot(self):
        return {"mode": "play", "source": "file:///example.wav"}

    def status(self):
        return dict(enabled=self.enabled, active=self.enabled, processing=self.enabled,
                    mode="pcm-active" if self.enabled else "native",
                    recovery_required=False, message="PCM di laboratorio")

    def set_enabled(self, enabled):
        self.enabled = enabled
        if enabled:
            self.app.post("/api/apply", {"profile": self.app.current_profile()})
        return self.status()


@contextmanager
def http_server(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def proxy(bind, backend_port, forward):
    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if not path.startswith(PREFIX + "/"):
                self.send_error(404)
                return
            env = {"HTTP_HOST": self.headers["Host"], "SCRIPT_NAME": PREFIX,
                   "PATH_INFO": path[len(PREFIX):], "REQUEST_METHOD": self.command,
                   "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                   "HTTP_COOKIE": self.headers.get("Cookie", ""),
                   "HTTP_SEC_FETCH_SITE": self.headers.get("Sec-Fetch-Site", ""),
                   "HTTP_ORIGIN": self.headers.get("Origin", "")}
            size = int(self.headers.get("Content-Length", "0"))
            status, headers, body = forward(env, self.rfile.read(size), backend_port)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET
    return ThreadingHTTPServer((bind, 0), Gateway)


class Lab:
    def __init__(self, work, backend, cleanup):
        sys.path.insert(0, str(backend.resolve()))
        from bridge import forward
        from peq.engine import WebSocket
        from peq.model import compile_config, flat
        from peq.server import Application, make_server

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            engine_port = sock.getsockname()[1]
        state = work / "state"
        self.pcm = work / "output.raw"
        self.route = {"rate": RATE, "chunksize": CHUNK, "verified_pcm": True,
                      "capture": {"type": "Stdin", "channels": 2, "format": "S16_LE"},
                      "playback": {"type": "File", "filename": str(self.pcm),
                                   "channels": 2, "format": "S16_LE"}}
        template = compile_config(flat(), RATE, CHUNK, self.route["capture"], self.route["playback"])
        cfg_path = work / "engine.json"
        cfg_path.write_text(json.dumps(template))
        exe = work / "camilladsp"
        shutil.copyfile(backend / "bin/camilladsp", exe)
        exe.chmod(0o700)
        self.engine_version = subprocess.check_output([str(exe), "--version"], text=True).strip()
        with (work / "engine.log").open("wb") as log:
            child = subprocess.Popen([str(exe), "-a", "127.0.0.1", "-p", str(engine_port),
                                      str(cfg_path)], stdin=subprocess.PIPE, stdout=log, stderr=log)
        stop = threading.Event()
        def feed():
            while not stop.is_set():
                try:
                    child.stdin.write(PCM)
                    child.stdin.flush()
                except (OSError, ValueError):
                    return
                stop.wait(0.01)
        pump = threading.Thread(target=feed, daemon=True)
        pump.start()
        def finish_engine():
            stop.set()
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
            pump.join(timeout=2)
            child.stdin.close()
        cleanup.callback(finish_engine)
        self.app = Application(state, self.route, engine_port, audio=FilePlayer())
        self.ws_type = WebSocket
        self.engine_port = engine_port
        until(lambda: child.poll() is None and self.app.engine.status().get("state") == "Running",
              reason="CamillaDSP Running")
        port = cleanup.enter_context(http_server(make_server(self.app, "127.0.0.1", 0)))
        public_port = cleanup.enter_context(http_server(proxy("127.0.0.1", port, forward)))
        self.base = f"http://127.0.0.1:{public_port}{PREFIX}/"
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPCookieProcessor(CookieJar()))
        self.wav = work / "example.wav"
        with wave.open(str(self.wav), "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(RATE)
            f.writeframes(PCM * 300)
        self.input_without_html = self.call("api/session")
        assert self.input_without_html["authenticated"]

    def call(self, path, data=None):
        url = self.base + path
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with self.client.open(req, timeout=10) as f:
            body = f.read()
            return json.loads(body) if f.headers.get_content_type() == "application/json" else body

    def pcm_gain(self):
        frames = 48 * 32
        if not self.pcm.exists() or self.pcm.stat().st_size < frames * 4:
            return None
        with self.pcm.open("rb") as f:
            end = f.seek(0, os.SEEK_END)
            f.seek(end - end % 4 - frames * 4)
            data = f.read(frames * 4)
        pairs = list(struct.iter_unpack("<hh", data))
        return [20 * math.log10(max(1e-12, math.sqrt(sum(frame[ch] ** 2 for frame in pairs)
                                                      / frames) / REFERENCE_RMS)) for ch in (0, 1)]

    def expect_pcm(self, left, right):
        def matches():
            values = self.pcm_gain()
            if values and all(abs(a - b) < .045 for a, b in zip(values, (left, right))):
                return [round(v, 4) for v in values]
        return until(matches, reason=f"measured PCM gains {left}, {right} dB")

    def set_rew(self):
        old = self.call("api/profile/state")
        p = copy.deepcopy(old["profile"])
        p.update(id="sample-peq", name="Salotto", enabled=True)
        for ch, gain in (("L", -6), ("R", -2)):
            t = rew_txt(gain)
            preview = self.call("api/rew/preview", {"text": t, "channel": ch, "rate": RATE})
            assert preview["can_apply"] and not preview["issues"]
            p["peq"][ch] = preview["bands"]
        return p, old


def rew_txt(gain):
    return f"Filter Settings file\nFilter 1: ON PK Fc 1000 Hz Gain {gain} dB Q 1.00\n"


def native(lab, comparison=True):
    status = lab.call("api/status")
    assert status["version"] == "4.1.3" and status["state"] == "Running"
    root = lab.call("")
    assert b'lang="it"' in root
    for url in ("app.js", "eq-graph.js", "style.css"):
        assert len(lab.call(url)) > 100
    initial_measured = lab.expect_pcm(0., 0.)
    p, old = lab.set_rew()
    before = lab.call("api/presets")
    status = lab.call("api/apply", {"profile": p, "expected": old["profile_revision"]})
    assert status["applied"]
    corrected_measured = lab.expect_pcm(-6., -2.)
    updated = lab.call("api/profile/state")
    assert updated["profile"] == p
    catalog = lab.call("api/presets/save", {"revision": before["revision"], "profile": p})
    assert any(item["id"] == p["id"] for item in catalog["presets"])
    saved_revision = updated["profile_revision"]
    report = {"module_version": lab.call("api/status")["module_version"],
              "engine": lab.engine_version, "flat_pcm_db": initial_measured,
              "peq_pcm_db": corrected_measured, "channels_checked": "L, R at 1000 Hz"}
    if comparison:
        session = uuid.uuid4().hex
        cmd = {"session": session, "expected": saved_revision}
        def listen(a):
            return lab.call("api/apply", {**cmd, "comparison": "listen", "profile": a, "rate": RATE})
        flat = copy.deepcopy(catalog["presets"][0])
        flat.update(enabled=True, bypass=False)
        listen(flat)
        report["temporary_flat_db"] = lab.expect_pcm(0., 0.)
        assert lab.call("api/profile/state") == updated
        assert lab.call("api/presets") == catalog
        listen(p)
        report["temporary_correction_db"] = lab.expect_pcm(-6., -2.)
        listen(flat)
        lab.expect_pcm(0, 0)
        lab.call("api/apply", {**cmd, "comparison": "end"})
        report["restored_db"] = lab.expect_pcm(-6., -2.)
        assert lab.call("api/profile/state") == updated and lab.call("api/presets") == catalog
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, help="Validate the supplied, exact ZIP; default to src.")
    parser.add_argument("--baseline", action="store_true", help="Only features supported by v0.4.47.")
    flow = parser.add_mutually_exclusive_group()
    flow.add_argument("--dom-library", type=Path, help="Run DOM events using the supplied LinkeDOM worker ES module.")
    flow.add_argument("--qt", action="store_true", help="Test both viewports using the installed QtWebEngine browser.")
    flow.add_argument("--browser", action="store_true", help="Also run Playwright with Chromium.")
    parser.add_argument("--screens", type=Path, help="Optional local directory for UI captures.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="daphile-release-") as tmp, ExitStack() as cleanup:
        work = Path(tmp)
        if args.package:
            with zipfile.ZipFile(args.package) as z:
                assert z.testzip() is None
                z.extractall(work / "release")
            backend = work / "release" / "DaphileCamillaDSP/backend"
        else:
            backend = Path(__file__).resolve().parents[2] / "DaphileCamillaDSP/backend"
        # Keep all engine locks and Python files confined to the lab.
        sys.dont_write_bytecode = True
        tempfile.tempdir = str(work)
        lab = Lab(work, backend, cleanup)
        result = native(lab, not args.baseline)
        if args.dom_library:
            dom = Path(__file__).with_name("check_dom.mjs")
            output = subprocess.check_output(["node", str(dom), lab.base, str(lab.pcm), str(lab.wav), str(args.dom_library.resolve())], text=True, timeout=90)
            result["ui_dom"] = json.loads(output)
        if args.qt:
            from check_qt import check_qt
            result["qt_browser"]=check_qt(lab,args.screens)
        if args.browser:
            from check_ui import check_browser
            result["browser"] = check_browser(lab, args.screens)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
