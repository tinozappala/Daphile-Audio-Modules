"""One absolute AutoTune budget, counted from entry including setup/recovery.

New sessions have a 1500 second operational window and 300 seconds reserved
inside the 1800 second total. Persisted monotonic anchors belong to one boot
and PID namespace. Reopening a record never starts a new elapsed-time clock.
Historical 1080 second records retain their original, shorter deadline.
"""
import copy
import math
import os
from pathlib import Path
import signal
import time

MAX_SECONDS=1800
WORK_SECONDS=1500
RECOVERY_SECONDS=300
LEGACY_MAX_SECONDS=1080
LEGACY_WORK_SECONDS=780
PROTOCOL='absolute-monotonic-boot-v1'
KEYS=('budget_protocol','budget_boot_id','budget_pid_namespace','calibration_started',
      'work_deadline','calibration_deadline','maximum_seconds','recovery_reserve_seconds',
      'calibration_monotonic_started','work_monotonic_deadline','calibration_monotonic_deadline')


class BudgetExpired(BaseException):pass
class HardDeadline(BudgetExpired):pass


def boot_id():
    value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not value:raise RuntimeError('Current Linux boot identity unavailable')
    return value


def pid_namespace():
    # A /proc mount belonging to another PID namespace cannot qualify the
    # numeric PID which a pidfd_open() in this namespace would resolve.
    proc_pid=int(Path('/proc/self/stat').read_text().split(' ',1)[0])
    value=os.readlink('/proc/self/ns/pid')
    if proc_pid!=os.getpid() or not value.startswith('pid:[') or not value.endswith(']'):
        raise RuntimeError('Process and /proc PID namespaces are inconsistent')
    return value


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def record(start=None):
    if start is not None:
        # Legacy callers supplied a historical wall-clock start. A missing
        # monotonic anchor must never grant them the newly authorized limit.
        if not finite(start) or start<0:raise ValueError('Finite historical start required')
        return dict(calibration_started=start,work_deadline=start+LEGACY_WORK_SECONDS,
            calibration_deadline=start+LEGACY_MAX_SECONDS,maximum_seconds=LEGACY_MAX_SECONDS,
            recovery_reserve_seconds=LEGACY_MAX_SECONDS-LEGACY_WORK_SECONDS)
    mono=time.monotonic();wall=time.time()
    return dict(budget_protocol=PROTOCOL,budget_boot_id=boot_id(),budget_pid_namespace=pid_namespace(),
        calibration_started=wall,work_deadline=wall+WORK_SECONDS,calibration_deadline=wall+MAX_SECONDS,
        maximum_seconds=MAX_SECONDS,recovery_reserve_seconds=RECOVERY_SECONDS,
        calibration_monotonic_started=mono,work_monotonic_deadline=mono+WORK_SECONDS,
        calibration_monotonic_deadline=mono+MAX_SECONDS)


def validate(value,*,current=True):
    if not isinstance(value,dict):raise ValueError('Explicit persisted time budget required')
    maximum=value.get('maximum_seconds');reserve=value.get('recovery_reserve_seconds',300)
    fields=('calibration_started','work_deadline','calibration_deadline')
    if any(not finite(value.get(key)) or value[key]<0 for key in fields):
        raise ValueError('Finite persisted wall-clock deadlines required')
    if type(maximum) is not int or maximum not in (LEGACY_MAX_SECONDS,MAX_SECONDS) or reserve!=300 or type(reserve) is not int:
        raise ValueError('Known total budget with its full recovery reserve required')
    if (not math.isclose(value['calibration_deadline']-value['calibration_started'],maximum,abs_tol=1e-6,rel_tol=0)
            or not math.isclose(value['calibration_deadline']-value['work_deadline'],reserve,abs_tol=1e-6,rel_tol=0)):
        raise ValueError('Inconsistent persisted budget deadlines')
    if value.get('budget_protocol') is None:
        if maximum!=LEGACY_MAX_SECONDS or any(key in value for key in KEYS if key.startswith('budget_') or 'monotonic' in key):
            raise ValueError('Unanchored records cannot acquire a longer time budget')
        return False
    if value.get('budget_protocol')!=PROTOCOL or maximum!=MAX_SECONDS:
        raise ValueError('Known monotonic total-budget protocol required')
    if any(key not in value for key in KEYS):raise ValueError('Complete original monotonic budget required')
    mono=('calibration_monotonic_started','work_monotonic_deadline','calibration_monotonic_deadline')
    if any(not finite(value.get(key)) or value[key]<0 for key in mono):
        raise ValueError('Finite persisted monotonic deadlines required')
    if (not math.isclose(value[mono[2]]-value[mono[0]],maximum,abs_tol=1e-6,rel_tol=0)
            or not math.isclose(value[mono[2]]-value[mono[1]],reserve,abs_tol=1e-6,rel_tol=0)):
        raise ValueError('Inconsistent persisted monotonic budget')
    if not isinstance(value.get('budget_boot_id'),str) or not value['budget_boot_id'] or not isinstance(value.get('budget_pid_namespace'),str) or not value['budget_pid_namespace']:
        raise ValueError('Budget boot and PID namespace identity required')
    if current and (value['budget_boot_id']!=boot_id() or value['budget_pid_namespace']!=pid_namespace()):
        raise BudgetExpired('Budget belongs to a different boot or PID namespace; no continuation')
    return True


def timing(value,now=None):
    maximum=value.get('maximum_seconds') if isinstance(value,dict) else None
    try:
        anchored=validate(value,current=False)
        if anchored:
            if value['budget_boot_id']!=boot_id() or value['budget_pid_namespace']!=pid_namespace():
                return dict(elapsed_seconds=None,remaining_seconds=0,maximum_seconds=maximum,
                            clock='different-boot-or-namespace',deadline_valid=False)
            end=value.get('calibration_finished_monotonic',time.monotonic())
            if not finite(end) or end<value['calibration_monotonic_started']:raise ValueError('Invalid elapsed clock')
            elapsed=end-value['calibration_monotonic_started']
            remaining=max(0,min(value['calibration_monotonic_deadline']-end,
                value['calibration_deadline']-(time.time() if now is None else now)))
            clock='monotonic-with-wall-upper-bound'
        else:
            wall=time.time() if now is None else now
            elapsed=max(0,wall-value['calibration_started'])
            remaining=max(0,value['calibration_deadline']-wall) if wall>=value['calibration_started'] else 0
            clock='historical-wall-clock'
        return dict(elapsed_seconds=round(elapsed,1),remaining_seconds=round(remaining,1),
                    maximum_seconds=maximum,clock=clock,deadline_valid=True)
    except (ValueError,TypeError,OSError,RuntimeError):
        return dict(elapsed_seconds=None,remaining_seconds=0,maximum_seconds=maximum,
                    clock='unverified',deadline_valid=False)


class Deadline:
    def __init__(self,value):
        self.record=copy.deepcopy(value);self.anchored=validate(self.record);self.hard=False;self.entered=False
        self.anchor=time.monotonic();wall=time.time()
        if self.anchored:
            self.mono_work=self.record['work_monotonic_deadline'];self.mono_end=self.record['calibration_monotonic_deadline']
        else:
            # Only legacy records can lack an original monotonic anchor. No
            # negative wall elapsed time or new 1800-second allowance is given.
            elapsed=wall-self.record['calibration_started']
            self.mono_work=self.anchor+(max(0,self.record['work_deadline']-wall) if elapsed>=0 else 0)
            self.mono_end=self.anchor+(max(0,self.record['calibration_deadline']-wall) if elapsed>=0 else 0)

    def remaining(self,hard=False):
        now=time.monotonic()
        if self.anchored and now<self.record['calibration_monotonic_started']:return 0
        wall=self.record['calibration_deadline' if hard else 'work_deadline']-time.time()
        mono=(self.mono_end if hard else self.mono_work)-now
        return max(0,min(wall,mono))

    def allows(self,seconds):
        if not finite(seconds) or seconds<0:raise ValueError('Finite nonnegative required time')
        return self.remaining(self.hard)>=seconds

    def require(self,seconds=1):
        if not self.allows(seconds):raise BudgetExpired('Operational time reserved for verification and full recovery')

    def alarm(self,*_):
        if self.hard:raise HardDeadline('Absolute automatic session deadline reached; incomplete recovery remains explicit')
        raise BudgetExpired('Operational deadline reached; independent recovery reserve remains')

    def interrupted(self,*_):
        raise BudgetExpired('Automatic worker interrupted; independent recovery remains active')

    def arm(self):
        remaining=self.remaining(self.hard)
        if remaining<=0:self.alarm()
        signal.setitimer(signal.ITIMER_REAL,max(.001,remaining))

    def recovery(self):
        self.hard=True;self.arm()

    def __enter__(self):
        if self.entered:raise RuntimeError('Deadline context already active')
        self.previous_timer=signal.getitimer(signal.ITIMER_REAL)
        if any(self.previous_timer):raise RuntimeError('Another process timer owns the deadline')
        self.handlers={sig:signal.getsignal(sig) for sig in (signal.SIGALRM,signal.SIGTERM)}
        try:
            signal.signal(signal.SIGALRM,self.alarm)
            signal.signal(signal.SIGTERM,self.interrupted)
            self.arm();self.entered=True;return self
        except BaseException:
            signal.setitimer(signal.ITIMER_REAL,0)
            for sig,handler in self.handlers.items():signal.signal(sig,handler)
            raise

    def __exit__(self,*_):
        signal.setitimer(signal.ITIMER_REAL,0)
        for sig,handler in self.handlers.items():signal.signal(sig,handler)
        self.entered=False


try:from core_policy import shortlist
except ImportError:from .core_policy import shortlist


def representative(entries,transport):
    """Retained legacy format selector; the V2 PCM workflow does not call it."""
    pcm=[e for e in entries if e['kind']=='PCM'];chosen=[]
    def add(e):
        if e and e not in chosen:chosen.append(e)
    for base in (44100,48000):
        family=[e for e in pcm if e['rate']%base==0 and e['bits']==24]
        add(min(family,key=lambda e:e['rate']) if family else None)
    add(max((e for e in pcm if e['bits']==32),key=lambda e:e['rate'],default=None))
    dsd=[e for e in entries if e['kind']=='DSD' and e.get('transport')==transport and e['status']!='unsupported']
    add(min(dsd,key=lambda e:e['rate']) if dsd else None)
    return chosen

__release_signature__ = 'Tino Zappala'
