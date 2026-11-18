"""Bound startup log processing; preserve the original large log once."""
import argparse
import os
from pathlib import Path
import stat

LIMIT = 8 * 1024 * 1024

def trim(logdir, archive, limit=LIMIT):
    logdir, archive = Path(logdir), Path(archive)
    changed = []
    for name in ['server.log', 'scanner.log', 'perfmon.log']:
        path = logdir / name
        try:fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:continue
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError('LOG_NOT_REGULAR')
            start = max(0, info.st_size - limit)
            stream.seek(start)
            tail = stream.read(limit)
        if start:
            # Discard only the first partial line of the bounded tail.
            line = tail.find(b'\n')
            if line >= 0:tail = tail[line + 1:]
        if info.st_size <= limit:
            continue
        archive.mkdir(mode=0o700, parents=True, exist_ok=True)
        if archive.is_symlink():
            raise RuntimeError('LOG_ARCHIVE_SYMLINK')
        legacy = archive / ('legacy-' + name)
        destination = legacy if not legacy.exists() else archive / ('previous-' + name)
        os.replace(path, destination)
        temporary = path.with_name('.' + name + '.bounded-start')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(tail)
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
                os.fchmod(stream.fileno(), info.st_mode & 0o777)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            if not path.exists():os.replace(destination, path)
            raise
        changed.append(name)
    return changed

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--logdir', default='/var/daphile/mediaserver/log')
    parser.add_argument('--archive', default='/mnt/hdd/system/daphile-ram/log-archive')
    args = parser.parse_args()
    print('Bounded startup logs:', len(trim(args.logdir, args.archive)))
