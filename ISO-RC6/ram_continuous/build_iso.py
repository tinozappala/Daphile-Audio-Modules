"""Utilities used by the RC6 compositor to read and rebuild ISO images."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
TOOLKIT = REPO / "vendor/review-kit/daphile-v5-ssh/tools"
sys.path.insert(0, str(Path(__file__).resolve().parent / "support"))
sys.path.insert(0, str(TOOLKIT / "python"))
from pseudo_fs import MARKER, inventory, read_header  # Used by the compositor.
import pycdlib

WORK = REPO / "iso_maintenance_20261117/build"


def digest(path, kind="sha256"):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, kind).hexdigest()


def command(tool, args, log, timeout=300):
    entries = [str(value).replace("\\", "/") for value in args]
    env = os.environ.copy()
    if os.name == "nt":
        directory = Path(env.get("DAPHILE_ISO_TOOLS", TOOLKIT / "usr/bin"))
        binary = directory / (tool + ".exe")
        env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")
        if tool == "xorriso":
            entries = [re.sub(r"([A-Za-z]):/", lambda m: "/cygdrive/" + m[1].lower() + "/", value)
                       for value in entries]
    else:
        directory = env.get("DAPHILE_ISO_TOOLS")
        binary = (Path(directory) / tool) if directory else shutil.which(tool)
        if not binary:
            raise RuntimeError(f"Serve {tool}. Installare squashfs-tools e xorriso prima di proseguire.")
    with (WORK / log).open("wb") as output:
        result = subprocess.run([str(binary), *entries], env=env, stdout=output,
                                stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{tool} si è fermato. Dettagli in {WORK / log}.")


def extract(iso, inside, target):
    reader = pycdlib.PyCdlib()
    reader.open(str(iso))
    try:
        length = reader.get_record(rr_path=inside).data_length
        reader.get_file_from_iso(str(target), rr_path=inside)
        if target.stat().st_size != length:
            raise ValueError(f"Dimensione inattesa per {inside}")
    finally:
        reader.close()
