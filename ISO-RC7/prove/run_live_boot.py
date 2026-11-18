"""Boot the exact release CD in a bounded, local QEMU test."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from observe_guest import digest

CAM_SHA1 = "9cbaadfb091ec318d54edf1072d7cb55fe731ae2"
JS = "6662f65dc1564c6e90c9af8aef949db3e25cb083c3aa707ea8251bcf29f85b39"


def http(base, path, payload=None, cookie=None):
    headers = {"Origin": base}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(payload).encode()
    if cookie is not None:
        headers["Cookie"] = cookie
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(base + path, data=payload, headers=headers), timeout=8) as response:
        return response.read(4 * 1024 * 1024), response.headers


def web_checks(base, report):
    html, _ = http(base, "/cgi-bin/AutoTune")
    js, _ = http(base, "/js/dat-core.js")
    catalog, _ = http(base, "/cgi-bin/AudioPlugins/extensions.xml")
    entry = ET.fromstring(catalog).find("./plugins/plugin[@name='DaphileCamillaDSP']")
    if entry is None or entry.get("version") != "0.4.49" or entry.findtext("sha") != CAM_SHA1 or (
            f"/js/dat-core.js?v={JS[:15]}".encode() not in html or hashlib.sha256(js).hexdigest() != JS):
        raise RuntimeError("Unexpected versions in the served UI or catalog")
    body, _ = http(base, "/jsonrpc.js", {"id": 1, "method": "slim.request", "params": ["", ["version", "?"]]})
    if json.loads(body)["result"]["_version"] != "9.1.1":
        raise RuntimeError("Unexpected LMS version")
    report["web_core"] = {"AutoTune_JS_sha256": JS, "Camilla_catalog_version": entry.get("version"),
                          "Camilla_catalog_sha1": entry.findtext("sha"), "LMS": "9.1.1"}
    body, _ = http(base, "/cgi-bin/AutoTune", {"action": "status"})
    status = json.loads(body)
    if status.get("enabled") is not True or any(job.get("running") for job in status.get("jobs", [])):
        raise RuntimeError("AutoTune GUI broker is unavailable or busy")
    report["web_AutoTune_broker"] = "PASS"
    path = "/cgi-bin/CamillaPlugin"
    _, headers = http(base, path + "/api/session")
    body, _ = http(base, path + "/api/status", cookie=headers["Set-Cookie"].split(";", 1)[0])
    status = json.loads(body)
    if status.get("module_version") != "0.4.49":
        raise RuntimeError("Unexpected Camilla API version")
    report["web_Camilla_API"] = {"session_http": 200, "status_http": 200, "version": status["module_version"],
                                 "audio_mode": status.get("audio", {}).get("mode")}


class Machine:
    def __init__(self, args, iso):
        self.args = args
        self.case = args.case
        self.case.mkdir(parents=True)
        self.name = "daphile-rc7-" + args.mode
        self.qmp_port, self.http_port = (28499, 28480) if args.mode == "bios" else (28599, 28580)
        self.base = f"http://127.0.0.1:{self.http_port}"
        for port in (self.qmp_port, self.http_port):
            with socket.socket() as test:
                if test.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(f"Test port in use: {port}")
        qemu = args.qemu
        self.env = dict(os.environ, PATH=str(qemu / "bin") + os.pathsep + os.environ["PATH"])
        share = qemu / "share/qemu"
        self.argv = [
            qemu / "bin/qemu-system-x86_64.exe", "-name", self.name, "-L", share,
            "-machine", "q35", "-accel", "tcg,thread=multi,tb-size=128", "-m", "2048", "-smp", "2",
            "-cpu", "max,avx2=off", "-display", "none", "-monitor", "none",
            "-serial", "file:" + str(self.case / "serial.log"),
            "-qmp", f"tcp:127.0.0.1:{self.qmp_port},server=on,wait=off",
            "-nic", f"user,restrict=on,model=e1000,hostfwd=tcp:127.0.0.1:{self.http_port}-:80",
            "-drive", "file=" + str(iso) + ",format=raw,media=cdrom,readonly=on", "-boot", "order=d,strict=on",
        ]
        if args.mode == "uefi":
            variables = self.case / "vars.fd"
            shutil.copyfile(share / "edk2-i386-vars.fd", variables)
            self.argv.extend(["-drive", f"if=pflash,unit=0,format=raw,readonly=on,file={share / 'edk2-x86_64-code.fd'}",
                              "-drive", f"if=pflash,unit=1,format=raw,file={variables}"])
        if args.data:
            metadata = json.loads(args.data.with_suffix(".json").read_text())
            if digest(args.data) != metadata["sha256"]:
                raise RuntimeError("Test DATA backing differs")
            self.uuid = metadata["uuid"]
            disk = self.case / "data.qcow2"
            subprocess.run([str(qemu / "bin/qemu-img.exe"), "create", "-f", "qcow2", "-F", "raw",
                            "-b", str(args.data), str(disk)], env=self.env, capture_output=True,
                           check=True, timeout=20)
            self.argv.extend(["-drive", f"file={disk},format=qcow2,if=virtio", "-S"])
            self.argv.extend(["-audiodev", "none,id=qa-audio", "-device", "ich9-intel-hda",
                              "-device", "hda-duplex,audiodev=qa-audio"])

    def start(self):
        self.started = time.monotonic()
        with (self.case / "qemu.log").open("xb") as log:
            self.process = subprocess.Popen([str(arg) for arg in self.argv], env=self.env, stdout=log,
                                            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        print(json.dumps({"event": "VM_STARTED", "mode": self.args.mode, "pid": self.process.pid}), flush=True)

    def qmp(self, action, arguments=None):
        with socket.create_connection(("127.0.0.1", self.qmp_port), timeout=5) as connection:
            with connection.makefile("rwb", buffering=0) as stream:
                json.loads(stream.readline())

                def call(command, values=None):
                    body = {"execute": command}
                    if values is not None:
                        body["arguments"] = values
                    stream.write(json.dumps(body).encode() + b"\n")
                    while True:
                        reply = json.loads(stream.readline())
                        if "error" in reply:
                            raise RuntimeError(reply["error"])
                        if "return" in reply:
                            return reply["return"]

                call("qmp_capabilities")
                if call("query-name")["name"] != self.name:
                    raise RuntimeError("Wrong QMP instance")
                return call(action, arguments)

    def stop(self):
        pressed_power = False
        used_quit = False
        if self.process.poll() is None:
            try:
                self.qmp("system_powerdown")
                pressed_power = True
                self.process.wait(timeout=40)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    try:
                        self.qmp("quit")
                        used_quit = True
                        self.process.wait(timeout=8)
                    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                        self.process.terminate()
                        self.process.wait(timeout=8)
        return {"pid_exited": self.process.poll() is not None, "acpi_requested": pressed_power, "qmp_quit": used_quit}

    def boot_wait(self):
        if self.args.data:
            self.boot_instrumented()
        report = {"status": "REVIEW", "mode": self.args.mode, "iso_sha256": self.args.sha256,
                  "DATA": "new ext4 fixture with observer only" if self.args.data else "default Live tmpfs"}
        last = None
        deadline = self.started + self.args.timeout
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                web_checks(self.base, report)
                web_pass = True
            except (OSError, ValueError, KeyError, RuntimeError, ET.ParseError) as error:
                web_pass = False
                report["last_web_error"] = f"{type(error).__name__}: {error}"[:250]
            guest = None
            if self.args.data:
                try:
                    body, _ = http(self.base, "/__rc7_qa__.json")
                    guest = json.loads(body)
                    report["guest"] = guest
                except (OSError, ValueError):
                    pass
            if web_pass and (not self.args.data or guest and guest["status"] == "PASS" and guest["efi"]):
                report["status"] = "PASS"
                report.pop("last_web_error", None)
                break
            summary = (bool(report.get("web_core")), bool(report.get("web_AutoTune_broker")),
                       bool(report.get("web_Camilla_API")), (guest or {}).get("status"), report.get("last_web_error"))
            if last != summary:
                print(json.dumps({"event": "BOOT_PROGRESS", "seconds": round(time.monotonic() - self.started),
                                  "core": summary[0], "autotune": summary[1], "camilla": summary[2],
                                  "observer": summary[3], "pending": summary[4]}), flush=True)
                last = summary
            if guest and guest["status"] in ("REVIEW","PASS_AFTER_MANUAL_MODULE_START"):
                break
            time.sleep(6)
        report["boot_seconds"] = round(time.monotonic() - self.started, 2)
        return report

    def boot_instrumented(self):
        # Add only transient console and DATA arguments in the actual GRUB shell.
        def key(name):
            self.qmp("human-monitor-command", {"command-line": "sendkey " + name + " 10"})
        def serial():
            return (self.case / "serial.log").read_bytes()
        end=time.monotonic()+10
        while True:
            try:
                self.qmp("cont")
                break
            except OSError:
                if time.monotonic()>=end:raise
                time.sleep(.2)
        end=time.monotonic()+70
        while time.monotonic()<end and b"grub> " not in serial():
            key("c")
            time.sleep(1)
        if b"grub> " not in serial():
            self.qmp("screendump",{"filename":str(self.case/"boot-menu.ppm")})
            raise RuntimeError("GRUB shell prompt did not appear")
        key("ctrl-u")
        time.sleep(.12)
        keys={" ":"spc","/":"slash","=":"equal","-":"minus","_":"shift-minus",",":"comma"}
        for command in ("set root=cd0","set gfxpayload=keep",
                        "linux /boot/fw2610191845/kernel daphile=fw2610191845 panic=1 console=tty2 "
                        "console=ttyS0,115200n8 loglevel=5 efi=runtime live uefi=64 data_uuid="+self.uuid,
                        "initrd /boot/fw2610191845/initrd", "boot"):
            offset=len(serial())
            for char in command:
                key(keys.get(char,"shift-"+char.lower() if char.isupper() else char))
                time.sleep(.04)
            key("ret")
            if command=="boot":
                self.started=time.monotonic()
                print(json.dumps({"event":"UEFI_KERNEL_COMMAND_SENT"}),flush=True)
                return
            end=time.monotonic()+30
            while time.monotonic()<end:
                response=serial()[offset:]
                if b"error:" in response:raise RuntimeError("GRUB refused "+command.split()[0])
                if b"grub> " in response:break
                time.sleep(.2)
            else:raise RuntimeError("GRUB did not complete "+command.split()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("bios", "uefi"), required=True)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--timeout", type=int, default=1020)
    args = parser.parse_args()
    for key in ("iso", "qemu", "case", "data"):
        value = getattr(args, key)
        if value:
            setattr(args, key, value.resolve())
    if digest(args.iso) != args.sha256:
        raise RuntimeError("Boot input is not the verified ISO")
    machine = Machine(args, args.iso)
    machine.start()
    try:
        report = machine.boot_wait()
    except Exception as error:
        report = {"status": "REVIEW", "mode": args.mode, "error": f"{type(error).__name__}: {error}"}
    finally:
        cleanup = machine.stop()
    report["cleanup"] = cleanup
    report["ISO_byte_verified_after_boot"] = digest(args.iso) == args.sha256
    (args.case / "QA_RESULT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
