"""Observe an RC7 Live boot from a temporary DATA disk, without starting audio."""

import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

HTTP = "http://127.0.0.1"
ROOT = Path("/usr/local/share/daphile-release")
HOST = Path("/mnt/hdd/system/daphile-audio-host")
MODULE = HOST / "modules/DaphileCamillaDSP"
OUTPUT = Path("/var/www/localhost/htdocs/__rc7_qa__.json")
CASES = Path("/etc/local.d/rc7_expected.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def json_file(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def request(path, data=None, cookie=None, base=HTTP):
    headers = {"Origin": base}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    if cookie:
        headers["Cookie"] = cookie
    with OPENER.open(urllib.request.Request(base + path, data=data, headers=headers), timeout=10) as reply:
        return reply.read(4 * 1024 * 1024), reply.headers


def write_report(report):
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.chmod(0o644)
    os.replace(temporary, OUTPUT)


def loop_root():
    boot = Path("/boot/boot/fw2610191845/rootfs")
    mounts = Path("/proc/self/mounts").read_text().splitlines()
    current = next(line.split() for line in mounts if line.split()[1] == "/mnt/root-ro")
    if current[2] != "squashfs":
        raise RuntimeError("Read-only root is not squashfs")
    backing = Path("/sys/block") / Path(current[0]).name / "loop/backing_file"
    if backing.read_text().strip() != str(boot):
        raise RuntimeError("Live root is not the CD image")
    return digest(boot)


def check_files(expected):
    auto = json_file(Path("/opt/daphile-autotune/CANDIDATE.json"))
    release = json_file(ROOT / "release.json")
    if auto["version"] != "B4f" or release["Camilla_module"] != "0.4.49":
        raise RuntimeError("Unexpected installed version markers")
    rows = expected["autotune_source"] + [expected["javascript"]]
    for row in rows:
        path = Path("/") / row["path"]
        if digest(path) != row["sha256"] or stat.S_IMODE(path.stat().st_mode) != row["mode"]:
            raise RuntimeError("AutoTune file hash or mode differs: " + row["path"])
    return {"AutoTune": auto["version"], "Camilla": release["Camilla_module"], "AutoTune_files_verified": len(rows)}


def check_lms():
    body, _ = request("/jsonrpc.js", {"id": 1, "method": "slim.request", "params": ["", ["version", "?"]]},
                      base="http://127.0.0.1:9000")
    version = json.loads(body)["result"]["_version"]
    if version != "9.1.1":
        raise RuntimeError("Unexpected LMS version")
    return version


def check_autotune(expected):
    html, _ = request("/cgi-bin/AutoTune")
    if ("/js/dat-core.js?v=" + expected["javascript"]["sha256"][:15]).encode() not in html:
        raise RuntimeError("Unexpected AutoTune panel version")
    js, _ = request("/js/dat-core.js")
    if hashlib.sha256(js).hexdigest() != expected["javascript"]["sha256"]:
        raise RuntimeError("Unexpected served AutoTune JS")
    body, _ = request("/cgi-bin/AutoTune", {"action": "status"})
    value = json.loads(body)
    if value.get("enabled") is not True or any(job.get("running") for job in value.get("jobs", [])):
        raise RuntimeError("AutoTune broker not available and idle")
    return {"panel_http": 200, "javascript_sha256": expected["javascript"]["sha256"], "broker_idle": True}


def check_camilla_factory(expected):
    entry = expected["camilla"]
    approval = json_file(HOST / "approvals.json")["DaphileCamillaDSP"]
    if entry not in approval or digest(HOST / "bundles" / entry["archive"]) != entry["sha256"]:
        raise RuntimeError("Native Camilla approval or archive differs")
    plugins = Path(json_file(HOST / "config.json")["plugins"])
    source = plugins / "DaphileCamillaDSP"
    for name, sha in entry["files"].items():
        if digest(source / name) != sha:
            raise RuntimeError("LMS Camilla file differs: " + name)
    value, _ = request("/cgi-bin/AudioPlugins/extensions.xml")
    catalog = ET.fromstring(value).find("./plugins/plugin[@name='DaphileCamillaDSP']")
    if catalog is None or catalog.get("version") != entry["version"] or catalog.findtext("sha") != entry["sha1"]:
        raise RuntimeError("Unexpected Camilla catalog entry")
    return {"version": entry["version"], "release": entry["sha256"], "LMS_files_verified": len(entry["files"])}


def check_camilla_api(expected):
    entry = expected["camilla"]
    current = json_file(MODULE / "current.json")
    process = json_file(MODULE / "process.json")
    ready = json_file(MODULE / "data/ready.json")
    state = Path("/proc") / str(process["pid"]) / "stat"
    fields = state.read_text().rsplit(")", 1)[1].split()
    if (current["release"], current["status"]) != (entry["sha256"], "running") or (
            ready["process"] != process or fields[0] in ("Z", "X") or fields[19] != process["start"]):
        raise RuntimeError("Camilla process not ready")
    cgi = "/cgi-bin/CamillaPlugin"
    _, headers = request(cgi + "/api/session")
    status, _ = request(cgi + "/api/status", cookie=headers["Set-Cookie"].split(";", 1)[0])
    value = json.loads(status)
    if value.get("module_version") != entry["version"]:
        raise RuntimeError("Wrong Camilla API version")
    return {"session_http": 200, "status_http": 200, "version": value["module_version"],
            "audio_mode": value.get("audio", {}).get("mode")}


def failure_details():
    details = {}
    for name in ("daphile-modules", "daphile-audio-host", "daphile-autotune-gui", "mediaserver"):
        result = subprocess.run(["/sbin/rc-service", name, "status"], capture_output=True, timeout=8)
        details[name] = {"exit": result.returncode, "status": result.stdout.decode(errors="replace").strip()[-400:]}
    for name, base in {"host": HOST, "Camilla": MODULE, "RME": HOST / "modules/DaphileRME"}.items():
        module = {}
        for file in ("status.json", "current.json", "menu-error.json"):
            try:
                value = json_file(base / file)
                module[file] = {key: value[key] for key in ("version", "status", "error", "errors") if key in value}
            except (OSError, ValueError):
                pass
        details[name] = module
    if (HOST/"config.json").is_file():
        import yaml
        config=json_file(HOST/"config.json")
        preferences=yaml.safe_load(Path(config["prefs"]).read_text())
        details["native_enabled"]={name:preferences.get(name) for name in ("DaphileCamillaDSP","DaphileRME")}
        details["host_socket_present"]=(HOST/"control.sock").is_socket()
    return details


def main():
    began = time.monotonic()
    expected = json_file(CASES)
    report = {"scope": "Fresh ext4 DATA with observer only. ISO is read-only.", "efi": Path("/sys/firmware/efi").is_dir(),
              "kernel": platform.release(), "python": platform.python_version(), "checks": {}}
    write_report(dict(report, status="OBSERVER_STARTED"))
    checks = report["checks"]
    errors = {}
    probes = {"versions_and_files": lambda: check_files(expected), "LMS": check_lms,
              "AutoTune_HTTP": lambda: check_autotune(expected), "Camilla_factory": lambda: check_camilla_factory(expected),
              "Camilla_API": lambda: check_camilla_api(expected)}
    deadline = began + 235
    while time.monotonic() < deadline:
        for name, probe in probes.items():
            if name not in checks:
                try:
                    checks[name] = probe()
                    errors.pop(name, None)
                except Exception as error:
                    errors[name] = f"{type(error).__name__}: {error}"[:300]
        if len(checks) == len(probes):
            break
        write_report(dict(report, status="OBSERVING", pending=errors))
        time.sleep(3)
    if set(errors)=={"Camilla_API"} and len(checks)==len(probes)-1:
        report["cold_Camilla_API"]={"result":"UNAVAILABLE","error":errors["Camilla_API"]}
        try:
            report["cold_Camilla_API"]["diagnostics"]=failure_details()
            write_report(dict(report,status="DIAGNOSING_CAMILLA"))
            if not (HOST/"control.sock").is_socket():
                command=["/sbin/rc-service","daphile-audio-host","start"]
            else:
                command=["/usr/bin/python3","-I","-B",str(HOST/"code/hostctl.py"),"sync",
                         "--root",str(HOST),"--module","DaphileCamillaDSP"]
            started=subprocess.run(command,capture_output=True,timeout=28)
            report["Camilla_diagnostic_retry"]={"command":" ".join(command),"exit":started.returncode}
            finish=time.monotonic()+66
            while time.monotonic()<finish:
                try:
                    checks["Camilla_API"]=check_camilla_api(expected)
                    errors.pop("Camilla_API",None)
                    break
                except Exception as error:
                    errors["Camilla_API"]=f"{type(error).__name__}: {error}"[:300]
                    time.sleep(3)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            report["Camilla_diagnostic_retry"]={"error":type(error).__name__}
    try:
        observed = loop_root()
        if observed != expected["rootfs_sha256"]:
            raise RuntimeError("Unexpected actual squashfs hash")
        checks["mounted_RC7_root"] = observed
    except Exception as error:
        errors["mounted_RC7_root"] = str(error)[:300]
    report["seconds_since_observer"] = round(time.monotonic() - began, 3)
    report["status"] = ("PASS_AFTER_MANUAL_MODULE_START" if report.get("cold_Camilla_API") else "PASS") if not errors else "REVIEW"
    if errors:
        report["errors"] = errors
        try:
            report["diagnostics"] = failure_details()
        except (OSError, subprocess.TimeoutExpired):
            pass
    write_report(report)


if __name__ == "__main__":
    main()
