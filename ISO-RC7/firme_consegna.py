"""Sign or verify the files of a Daphile ISO delivery with Ed25519."""

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FORMAT = "Daphile firme ISO v1"
LABEL = "Tino Zappala, Milano - 2026"
CATALOG = "FIRME_CONSEGNA.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def encode(raw):
    return base64.b64encode(raw).decode("ascii")


def decode(value):
    return base64.b64decode(value, validate=True)


def message(kind, value):
    return canonical([FORMAT, kind, value])


def artifact(root, name):
    relative = PurePosixPath(name)
    if (relative.is_absolute() or relative.as_posix() != name or not relative.parts
            or ".." in relative.parts or "\\" in name or ":" in name):
        raise ValueError("Percorso non valido: " + name)
    path = root.joinpath(*relative.parts)
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("File non disponibile nella consegna: " + name)
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"percorso": name, "byte": path.stat().st_size, "sha256": sha}


def sign(directory, names):
    if not names or len(set(names)) != len(names) or CATALOG in names:
        raise ValueError("Elenco dei file da firmare non valido")
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    records = []
    for name in sorted(names):
        record = artifact(directory, name)
        records.append({**record, "ed25519": encode(private.sign(message("file", record)))})
    envelope = {"formato": FORMAT, "firma_nominativa": LABEL, "algoritmo": "Ed25519",
                "chiave_pubblica_base64": encode(public), "file": records}
    signed = {**envelope, "firma_catalogo": encode(private.sign(message("catalogo", envelope)))}
    with (directory / CATALOG).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(signed, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def verify(directory, selected=None):
    catalog = json.loads((directory / CATALOG).read_bytes())
    if (catalog["formato"], catalog["algoritmo"], catalog["firma_nominativa"]) != (FORMAT, "Ed25519", LABEL):
        raise ValueError("Formato o firma nominativa del catalogo diversa")
    key = decode(catalog["chiave_pubblica_base64"])
    public = Ed25519PublicKey.from_public_bytes(key)
    signature = decode(catalog.pop("firma_catalogo"))
    public.verify(signature, message("catalogo", catalog))
    names = set()
    bytes_verified = 0
    checked = 0
    for signed in catalog["file"]:
        record = dict(signed)
        public.verify(decode(record.pop("ed25519")), message("file", record))
        name = record["percorso"]
        if name in names:
            raise ValueError("File duplicato nel catalogo: " + name)
        names.add(name)
        if not selected or name in selected:
            if artifact(directory, name) != record:
                raise ValueError("Dimensione o SHA-256 differente: " + name)
            checked += 1
            bytes_verified += record["byte"]
    if not checked or selected and selected - names:
        raise ValueError("File richiesto assente dal catalogo firmato")
    return {"stato": "PASS", "file_verificati": checked, "byte_verificati": bytes_verified,
            "chiave_pubblica_sha256": hashlib.sha256(key).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cartella", default=Path(__file__).resolve().parent, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--crea", nargs="+", metavar="FILE")
    mode.add_argument("--solo", nargs="+", metavar="FILE")
    args = parser.parse_args()
    root = args.cartella.resolve()
    try:
        if args.crea:
            sign(root, args.crea)
        print(json.dumps(verify(root, set(args.solo) if args.solo else None)))
    except (OSError, ValueError, KeyError, InvalidSignature) as error:
        parser.exit(1, "Verifica fallita. " + (str(error) or "Firma Ed25519 non valida") + "\n")


if __name__ == "__main__":
    main()
