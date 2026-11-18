"""Portable current-host binding without requiring a particular motherboard."""
from pathlib import Path
import re
UUID=re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')

def _read(path):
    try:
        with Path(path).open('rb') as stream:raw=stream.read(257)
        return raw.decode().replace('\0','').strip() if len(raw)<=256 else None
    except (OSError,UnicodeError):return None

def info(reader=None):
    reader=reader or _read
    value=reader('/sys/class/dmi/id/product_uuid')
    if isinstance(value,str) and UUID.fullmatch(value):
        compact=value.replace('-','').lower()
        if len(set(compact))>1:
            return dict(value=value,source='dmi',persistent=True)
    for path in ('/etc/machine-id','/var/lib/dbus/machine-id'):
        value=reader(path)
        if isinstance(value,str) and re.fullmatch('[0-9a-fA-F]{32}',value) and len(set(value.lower()))>1:
            return dict(value='machine:'+value.lower(),source='machine-id',persistent=True)
    value=reader('/proc/sys/kernel/random/boot_id')
    if isinstance(value,str) and UUID.fullmatch(value):
        return dict(value='boot:'+value.lower(),source='boot-id',persistent=False)
    return dict(value=None,source='unavailable',persistent=False)

def current(reader=None):return info(reader)['value']
