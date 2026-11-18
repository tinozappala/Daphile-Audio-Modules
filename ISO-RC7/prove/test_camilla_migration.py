"""Exercise shipped Camilla consolidation with synthetic existing DATA."""

import argparse
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import zipfile

NAME = "DaphileCamillaDSP"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(path, data, mode=0o644, *owner):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("b4f", "base-camilla", "consolidate", "repository"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    module = load(args.consolidate)
    repository = load(args.repository)
    with zipfile.ZipFile(args.b4f) as source:
        prefix = "AutoTune-B4f/compatibility/"
        old = json.loads(source.read(prefix + "APPROVED_ENTRY_0.4.47.json"))
        new = json.loads(source.read(prefix + "APPROVED_ENTRY_0.4.49.json"))
        candidate = source.read(prefix + new["archive"])
    with zipfile.ZipFile(args.base_camilla) as source:
        previous = {n: source.read(NAME + "/" + n) for n in old["files"]}
    with zipfile.ZipFile(io.BytesIO(candidate)) as source:
        expected = {n: source.read(NAME + "/" + n) for n in new["files"]}
    assert all(module.sha(previous[name])==sha for name,sha in old["files"].items())
    cases = {}
    with tempfile.TemporaryDirectory(prefix="rc7-camilla-") as directory:
        root = Path(directory)
        factory = root / "factory"
        publish(factory / "audio-host/approvals.json", json.dumps({NAME:[new]}).encode())
        publish(factory / "audio-host/bundles" / new["archive"], candidate)

        def layout(case):
            host, plugins = root / case / "host", root / case / "plugins"
            install = plugins / NAME
            for name, data in previous.items():
                publish(install / name, data)
            publish(host / "approvals.json", json.dumps({NAME:[old]}).encode())
            publish(host / "repository-cgi.json", b'{"base_url":"http://fixture.invalid/AudioPlugins"}')
            protected = host / "modules" / NAME / "data/presets.json"
            publish(protected, b'{"fixture": ["A", "B", "A"]}\n')
            return host, plugins, install, protected

        def apply(host, plugins):
            return module.consolidate(factory, host, plugins, publish, lambda base,url: {
                "repository.xml":repository.catalog(json.loads((base/"approvals.json").read_text()),url)
            })

        host, plugins, install, protected = layout("upgrade")
        original = protected.read_bytes()
        result = apply(host, plugins)
        assert result["version"]=="0.4.49" and result["changed_plugin_files"]==3
        assert all((install/n).read_bytes()==v for n,v in expected.items())
        assert protected.read_bytes()==original and b'version="0.4.49"' in (host/"repository.xml").read_bytes()
        assert json.loads((host/"approvals.json").read_text())[NAME]==[old,new]
        repeated = apply(host,plugins)
        assert repeated["changed_plugin_files"]==0 and repeated["catalog_updated"] is False
        cases["upgrade_rc6_and_repeat"]="PASS"

        host, plugins, install, protected = layout("custom")
        changed=install/"backend/native_player.py"
        data=changed.read_bytes()+b"\n# fixture customization\n"
        changed.write_bytes(data)
        try:
            apply(host,plugins)
        except RuntimeError as error:
            assert str(error)=="CAMILLA_CUSTOM_PLUGIN_SOURCE_PRESERVED"
        else:
            raise AssertionError("Custom Camilla source replaced")
        assert changed.read_bytes()==data and protected.read_bytes()==original
        cases["custom_source_preserved"]="PASS"

        host,plugins,install,protected=layout("newer")
        xml=install/"install.xml"
        version=xml.read_bytes().replace(b"<version>0.4.47</version>",b"<version>0.4.50</version>")
        xml.write_bytes(version)
        assert apply(host,plugins)["preserved_newer"] is True and xml.read_bytes()==version
        cases["newer_version_preserved"]="PASS"
    print(json.dumps({"status":"PASS","environment":"filesystem fixtures with synthetic DATA and real consolidation code","cases":cases}))


if __name__ == "__main__":
    main()
