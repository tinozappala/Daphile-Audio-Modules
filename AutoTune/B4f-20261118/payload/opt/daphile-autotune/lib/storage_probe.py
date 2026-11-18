"""Bounded read-only direct I/O during an instrumented audio observation.

The known-good boot rootfs is read without the page cache. No writes, cache
eviction or disk power commands are used. Unsupported direct I/O refuses the
trial instead of substituting a cached benchmark.
"""
import hashlib
import math
import mmap
import os
from pathlib import Path
import re
import stat
import threading
import time

BLOCK=65536


def summary(latencies,elapsed,errors):
    if errors or not latencies or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<=0:
        raise RuntimeError('Direct storage observation missing or failed')
    if any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in latencies):
        raise RuntimeError('Invalid direct storage latency')
    if len(latencies)<elapsed*5:raise RuntimeError('Direct storage observation incomplete')
    return dict(status='observed-direct-read',samples=len(latencies),elapsed_seconds=elapsed,
                bytes_read=len(latencies)*BLOCK,maximum_read_ms=max(latencies)*1000,
                mean_read_ms=sum(latencies)*1000/len(latencies),errors=0,content_verified=True)


def compare(before,after):
    for row in (before,after):
        if row.get('status')!='observed-direct-read' or row.get('errors')!=0 or row.get('content_verified') is not True:
            raise RuntimeError('Comparable direct storage observations required')
        for key in ('maximum_read_ms','mean_read_ms'):
            value=row.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:
                raise RuntimeError('Finite direct storage latency required')
    if any(after[k]>before[k] for k in ('maximum_read_ms','mean_read_ms')):
        raise RuntimeError('Direct storage read latency regressed')


class Probe:
    def __init__(self):
        firmware=Path('/version').read_text().strip()
        if not re.fullmatch(r'fw[0-9]{10}',firmware):raise RuntimeError('Unknown running firmware')
        root=Path('/boot')/firmware
        if root.is_symlink() or not (root/'ok').is_file():raise RuntimeError('Known-good firmware required')
        path=root/'rootfs'
        if path.is_symlink() or not hasattr(os,'O_DIRECT'):raise RuntimeError('Direct regular firmware reads unavailable')
        self.fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_DIRECT)
        self.buffer=None;self.thread=None;self.stop=threading.Event();self.latencies=[];self.errors=[]
        try:
            info=os.fstat(self.fd);self.identity=(info.st_dev,info.st_ino,info.st_size)
            if not stat.S_ISREG(info.st_mode) or info.st_size<16*1024*1024:
                raise RuntimeError('Bounded regular firmware image required')
            self.buffer=mmap.mmap(-1,BLOCK)
            self.hashes=[]
            for index in range(256):
                if os.preadv(self.fd,[self.buffer],index*BLOCK)!=BLOCK:raise RuntimeError('Incomplete direct read')
                self.hashes.append(hashlib.sha256(self.buffer).digest())
        except BaseException:
            self.close();raise

    def run(self):
        try:
            index=0
            while not self.stop.is_set():
                info=os.fstat(self.fd)
                if (info.st_dev,info.st_ino,info.st_size)!=self.identity:raise RuntimeError('Firmware read identity changed')
                # Deterministic permutation, identical for both observations.
                block=(index*73)%256;started=time.monotonic()
                count=os.preadv(self.fd,[self.buffer],block*BLOCK)
                latency=time.monotonic()-started
                if count!=BLOCK or hashlib.sha256(self.buffer).digest()!=self.hashes[block]:
                    raise RuntimeError('Direct read content changed or incomplete')
                self.latencies.append(latency);index+=1
                if self.stop.wait(.05):break
        except BaseException as error:self.errors.append(type(error).__name__)

    def observe(self,observer,seconds):
        started=time.monotonic();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
        try:
            result=observer(seconds)
        finally:
            self.stop.set();self.thread.join(timeout=10)
            if self.thread.is_alive():raise RuntimeError('Direct storage reader cleanup unconfirmed')
        result['storage_io']=summary(self.latencies,time.monotonic()-started,self.errors)
        return result

    def close(self):
        self.stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
            if self.thread.is_alive():raise RuntimeError('Direct storage reader still active')
        if self.buffer is not None:self.buffer.close();self.buffer=None
        if self.fd is not None:os.close(self.fd);self.fd=None


def trial(desired,seconds=30,state=None):
    import engine,measure
    from storage_policy import plan
    from core_policy import measured_gain
    first=None
    def observe(duration):
        nonlocal first
        probe=Probe()
        try:result=probe.observe(measure.observe,duration)
        finally:probe.close()
        if first is None:first=result['storage_io']
        else:compare(first,result['storage_io'])
        return result
    result=engine.trial(lambda hardware:plan(hardware,desired),'storage-power-review',seconds,
                        state=engine.STATE if state is None else state,commit=False,observer=observe,restore_position=False)
    benchmark=result.get('benchmark',{})
    result['measured_benefit']=(result['status']=='trial-passed-restored' and
                              measured_gain(benchmark['before'],benchmark['after']))
    result['persistent_changes']=False
    return result

__release_signature__ = 'Tino Zappala'
