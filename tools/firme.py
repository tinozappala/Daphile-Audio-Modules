"""Firma di pubblicazione dei moduli. Tino Zappala, Milano - 2026."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from zipfile import ZipFile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


ROOT = Path(__file__).resolve().parents[1]
CATALOG = "FIRME.json"
LABEL = "Tino Zappala, Milano - 2026"
FORMAT = "Daphile firme di pubblicazione v1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def display(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def current_files():
    if (ROOT / ".git").exists():
        names = git("ls-files", "-z").decode("utf-8").split("\0")
    else:
        names = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts]
    return sorted(name for name in names if name and name != CATALOG)


def records(kind, path, data, revision=None):
    record = {"tipo": kind, "percorso": path, "sha256": digest(data), "firma_nominativa": LABEL}
    if revision:
        record["revisione"] = revision
    yield record
    if path.lower().endswith(".zip") and kind != "storico":
        with ZipFile(io.BytesIO(data)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) != len({m.filename for m in members}):
                raise ValueError("Nome ripetuto nello ZIP " + path)
            for member in sorted(members, key=lambda m: m.filename):
                yield from records("voce ZIP", path + "!/" + member.filename, archive.read(member))


def current_records():
    for name in current_files():
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("Percorso esterno alla raccolta: " + name)
        yield from records("file", name, path.read_bytes())


def older_records(current):
    known = {(r["percorso"], r["sha256"]) for r in current if r["tipo"] == "file"}
    visited = set()
    for commit in git("rev-list", "HEAD").decode("ascii").splitlines():
        entries = git("ls-tree", "-r", "-z", commit).split(b"\0")
        for entry in filter(None, entries):
            header, path = entry.split(b"\t", 1)
            _, kind, blob = header.decode("ascii").split()
            name = path.decode("utf-8")
            if kind != "blob" or (name, blob) in visited:
                continue
            visited.add((name, blob))
            data = git("cat-file", "blob", blob)
            if (name, digest(data)) not in known:
                yield from records("storico", name, data, commit)


def signed(record, private):
    message = canonical([FORMAT, record])
    return {**record, "ed25519": base64.b64encode(private.sign(message)).decode("ascii")}


def verify_signature(record, public):
    fields = {k: v for k, v in record.items() if k != "ed25519"}
    if fields["firma_nominativa"] != LABEL:
        raise ValueError("Firma nominativa diversa da quella prevista")
    public.verify(base64.b64decode(record["ed25519"], validate=True), canonical([FORMAT, fields]))
    return fields


def catalog_record(catalog):
    return {"tipo": "catalogo", "percorso": CATALOG, "sha256": digest(canonical(catalog)),
            "firma_nominativa": LABEL}


def generate():
    if not (ROOT / ".git").exists():
        raise ValueError("Crea il catalogo dal clone Git, dopo avere preparato i file da pubblicare.")
    private = Ed25519PrivateKey.generate()
    key = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    current = list(current_records())
    catalog = {"formato": FORMAT, "firma_nominativa": LABEL, "algoritmo": "Ed25519",
               "chiave_pubblica_base64": base64.b64encode(key).decode("ascii"),
               "file": [signed(r, private) for r in current],
               "versioni_precedenti": [signed(r, private) for r in older_records(current)]}
    catalog["firma_catalogo"] = signed(catalog_record(catalog), private)
    destination = ROOT / CATALOG
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(display(catalog))
    temporary.replace(destination)


def verify():
    raw = (ROOT / CATALOG).read_bytes()
    catalog = json.loads(raw)
    if display(catalog) != raw or catalog["formato"] != FORMAT or catalog["algoritmo"] != "Ed25519":
        raise ValueError("Il catalogo ha un formato diverso da quello firmato")
    key = base64.b64decode(catalog["chiave_pubblica_base64"], validate=True)
    public = Ed25519PublicKey.from_public_bytes(key)
    envelope = verify_signature(catalog.pop("firma_catalogo"), public)
    if envelope != catalog_record(catalog):
        raise ValueError("Il contenuto del catalogo è cambiato")
    actual = list(current_records())
    claimed = [verify_signature(row, public) for row in catalog["file"]]
    if claimed != actual:
        raise ValueError("I file presenti e le voci degli ZIP differiscono dal catalogo firmato")
    has_history = (ROOT / ".git").exists()
    for item in catalog["versioni_precedenti"]:
        r = verify_signature(item, public)
        if has_history:
            path = PurePosixPath(r["percorso"])
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Percorso di revisione non valido")
            if digest(git("show", r["revisione"] + ":" + path.as_posix())) != r["sha256"]:
                raise ValueError("La versione storica è cambiata: " + r["percorso"])
    files = sum(r["tipo"] == "file" for r in claimed) + 1
    print(f"{files} file e {len(claimed) - files + 1} voci ZIP verificati. {LABEL}.")
    print("Impronta SHA-256 della chiave pubblica: " + digest(key))
    if catalog["versioni_precedenti"] and not has_history:
        print("Per confrontare anche i contenuti storici occorre il clone Git completo.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crea", action="store_true", help="Crea e verifica un nuovo catalogo di edizione")
    args = parser.parse_args()
    try:
        if args.crea:
            generate()
        verify()
    except (OSError, ValueError, KeyError, InvalidSignature, subprocess.CalledProcessError) as error:
        sys.exit("Verifica fallita. " + (str(error) or "Firma Ed25519 non valida."))
