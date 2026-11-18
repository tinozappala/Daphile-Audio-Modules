"""Idempotent, additive Daphile header initializer for the RAM menu button."""
from pathlib import Path
import os
import stat
import uuid

MARKER = b'data-daphile-ram-nav-script'
TAG = b"<script defer src='/cgi-bin/RamNavigation?v=20261116a1' data-daphile-ram-nav-script></script>\n"

def atomic_existing(path, data):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('RAM_NAV_HEADER_NOT_REGULAR')
    temporary = path.with_name('.' + path.name + '.ram-nav-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def initialize(cgi_dir=Path('/var/www/localhost/cgi-bin')):
    header = Path(cgi_dir) / 'pageheader'
    original = header.read_bytes()
    if TAG in original:
        if original.count(TAG) != 1:
            raise RuntimeError('RAM_NAV_DUPLICATE_OWN_TAG')
        return {'header_changed': False, 'own_tag_count': 1}
    if MARKER in original:
        raise RuntimeError('RAM_NAV_OTHER_OWN_TAG_PRESERVED')
    closing = b'</head>\n'
    if original.count(closing) != 1:
        raise RuntimeError('RAM_NAV_HEADER_ANCHOR_NOT_UNIQUE')
    atomic_existing(header, original.replace(closing, TAG + closing, 1))
    return {'header_changed': True, 'own_tag_count': 1}

if __name__ == '__main__':
    import json
    print(json.dumps(initialize()))
