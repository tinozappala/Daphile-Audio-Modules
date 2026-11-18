"""Buffer raw scheduler events privately; decode after the capture interval.

Kernel timestamps and loss counters remain authoritative. The size bound fails
the observation instead of dropping events or claiming partial coverage.
"""
import tempfile
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import collections
import heapq
import struct

EVENT_HEADER=re.compile(rb'\s(\d+)\.(\d+):\s+(?:sched:)?sched_\w+:\s')
CPU_CONTEXT=re.compile(rb'-\d+\s+(?:\(\s*(?:\d+|-+)\s*\)\s+)?\[(\d+)\]\s+(?:\S+\s+)?$')
INDEX=struct.Struct('!QQQI')  # ns, original ordinal, raw offset, raw length


def cpu_timestamp(line):
    stamp=EVENT_HEADER.search(line)
    if stamp is None or len(stamp[2])>9:return None
    # CPU provenance must be in the context BEFORE the event timestamp,
    # never in a task name or arbitrary text inside the event payload.
    context=CPU_CONTEXT.search(line[:stamp.start(1)])
    if context is None:return None
    return int(context[1]),int(stamp[1])*10**9+int(stamp[2].ljust(9,b'0'))


class TraceSpool:
    def __init__(self, limit=512 * 1024 * 1024):
        if type(limit) is not int or limit < 1:
            raise ValueError('Positive trace byte limit required')
        self.stream = tempfile.TemporaryFile(mode='w+b')
        self.limit = limit
        self.size = 0

    def append(self, chunk):
        if self.size + len(chunk) > self.limit:
            raise RuntimeError('Scheduler capture exceeds private byte budget; observation incomplete')
        if self.stream.write(chunk) != len(chunk):
            raise OSError('Incomplete scheduler capture write')
        self.size += len(chunk)

    def _replay_raw(self,counter):
        self.stream.seek(0)
        while True:
            line = self.stream.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                counter.unparsed+=1
                while line and not line.endswith(b'\n'):line=self.stream.readline(65537)
                continue
            if not line.endswith(b'\n'):
                if line.strip():
                    counter.unparsed += 1
                continue
            counter.feed(line.decode(errors='replace'))

    def replay(self,counter):
        """Validate CPU streams, then merge their index heads stably.

        trace_pipe is a live consumer of independent CPU buffers. Preserve the
        original spool and each CPU's order; only verified interleaving can be
        reconstructed. Invalid CPU clocks/headers remain a hard failure.
        """
        receipt=dict(protocol='verified-per-cpu-merge-v1',validated=False,mode='invalid',
            source_lines=0,event_records=0,cpu_ids=[],raw_global_backtracks=0,
            max_raw_backtrack_ns=0,per_cpu_clock_errors=0,header_errors=0,
            loss_markers=0,per_cpu_error_examples=[],max_open_index_files=16,
            index_bytes=0,raw_bytes_duplicated=0)
        handles=collections.OrderedDict();last={};positions={};previous=None
        with tempfile.TemporaryDirectory(prefix='dat-trace-index-') as temporary:
            def stream(cpu,mode):
                handle=handles.pop(cpu,None)
                if handle is None:
                    if len(handles)>=16:handles.popitem(last=False)[1].close()
                    handle=open(Path(temporary)/('cpu-'+str(cpu)+'.idx'),mode)
                handles[cpu]=handle
                return handle
            def close_handles():
                for handle in handles.values():handle.close()
                handles.clear()
            try:
                self.stream.seek(0)
                while True:
                    line=self.stream.readline(65537)
                    if not line:break
                    receipt['source_lines']+=1;ordinal=receipt['source_lines']
                    if len(line)>65536:
                        receipt['header_errors']+=1
                        while line and not line.endswith(b'\n'):line=self.stream.readline(65537)
                        continue
                    if not line.endswith(b'\n'):
                        if line.strip():receipt['header_errors']+=1
                        continue
                    if not line.strip() or line.lstrip().startswith(b'#'):continue
                    if b'LOST' in line and b'EVENT' in line:
                        receipt['loss_markers']+=1;continue
                    header=cpu_timestamp(line)
                    if header is None:
                        receipt['header_errors']+=1;continue
                    cpu,stamp=header
                    if stamp>=2**64:
                        receipt['header_errors']+=1;continue
                    if cpu in last and stamp<last[cpu]:
                        receipt['per_cpu_clock_errors']+=1
                        if len(receipt['per_cpu_error_examples'])<4:
                            receipt['per_cpu_error_examples'].append(dict(cpu=cpu,previous_stamp_ns=last[cpu],
                                new_stamp_ns=stamp,raw_ordinal=ordinal,payload=line[:384].decode(errors='replace')))
                    if previous is not None and stamp<previous:
                        receipt['raw_global_backtracks']+=1
                        receipt['max_raw_backtrack_ns']=max(receipt['max_raw_backtrack_ns'],previous-stamp)
                    last[cpu]=stamp;previous=stamp;receipt['event_records']+=1
                close_handles();receipt['cpu_ids']=sorted(last)
                receipt['validated']=bool(last) and not any(receipt[key] for key in
                    ('per_cpu_clock_errors','header_errors','loss_markers'))
                if not receipt['validated']:
                    self._replay_raw(counter)
                    counter.unparsed=max(counter.unparsed,receipt['header_errors'],1 if not last else 0)
                    counter.lost=max(counter.lost,receipt['loss_markers'])
                    counter.clock_errors=max(counter.clock_errors,receipt['per_cpu_clock_errors'])
                    return receipt
                receipt['mode']='stable-cpu-merge' if receipt['raw_global_backtracks'] else 'raw-order'
                if not receipt['raw_global_backtracks']:
                    self._replay_raw(counter)
                    return receipt
                # The common already-ordered case needs no index files.
                # Only a verified delivery inversion pays for indexed merge.
                self.stream.seek(0);ordinal=0;raw_offset=0
                while True:
                    line=self.stream.readline(65537);offset=raw_offset;raw_offset+=len(line)
                    if not line:break
                    ordinal+=1
                    if not line.strip() or line.lstrip().startswith(b'#'):continue
                    cpu,stamp=cpu_timestamp(line)
                    record=INDEX.pack(stamp,ordinal,offset,len(line))
                    if stream(cpu,'ab').write(record)!=len(record):raise OSError('Incomplete trace index write')
                    receipt['index_bytes']+=len(record)
                close_handles()
                heap=[]
                def next_head(cpu):
                    opened=cpu not in handles;handle=stream(cpu,'rb');position=positions.get(cpu,0)
                    if opened and position:handle.seek(position)
                    raw=handle.read(INDEX.size)
                    if not raw:return
                    if len(raw)!=INDEX.size:raise OSError('Incomplete trace index record')
                    stamp,ordinal,offset,length=INDEX.unpack(raw);positions[cpu]=position+INDEX.size
                    heapq.heappush(heap,(stamp,ordinal,cpu,offset,length))
                for cpu in receipt['cpu_ids']:next_head(cpu)
                merged=getattr(counter,'feed_merged',None)
                raw_position=None
                while heap:
                    stamp,ordinal,cpu,offset,length=heapq.heappop(heap)
                    if raw_position!=offset:self.stream.seek(offset)
                    raw=self.stream.read(length);raw_position=offset+length
                    if len(raw)!=length:raise OSError('Incomplete original trace record')
                    line=raw.decode(errors='replace')
                    if merged is None:counter.feed(line)
                    else:merged(line,stamp)
                    next_head(cpu)
                return receipt
            finally:close_handles()

    def close(self):
        self.stream.close()

    def save_private(self,root,name,metadata,limit=32*1024*1024,max_cases=3):
        """Preserve bounded failure evidence; never overwrite or prune a case."""
        if not re.fullmatch(r'dat-[0-9a-f]{32}',name):raise ValueError('Unique trace instance required')
        if type(limit) is not int or limit<1 or type(max_cases) is not int or max_cases<1:
            raise ValueError('Positive diagnostic limits required')
        root=Path(root)
        try:root.mkdir(mode=0o700)
        except FileExistsError:pass
        info=root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or root.is_symlink()
                or (os.name=='posix' and (info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700))):
            raise RuntimeError('Private diagnostic directory required')
        entries=[entry.lstat() for entry in root.iterdir()]
        if any(stat.S_ISLNK(entry.st_mode) for entry in entries):raise RuntimeError('Diagnostic links refused')
        if sum(stat.S_ISDIR(entry.st_mode) for entry in entries)>=max_cases:
            return dict(status='not-saved',reason='private-diagnostic-capacity',max_cases=max_cases,
                        source_bytes=self.size,raw_complete=False)
        case=root/name;case.mkdir(mode=0o700)
        raw=case/'events.trace';meta=case/'metadata.json'
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0)
        fd=os.open(raw,flags,0o600);digest=hashlib.sha256();saved=0
        with os.fdopen(fd,'wb') as output:
            self.stream.seek(0)
            while saved<limit:
                chunk=self.stream.read(min(1024*1024,limit-saved))
                if not chunk:break
                if output.write(chunk)!=len(chunk):raise OSError('Incomplete private diagnostic write')
                digest.update(chunk);saved+=len(chunk)
            output.flush();os.fsync(output.fileno())
        result=dict(status='saved',case_path=str(case),raw_path=str(raw),metadata_path=str(meta),
            source_bytes=self.size,saved_bytes=saved,byte_limit=limit,max_cases=max_cases,
            raw_complete=saved==self.size,sha256=digest.hexdigest())
        fd=os.open(meta,flags,0o600)
        with os.fdopen(fd,'w',encoding='utf8') as output:
            json.dump(dict(metadata,raw=result),output,sort_keys=True,separators=(',',':'),allow_nan=False)
            output.write('\n');output.flush();os.fsync(output.fileno())
        return result

__release_signature__ = 'Tino Zappala'
