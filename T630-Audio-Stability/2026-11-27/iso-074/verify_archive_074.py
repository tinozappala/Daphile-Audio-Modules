"""Independent read-only verification of the candidate rootfs and archive."""
import hashlib
import io
import json
from pathlib import Path
import zipfile
import importlib.util
import sys
import tempfile
from unittest import mock
import build_074_linux as b


def digest(data, algorithm='sha256'):
    return hashlib.new(algorithm, data).hexdigest()


old=b.Pseudo(b.WORK/'base.pseudo')
new=b.Pseudo(b.WORK/'readback.pseudo')
prefix='DaphileCamillaDSP/'
oldname='DaphileCamillaDSP-0.4.73.zip'
newname='DaphileCamillaDSP-0.4.74.zip'
oldbytes=old.read(b.ARCHIVES[0]+oldname)
newbytes=new.read(b.ARCHIVES[0]+newname)
assert new.read(b.ARCHIVES[1]+newname)==newbytes
assert digest(newbytes)==digest((b.DEST/'DaphileCamillaDSP-0.4.74-candidate.zip').read_bytes())
with zipfile.ZipFile(io.BytesIO(oldbytes)) as before, zipfile.ZipFile(io.BytesIO(newbytes)) as after:
    assert before.testzip() is None and after.testzip() is None
    assert len(before.namelist())==len(after.namelist())==51
    assert set(before.namelist())==set(after.namelist())
    entries={n[len(prefix):]:after.read(n) for n in after.namelist() if n.startswith(prefix)}
    assert len(entries)==51
    differences={n[len(prefix):] for n in before.namelist() if before.read(n)!=after.read(n)}
    assert differences==set(b.CHANGED)
    for name,data in entries.items():
        assert new.read(b.PREFIX+name)==data, name
    assert digest(entries['backend/native_player.py'])==b.CANDIDATE_SHA
    assert b'0.4.74' in entries['install.xml']
    assert b'0.4.74' in entries['backend/peq/__init__.py']
    approval=json.loads(new.read(b.APPROVAL))['DaphileCamillaDSP']
    assert len(approval)==1 and approval[0]['version']=='0.4.74'
    record=approval[0]
    assert record['archive']==newname and record['sha256']==digest(newbytes)
    assert record['sha1']==digest(newbytes,'sha1')
    assert record['files']=={name:digest(data) for name,data in entries.items()}
    assert json.loads(new.read(b.RELEASE))['camilla']=='0.4.74'

# Also compare directly against the verified .72 image, bypassing any
# assumptions about which changes the intermediate .73 contains.
baseline72=b.Pseudo(Path('/root/darc_iso_073_empty_queue/base.pseudo'))
zip72=baseline72.read(b.ARCHIVES[0]+'DaphileCamillaDSP-0.4.72.zip')
assert digest(zip72)=='acab2f0797c8bc871a2454a411ba68a506a61fd986730889ed00962d0c968b52'
with zipfile.ZipFile(io.BytesIO(zip72)) as baseline:
    assert set(baseline.namelist())=={prefix+n for n in entries}
    direct_differences={n[len(prefix):] for n in baseline.namelist()
                        if baseline.read(n)!=entries[n[len(prefix):]]}
assert direct_differences==set(b.CHANGED)

# Exercise the parser from the read-back image with that same image's
# actual defaults and a simulated per-device file omitting USEDOP.
defaults=new.read('opt/spac/share/defaults.conf')
assert digest(defaults)=='d0e679cf12914ca44a5d260a7b67e6e699a5e67cf4fd16df227f8126edabc2ea'
with tempfile.TemporaryDirectory(prefix='verify-074-') as tmp:
    temp=Path(tmp)
    for name in ('native_player.py','native_route.py','player_router.py'):
        (temp/name).write_bytes(new.read(b.PREFIX+'backend/'+name))
    (temp/'defaults.conf').write_bytes(defaults)
    (temp/'volume.conf').write_text('SOFTVOL_MODE=bitperfect\n')
    sys.path.insert(0,str(temp))
    spec=importlib.util.spec_from_file_location('native_player_074_image',temp/'native_player.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    player=module.NativePlayer()
    def choose(*parts):
        path=Path(*parts)
        return temp/'defaults.conf' if path==Path('/opt/spac/share/defaults.conf') else path
    with mock.patch.object(module,'Path',side_effect=choose),mock.patch.object(player,'guard'):
        effective=player.configured_dsd_source_transport({'path':str(temp/'volume.conf')})
    assert effective=='native-converted-PCM'

result={'status':'PASS','version':'0.4.74','archive_sha256':digest(newbytes),
        'zip_members':len(entries),'installed_files_match_zip':len(entries),
        'only_changed_zip_members':sorted(differences),'all_approval_hashes':'PASS',
        'release_and_version_metadata':'PASS',
        'only_changed_members_directly_from_072':sorted(direct_differences),
        'image_defaults_sha256':digest(defaults),'missing_usedop_policy':effective}
print(json.dumps(result,indent=2),flush=True)
