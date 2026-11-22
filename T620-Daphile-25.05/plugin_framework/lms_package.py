"""Build native LMS ZIPs/catalog; never alter LMS's registry or preferences."""
import hashlib
import io
from pathlib import Path
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

NAMES = frozenset({'DaphileRME', 'DaphileCamillaDSP'})


def package(source, name):
    """Return deterministic native ZIP and metadata for our own namespaces only."""
    if name not in NAMES:
        raise ValueError('Namespace is not owned by this project')
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Regular source directory required')
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in plugin source')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Non-regular plugin source')
        relative = path.relative_to(source).as_posix()
        if any('\\' in part or ':' in part for part in path.relative_to(source).parts):
            raise ValueError('Unsafe member name')
        files[relative] = path.read_bytes()
    if len(files) > 1000 or sum(map(len, files.values())) > 200 * 1024 * 1024:
        raise ValueError('Plugin source too large')
    manifest = ET.fromstring(files['install.xml'])
    if manifest.tag != 'extension' or manifest.findtext('module') != f'Plugins::{name}::Plugin':
        raise ValueError('Manifest namespace mismatch')
    if manifest.findtext('defaultState') not in ('disabled','enabled') or manifest.find('enforce') is not None:
        raise ValueError('Native optional state required; enforced plugin refused')
    version = manifest.findtext('version', '')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Numeric three-component version required')
    target = manifest.find('targetApplication')
    if target is None or target.findtext('id') != 'SlimServer':
        raise ValueError('Native LMS target required')
    low, high = target.findtext('minVersion'), target.findtext('maxVersion')
    if not low or not high or high == '*':
        raise ValueError('Explicit tested LMS version range required')
    if 'Plugin.pm' not in files or 'strings.txt' not in files:
        raise ValueError('Native module and translations required')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, data in files.items():
            member = zipfile.ZipInfo(f'{name}/{relative}', date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = (0o100644 << 16)
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, data)
    data = buffer.getvalue()
    # SHA-1 is the existing LMS transport contract, not our provenance proof.
    metadata = dict(name=name, version=version, minTarget=low, maxTarget=high,
                    sha1=hashlib.sha1(data).hexdigest(), sha256=hashlib.sha256(data).hexdigest(),
                    bytes=len(data), files={p:hashlib.sha256(d).hexdigest() for p,d in files.items()})
    return data, metadata


def catalog(entries, base_url):
    """Generate an additional repository; caller supplies its actual serving URL."""
    url = urlsplit(base_url)
    if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('HTTP(S) repository base URL without credentials required')
    root = ET.Element('extensions')
    plugins = ET.SubElement(root, 'plugins')
    seen = set()
    for entry in entries:
        name = entry['name']
        if name not in NAMES or name in seen:
            raise ValueError('Duplicate or foreign namespace')
        seen.add(name)
        if not re.fullmatch('[0-9a-f]{40}', entry['sha1']):
            raise ValueError('Native SHA-1 required')
        plugin = ET.SubElement(plugins, 'plugin', name=name, version=entry['version'],
                               target='unix', minTarget=entry['minTarget'], maxTarget=entry['maxTarget'])
        ET.SubElement(plugin, 'title', lang='EN').text = name
        ET.SubElement(plugin, 'desc', lang='IT').text = 'Accesso preliminare ai pannelli locali Daphile; DSP non attivato automaticamente.'
        ET.SubElement(plugin, 'creator').text = 'Daphile custom project'
        ET.SubElement(plugin, 'category').text = 'hardware'
        ET.SubElement(plugin, 'url').text = base_url.rstrip('/') + f'/{name}-{entry["version"]}.zip'
        ET.SubElement(plugin, 'sha').text = entry['sha1']
    ET.SubElement(ET.SubElement(root, 'details'), 'title', lang='EN').text = 'Daphile optional audio modules (development)'
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
