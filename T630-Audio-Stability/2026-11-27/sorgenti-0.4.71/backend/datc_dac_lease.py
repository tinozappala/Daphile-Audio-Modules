# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Reuse RME 0.5.22's global write lock for native DAC transitions.
No MIDI calls, no hardware writes, no extra worker. Never unlink lock files.
"""
import contextlib,fcntl,json,os,stat,threading
from pathlib import Path
GLOBAL_LOCK=Path('/run/daphile-rme-peq.lock')
DEFAULT_STATE=Path('/mnt/hdd/daphile-autotune')
FINAL=frozenset(('committed','restored','rolled-back','no-change'))
class DacTransitionBusy(RuntimeError):
    """No permission to commence a cooperative DAC transition."""
class DacTransitionLease:
    def __init__(self,state=DEFAULT_STATE,lock_path=GLOBAL_LOCK):
        self.state=Path(state);self.path=Path(lock_path)
        self.local=threading.local()
    def _check_pending(self):
        if not self.state.is_absolute() or self.state.is_symlink() or not self.state.is_dir():
            raise DacTransitionBusy('Stato transazionale RME non verificato')
        for name in ('rme-peq','rme-audio','rme-control'):
            directory=self.state/name
            if directory.is_symlink():raise DacTransitionBusy('Registro RME non verificato')
            path=directory/'transaction.json'
            try:
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            except FileNotFoundError:continue
            except OSError as exc:raise DacTransitionBusy('Registro RME non verificato') from exc
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>4*1024*1024:
                    raise DacTransitionBusy('Registro RME non verificato')
                try:record=json.load(stream)
                except (ValueError,UnicodeError) as exc:raise DacTransitionBusy('Registro RME non verificato') from exc
                if not isinstance(record,dict) or not isinstance(record.get('status'),str) or record['status'] not in FINAL:
                    raise DacTransitionBusy('Recupero RME pendente; commutazione rinviata')
    def _verify(self,fd):
        a=os.fstat(fd);b=self.path.lstat()
        if (not stat.S_ISREG(a.st_mode) or not stat.S_ISREG(b.st_mode)
                or a.st_uid!=os.geteuid() or a.st_mode&0o077 or a.st_nlink!=1
                or (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino)):
            raise DacTransitionBusy('Lock RME non verificato')
    def assert_held(self):
        fd=getattr(self.local,'fd',None)
        if fd is None:raise DacTransitionBusy('Lock RME non detenuto dal chiamante')
        try:self._verify(fd)
        except OSError as exc:raise DacTransitionBusy('Lock RME non verificato') from exc
    @contextlib.contextmanager
    def hold(self):
        # Reentry only on the same thread, under NativeAudio's session RLock.
        if getattr(self.local,'fd',None) is not None:
            self._verify(self.local.fd)
            yield;return
        try:fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600)
        except OSError as exc:raise DacTransitionBusy('Lock RME non disponibile') from exc
        try:
            try:
                self._verify(fd)
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self._verify(fd)
                self._check_pending()
            except OSError as exc:raise DacTransitionBusy('Transazione DAC occupata o non verificata') from exc
            self.local.fd=fd
            try:yield
            finally:del self.local.fd
        finally:os.close(fd)
