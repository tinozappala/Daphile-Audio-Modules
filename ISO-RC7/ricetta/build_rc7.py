"""Compose the generic RC7 ISO from the exact RC6 ISO and tested B4f bundle."""

import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from time import monotonic
import xml.etree.ElementTree as ET
import zipfile

from pseudo_fs import MARKER, inventory, read_header

RC6_SHA = "c11c16fe07466af293c437d7eaaaa5a514e586c913e4b64b1197f8f979f63ed3"
B4F_SHA = "71772ef58e03434c18ee540f3f10b9b9bfec4318a22b3cb399ecdcf50154db85"
INTERFACE_SHA = "6a2f4e5951b98d405ac963dba171c7adbc30de6d745b2371a4754d8883929b1c"
CAMILLA_SHA = "64427d58da63f8383148fd8c5742fcb559186d9fdbce56806b5ba061096dfb27"
CAMILLA_OLD = "DaphileCamillaDSP-0.4.47.zip"
CAMILLA_NAME = "DaphileCamillaDSP"
BOOT = "boot/fw2610191845"
FACTORY = "usr/local/share/daphile-release/"
EDITION = "RAM-A7-Camilla049-RME057-AutoTuneB4f-RC7-20261118"
EPOCH = int(datetime.datetime(2026, 11, 18, tzinfo=datetime.timezone.utc).timestamp())


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def event(phase, **fields):
    print(json.dumps(dict(phase=phase, **fields)), flush=True)


def command(args, tool, parameters, log, timeout=300):
    executable = (
        str(args.tools_dir / (tool + (".exe" if os.name == "nt" else "")))
        if args.tools_dir else tool
    )
    environment = dict(os.environ)
    if args.tools_dir:
        environment["PATH"] = str(args.tools_dir) + os.pathsep + environment["PATH"]
    parameters = [x.as_posix() if isinstance(x, Path) else str(x) for x in parameters]
    if os.name == "nt" and tool == "xorriso":
        parameters = [re.sub(r"([A-Za-z]):/", lambda m: "/cygdrive/" + m[1].lower() + "/", x) for x in parameters]
    with (args.work / log).open("wb") as output:
        result = subprocess.run(
            [executable, *parameters], stdout=output, stderr=subprocess.STDOUT,
            env=environment, timeout=timeout,
        )
    require(result.returncode == 0, "See " + str(args.work / log))


def patch_root(pseudo, before, autotune, interface, guide, destination):
    header = read_header(pseudo)
    entries = header["entries"]
    rows = {name: row["raw"] for name, row in entries.items()}
    expected = dict(before)
    overlay = {}
    removed = []

    def put(name, value, mode=None):
        prior = before.get(name, {})
        require(not prior or prior["kind"] == "R", "Unexpected inode at " + name)
        mode = prior.get("mode", 0o644) if mode is None else mode
        if prior.get("sha256") != sha_bytes(value) or prior.get("mode") != mode:
            overlay[name] = (value, mode)

    def read(name):
        row = entries[name]
        require(row["kind"] == "R", "Expected a regular input file at " + name)
        with pseudo.open("rb") as stream:
            stream.seek(header["start"] + row["offset"])
            return stream.read(row["bytes"])

    with zipfile.ZipFile(autotune) as archive:
        root = "AutoTune-B4f/"
        require(archive.testzip() is None, "Invalid AutoTune ZIP")
        source = json.loads(archive.read(root + "SOURCE_MANIFEST.json"))
        update = json.loads(archive.read(root + "INSTALL_MANIFEST.json"))
        for row in update:
            name = row["path"]
            require(before.get(name, {}).get("sha256") in row["accepted_before_sha256"],
                    "RC6 preimage differs at " + name)
            content = archive.read(root + "payload/" + name)
            require(sha_bytes(content) == row["sha256"], "Altered B4f member: " + name)
            put(name, content, row["mode"])
        require(json.loads(archive.read(root + "payload/opt/daphile-autotune/CANDIDATE.json"))["version"] == "B4f",
                "B4f version marker missing")
        entry = json.loads(archive.read(root + "compatibility/APPROVED_ENTRY_0.4.49.json"))
        camilla = archive.read(root + "compatibility/" + entry["archive"])

    for row in source:
        name = row["path"]
        prior = before.get(name, {})
        observed = (sha_bytes(overlay[name][0]), overlay[name][1]) if name in overlay else (
            prior.get("sha256"), prior.get("mode"))
        require(observed == (row["sha256"], row["mode"]), "B4f source or mode differs at " + name)

    with zipfile.ZipFile(interface) as archive:
        revisions = json.loads(archive.read("INSTALL_MANIFEST.json"))
        for row in revisions:
            name = row["path"]
            prior = sha_bytes(overlay[name][0]) if name in overlay else before.get(name, {}).get("sha256")
            require(prior in row["accepted_before_sha256"], "B4f interface preimage differs at " + name)
            value = archive.read("payload/" + name)
            require(sha_bytes(value) == row["sha256"], "B4f interface content differs at " + name)
            put(name,value,row["mode"])

    require(entry["version"] == "0.4.49" and entry["sha256"] == CAMILLA_SHA
            and sha_bytes(camilla) == CAMILLA_SHA and entry["bytes"] == len(camilla)
            and entry["sha1"] == hashlib.sha1(camilla).hexdigest(), "Camilla approval/ZIP mismatch")
    approval_path = FACTORY + "audio-host/approvals.json"
    approvals = json.loads(read(approval_path))
    previous, = approvals[CAMILLA_NAME]
    require(previous["version"] == "0.4.47" and set(previous["files"]) == set(entry["files"]),
            "Unexpected Camilla base catalog")
    approvals[CAMILLA_NAME] = [entry]
    put(approval_path, json_bytes(approvals))
    for folder in (FACTORY + "audio-host/bundles/", "usr/local/share/mediaserver-init/plugins/"):
        old = folder + CAMILLA_OLD
        require(before[old]["sha256"] == previous["sha256"], "Old factory ZIP differs")
        rows.pop(old)
        expected.pop(old)
        removed.append(old)
        put(folder + entry["archive"], camilla)
    changed_members = []
    with zipfile.ZipFile(io.BytesIO(camilla)) as archive:
        prefix = CAMILLA_NAME + "/"
        require(sorted(archive.namelist()) == sorted(prefix + name for name in entry["files"]),
                "Camilla ZIP member set differs")
        for name, sha in entry["files"].items():
            value = archive.read(prefix + name)
            factory_file = FACTORY + "plugins/" + prefix + name
            require(before[factory_file]["sha256"] == previous["files"][name] and sha_bytes(value) == sha,
                    "Unexpected Camilla source: " + name)
            if sha != previous["files"][name]:
                changed_members.append(name)
            put(factory_file, value)
        xml = ET.fromstring(archive.read(prefix + "install.xml"))
        require(xml.findtext("version") == entry["version"]
                and xml.findtext("targetApplication/minVersion") == entry["minTarget"]
                and xml.findtext("targetApplication/maxVersion") == entry["maxTarget"],
                "Camilla LMS metadata differs")
    require(sorted(changed_members) == ["backend/native_player.py", "backend/peq/__init__.py", "install.xml"],
            "Unexpected Camilla delta")

    release = json.loads(read(FACTORY + "release.json"))
    release.update(release=EDITION, Camilla_module="0.4.49", AutoTune="B4f")
    put(FACTORY + "release.json", json_bytes(release))
    text = read("etc/daphile-custom-release").decode()
    text = text.replace("RC6 20261118", "RC7 20261118").replace("0.4.47 / engine", "0.4.49 / engine")
    text = text.replace("AutoTune B4e.", "AutoTune B4f.")
    require("RC7 20261118" in text and "0.4.49 / engine" in text and "AutoTune B4f." in text,
            "Release banner could not be refreshed")
    put("etc/daphile-custom-release", text.encode())
    put(FACTORY + "MANUTENZIONE_IT.md", guide)
    for name in rows:
        require(not name.endswith(("/authorized_keys", "/.bash_history", "/.ash_history"))
                and not (name.startswith("etc/ssh/ssh_host_") and name.endswith(("_key", "_key.pub"))),
                "Unexpected instance credential or history")
    require(all(line.split(":")[1] in ("!", "!!", "*", "") for line in read("etc/shadow").decode().splitlines()
                if ":" in line), "Unexpected password hash in the base")

    offset = header["data_bytes"]
    for name, (value, mode) in sorted(overlay.items()):
        require(name.isascii() and not any(c.isspace() for c in name), "Unsupported overlay path " + name)
        for parent in reversed(PurePosixPath(name).parents):
            folder = parent.as_posix()
            if folder != "." and folder not in rows:
                rows[folder] = f"{folder} D {EPOCH} 755 0 0".encode()
                expected[folder] = dict(kind="D", mtime=EPOCH, mode=0o755, uid=0, gid=0)
        uid = before.get(name, {}).get("uid", 0)
        gid = before.get(name, {}).get("gid", 0)
        rows[name] = f"{name} R {EPOCH} {mode:o} {uid} {gid} {len(value)} {offset} 0".encode()
        expected[name] = dict(kind="R", mtime=EPOCH, mode=mode, uid=uid, gid=gid, bytes=len(value),
                              data_flags=0, sha256=sha_bytes(value))
        offset += len(value)
    with destination.open("xb") as stream, pseudo.open("rb") as base:
        stream.write(b"\n".join(rows.values()) + b"\n" + MARKER)
        base.seek(header["start"])
        shutil.copyfileobj(base, stream, 1024 * 1024)
        for _, (value, _) in sorted(overlay.items()):
            stream.write(value)
    return expected, sorted(overlay), removed, len(source), len(entry["files"]), len(revisions)


def package_iso(args, rootfs, guide, iso):
    import pycdlib

    tree = args.work / "iso-tree"
    tree.mkdir()
    reader = pycdlib.PyCdlib()
    reader.open(str(args.base))
    try:
        for folder, _, files in reader.walk(rr_path="/"):
            destination = tree / folder.lstrip("/")
            destination.mkdir(parents=True, exist_ok=True)
            for name in files:
                inside = folder.rstrip("/") + "/" + name
                if inside != "/" + BOOT + "/rootfs":
                    reader.get_file_from_iso(str(destination / name), rr_path=inside)
    finally:
        reader.close()
    boot = tree / BOOT
    os.link(rootfs, boot / "rootfs")
    hashes = {name: digest(boot / name) for name in ("kernel", "initrd")}
    sums = []
    for line in (boot / "md5sum.txt").read_bytes().splitlines():
        _, name = line.split(None, 1)
        sums.append(digest(boot / Path(name.decode()).name, "md5").encode() + b"  " + name)
    (boot / "md5sum.txt").write_bytes(b"\r\n".join(sums) + b"\r\n")
    (tree / "documentazione/LEGGIMI.txt").write_bytes(guide)
    for path in tree.rglob("*"):
        os.utime(path, (EPOCH, EPOCH))
    command(args, "xorriso", [
        "-as", "mkisofs", "-V", "DaphileLIVE", "--modification-date=2026111800000000",
        "--set_all_file_dates", "2026111800000000", "--gpt_disk_guid", "cb2c789f9e655a058579eeade4eb23dd",
        "-uid", "0", "-gid", "0", "-dir-mode", "0755", "-file-mode", "0644",
        "-isohybrid-mbr", "--interval:local_fs:0s-15s:zero_mbrpt,zero_gpt:" + args.base.as_posix(),
        "-partition_cyl_align", "off", "-partition_offset", "0", "--mbr-force-bootable",
        "-iso_mbr_part_type", "0x00", "-c", "isolinux/boot.cat", "-b", "isolinux/isolinux.bin",
        "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table", "-eltorito-alt-boot",
        "-e", "efi2505251549.img", "-no-emul-boot", "-boot-load-size", "2880",
        "-isohybrid-gpt-basdat", "-o", iso, tree,
    ], "xorriso.log")
    command(args, "xorriso", ["-indev", iso, "-report_el_torito", "plain", "-report_system_area", "plain"],
            "boot-catalog.log")
    catalog = (args.work / "boot-catalog.log").read_text(errors="replace")
    require(all(item in catalog for item in ("BIOS", "UEFI", "MBR isohybrid", "GPT")), "Invalid hybrid boot catalog")
    reader.open(str(iso))
    try:
        for name, sha in {**hashes, "rootfs": digest(rootfs)}.items():
            output = args.work / "iso-readback"
            reader.get_file_from_iso(str(output), rr_path="/" + BOOT + "/" + name)
            require(digest(output) == sha, "Packaged data differs at " + name)
            output.unlink()
    finally:
        reader.close()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "autotune", "interface", "work", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tools-dir", type=Path)
    parser.add_argument("--pycdlib-dir", type=Path)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if value is not None:
            setattr(args, name, value.resolve())
    if args.pycdlib_dir:
        sys.path.insert(0, str(args.pycdlib_dir))
    import pycdlib

    started = monotonic()
    require(digest(args.base) == RC6_SHA and digest(args.autotune) == B4F_SHA and
            digest(args.interface) == INTERFACE_SHA, "Input checksum mismatch")
    args.out.mkdir(parents=True, exist_ok=True)
    final = args.out / ("Daphile-" + EDITION + ".iso")
    require(not final.exists() and not (args.out / "BUILD_REPORT.json").exists(), "Choose a new output directory")
    args.work.mkdir(parents=True)
    base_root = args.work / "base-rootfs"
    reader = pycdlib.PyCdlib()
    reader.open(str(args.base))
    try:
        reader.get_file_from_iso(str(base_root), rr_path="/" + BOOT + "/rootfs")
    finally:
        reader.close()
    pseudo = args.work / "base.pseudo"
    command(args, "unsquashfs", ["-pf", pseudo, base_root], "base-extract.log")
    base_sha = digest(base_root)
    base_root.unlink()
    before = inventory(pseudo)
    guide = Path(__file__).with_name("NOTA_ISO_IT.md").read_bytes()
    recipe = args.work / "rc7.pseudo"
    wanted, changed, removed, source_files, camilla_files, revised_files = patch_root(
        pseudo, before, args.autotune, args.interface, guide, recipe)
    pseudo.unlink()
    empty = args.work / "empty"
    empty.mkdir()
    rootfs = args.work / "rootfs"
    mode = before["/"]
    event("ROOTFS_BUILD", changed=len(changed), removed=len(removed))
    command(args, "mksquashfs", [
        empty, rootfs, "-pf", recipe, "-noappend", "-no-progress", "-processors", 2, "-mem", "256M",
        "-comp", "xz", "-b", 1048576, "-Xdict-size", 16384, "-reproducible", "-mkfs-time", EPOCH,
        "-root-time", mode["mtime"], "-root-mode", format(mode["mode"], "o"),
        "-root-uid", mode["uid"], "-root-gid", mode["gid"],
    ], "mksquashfs.log", timeout=1200)
    recipe.unlink()
    back = args.work / "readback.pseudo"
    command(args, "unsquashfs", ["-pf", back, rootfs], "rootfs-readback.log")
    actual = inventory(back)
    if actual != wanted:
        differences = {key: {"expected": wanted.get(key), "actual": actual.get(key)}
                       for key in set(actual) | set(wanted) if actual.get(key) != wanted.get(key)}
        (args.work / "READBACK_FAILURE.json").write_bytes(json_bytes(differences))
        raise RuntimeError("Filesystem readback differs")
    back.unlink()
    event("ROOTFS_VERIFIED", nodes=len(actual))
    partial = args.work / (final.name + ".partial")
    hashes = package_iso(args, rootfs, guide, partial)
    report = dict(status="COMPOSED_AND_FULL_READBACK_VERIFIED", name=final.name,
                  bytes=partial.stat().st_size, sha256=digest(partial), rootfs_sha256=digest(rootfs),
                  base_iso_sha256=RC6_SHA, base_rootfs_sha256=base_sha, autotune_bundle_sha256=B4F_SHA,
                  camilla_bundle_sha256=CAMILLA_SHA, kernel_sha256=hashes["kernel"], initrd_sha256=hashes["initrd"],
                  firmware_on_live_media=BOOT.split("/")[-1], filesystem_nodes=len(actual),
                  changed_or_added=changed, removed=removed, autotune_sources_verified=source_files,
                  camilla_members_verified=camilla_files, autotune_interface_sha256=INTERFACE_SHA,
                  autotune_interface_files=revised_files, hybrid_boot_catalog=True, elapsed_seconds=round(monotonic()-started, 2))
    require(not final.exists(), "Output created concurrently")
    os.link(partial, final)
    partial.unlink()
    (args.out / "BUILD_REPORT.json").write_bytes(json_bytes(report))
    event("ISO_READBACK_PASS", name=final.name, bytes=report["bytes"], sha256=report["sha256"],
          elapsed_seconds=report["elapsed_seconds"])


if __name__ == "__main__":
    main()
