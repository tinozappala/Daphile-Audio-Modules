"""Controlla i file della RC6 o prepara una cartella locale di ricostruzione."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
from zipfile import ZipFile, BadZipFile

ROOT = Path(__file__).resolve().parent
RELEASE = json.loads((ROOT / "RELEASE.json").read_text(encoding="utf-8"))
ISO = "iso_maintenance_20261117"
RC5 = "DAPHILE_RAM_A7_RC5_20261117"
VENDOR = "vendor/review-kit/daphile-v5-ssh/tools/"
PROJECT = (
    f"{ISO}/build_iso.py",
    f"{ISO}/bootstrap_release.py",
    f"{ISO}/consolidate_camilla.py",
    f"{ISO}/COMPONENTI_VERIFICATI.json",
    "ram_continuous/build_iso.py",
    "ram_continuous/support/pseudo_fs.py",
)


def verify(path, kind, destination=None):
    expected = RELEASE[kind]
    if path.stat().st_size != expected["bytes"]:
        raise ValueError(f"{path.name} ha una dimensione diversa da quella attesa.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
            if destination is not None:
                destination.write(block)
    if digest.hexdigest() != expected["sha256"]:
        raise ValueError(f"{path.name} ha un'impronta diversa da quella attesa.")
    print(f"{path.name}  OK")


def checked_relative(name):
    relative = PurePosixPath(name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Percorso non valido nell'archivio.")
    if "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Nome non valido nell'archivio.")
    return Path(*relative.parts)


def copy_member(archive, name, directory):
    target = directory / checked_relative(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name) as src, target.open("xb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)


def private_inputs(archive, directory):
    """Only materialize the fixed, verified inputs needed by the compositor."""
    components = json.loads((ROOT / ISO / "COMPONENTI_VERIFICATI.json").read_text(encoding="utf-8"))
    names = {f"{RC5}/RC5_FULL_FILESYSTEM_TABLE.json", "camilla_current/APPROVAL.json",
             "camilla_current/" + RELEASE["camilla"]["file"], f"{ISO}/LEGGIMI_ISO_IT.md"}
    for component in components.values():
        items = component["files"] + component.get("license_files", []) + list(component.get("boot_files", {}).values())
        for item in items:
            name = item["source"]
            if not name.startswith("components/"):
                raise ValueError("Oggetto runtime fuori dalla cartella componenti.")
            names.add(f"{ISO}/{name}")
    names.update(member.filename for member in archive.infolist()
                 if member.filename.startswith(VENDOR) and not member.is_dir())
    for name in sorted(names):
        copy_member(archive, name, directory)
    report = json.loads(archive.read(f"{RC5}/ISO_RC5_BUILD_REPORT.json"))
    needed = {key: report[key] for key in ("kernel_sha256", "initrd_sha256", "rootfs_sha256")}
    (directory / RC5 / "ISO_RC5_BUILD_REPORT.json").write_text(json.dumps(needed, indent=2) + "\n", encoding="utf-8")


def prepare(source, base, directory):
    directory = directory.resolve()
    if directory.is_relative_to(ROOT.parent) or ROOT.is_relative_to(directory):
        raise ValueError("Scegli una nuova cartella di lavoro esterna al repository.")
    verify(source, "sorgenti")
    directory.mkdir(mode=0o700, exist_ok=False)
    completed = False
    try:
        target = directory / RC5 / RELEASE["base"]["file"]
        target.parent.mkdir()
        with target.open("xb") as output:
            verify(base, "base", output)
        with ZipFile(source) as archive:
            private_inputs(archive, directory)
        for name in PROJECT:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        verify(directory / "camilla_current" / RELEASE["camilla"]["file"], "camilla")
        completed = True
    finally:
        if not completed:
            shutil.rmtree(directory)
    print(f"Cartella pronta: {directory}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("verifica", help="confronta dimensioni e SHA-256")
    for name in RELEASE:
        check.add_argument("--" + name, type=Path, metavar="FILE")
    setup = commands.add_parser("prepara", help="prepara i soli file necessari fuori dal repository")
    setup.add_argument("--sorgenti", type=Path, required=True)
    setup.add_argument("--base", type=Path, required=True)
    setup.add_argument("--cartella", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepara":
            prepare(args.sorgenti, args.base, args.cartella)
        else:
            given = {key: getattr(args, key) for key in RELEASE if getattr(args, key) is not None}
            if not given:
                parser.error("indica almeno un file da verificare")
            for key, path in given.items():
                verify(path, key)
    except (OSError, ValueError, BadZipFile, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
