"""Differential PCM guard fixtures and interleaved CPU benchmark, stdlib only.

Only temporary regular files are opened. No live /proc, audio, RPC, service,
plugin state or production release is read or changed by the loaded methods.
"""
import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import tempfile
import threading
import time
import types

sys.dont_write_bytecode=True


class RemapProc(ast.NodeTransformer):
    def __init__(self,root):self.root=str(root)
    def visit_Constant(self,node):
        value=node.value
        if isinstance(value,str) and (value=='/proc' or value.startswith('/proc/')):
            return ast.copy_location(ast.Constant(self.root+value[5:]),node)
        if isinstance(value,bytes) and (value==b'/proc' or value.startswith(b'/proc/')):
            return ast.copy_location(ast.Constant(os.fsencode(self.root)+value[5:]),node)
        return node


def method(tree,owner,name):
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name==owner)
    return next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==name)


def load_parser(original):
    model=ast.parse((original/'peq/model.py').read_text())
    nodes=[node for node in model.body if
        isinstance(node,ast.ClassDef) and node.name=='ValidationError' or
        isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='RATES' for target in node.targets)]
    route=ast.parse((original/'peq/routing.py').read_text())
    nodes.append(next(node for node in route.body if isinstance(node,ast.FunctionDef) and node.name=='parse_hw_params'))
    namespace=dict(re=re)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-original-parser','exec'),namespace)
    package=types.ModuleType('peq');package.__path__=[]
    routing=types.ModuleType('peq.routing');routing.parse_hw_params=namespace['parse_hw_params']
    sys.modules['peq']=package;sys.modules['peq.routing']=routing


def load_guard(source,root):
    tree=ast.parse((source/'native_player.py').read_text())
    nodes=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id.startswith('_GUARD_') for target in node.targets):nodes.append(node)
        elif isinstance(node,ast.FunctionDef) and node.name=='_read_guard_bytes':nodes.append(node)
    nodes.append(method(tree,'NativePlayer','fast_loopback_params'))
    fixture_os=types.SimpleNamespace(open=os.open,read=os.read,close=os.close,
        O_RDONLY=os.O_RDONLY|getattr(os,'O_BINARY',0),O_CLOEXEC=getattr(os,'O_CLOEXEC',0))
    namespace=dict(os=fixture_os,re=re,Path=Path)
    module=ast.fix_missing_locations(RemapProc(root).visit(ast.Module(body=nodes,type_ignores=[])))
    exec(compile(module,str(source/'native_player.py'),'exec'),namespace)
    return namespace


def load_pipeline_guard(original):
    tree=ast.parse((original/'native_audio.py').read_text())
    namespace={}
    exec(compile(ast.Module(body=[method(tree,'NativeAudio','guard_pcm')],type_ignores=[]),'actual-original-pipeline-guard','exec'),namespace)
    return namespace['guard_pcm']


class Files:
    def __init__(self,root):
        self.root=Path(root);self.paths=dict(card=self.root/'asound/card0/id',status=self.root/'asound/card0/pcm0p/sub6/status',
            hw=self.root/'asound/card0/pcm0p/sub6/hw_params',stat=self.root/'123/stat')
        self.target=dict(mac='fixture-player');self.player=types.SimpleNamespace(loopback_owner=('fixture-player',123,'999'))
    @staticmethod
    def stat(start='999',comm='fixture player'):
        return '123 ('+comm+') '+' '.join(['S']+['0']*18+[start]+['0']*12)+'\n'
    def reset(self,changes=None,missing=()):
        values=dict(card=b'Loopback\n',status=b'state: RUNNING\nowner_pid   : 123\n',
            hw=b'access: RW_INTERLEAVED\nformat: S32_LE\nchannels: 2\nrate: 44100 (44100/1)\n',stat=self.stat().encode())
        values.update(changes or {})
        for key,path in self.paths.items():
            path.parent.mkdir(parents=True,exist_ok=True)
            if key in missing:path.unlink(missing_ok=True)
            else:path.write_bytes(values[key] if isinstance(values[key],bytes) else values[key].encode())


class ReaderProbe:
    def __init__(self,base,short=False,fail=False):
        self.base=base;self.short=short;self.fail=fail;self.opened=[];self.closed=[];self.reads=0
        self.O_RDONLY=base.O_RDONLY;self.O_CLOEXEC=base.O_CLOEXEC
    def open(self,*args):
        fd=self.base.open(*args);self.opened.append(fd);return fd
    def read(self,fd,size):
        self.reads+=1
        if self.fail:raise OSError('injected read failure')
        return self.base.read(fd,min(size,7) if self.short else size)
    def close(self,fd):self.closed.append(fd);return self.base.close(fd)


def run_checks(files,old,new,pipeline_guard):
    checks={};expected=dict(rate=44100,format='S32_LE',channels=2)
    cases=[('pcm-s32',{},(),expected)]
    for fmt,normalized in (('S16_LE','S16_LE'),('S24_LE','S24_4_RJ_LE'),('S24_3LE','S24_3_LE'),('S24_3_LE','S24_3_LE')):
        cases.append(('pcm-'+fmt,dict(hw='format: '+fmt+'\nchannels: 2\nrate: 44100\n'),(),dict(expected,format=normalized)))
    for rate in (96000,192000):cases.append(('rate-'+str(rate),dict(hw='format: S32_LE\nchannels: 2\nrate: '+str(rate)+'\n'),(),dict(expected,rate=rate)))
    cases.extend([
        ('parentheses-in-comm',dict(stat=Files.stat(comm='player (a) b')),(),expected),
        ('rational-rate-suffix',dict(hw='format: S32_LE\nchannels: 2\nrate: 44100 (44100/1)\n'),(),expected),
        ('closed-hardware',dict(hw='closed\n'),(),None),
        ('prepared',dict(status='state: PREPARED\nowner_pid: 123\n'),(),None),
        ('paused',dict(status='state: PAUSED\nowner_pid: 123\n'),(),None),
        ('xrun',dict(status='state: XRUN\nowner_pid: 123\n'),(),None),
        ('running-substring-refused',dict(status='state: NOT_RUNNING\nowner_pid: 123\n'),(),None),
        ('different-owner',dict(status='state: RUNNING\nowner_pid: 124\n'),(),None),
        ('missing-owner',dict(status='state: RUNNING\n'),(),None),
        ('pid-reused',dict(stat=Files.stat(start='1000')),(),None),
        ('malformed-stat',dict(stat='bad stat'),(),None),
        ('incomplete-hardware',dict(hw='format: S32_LE\nrate: 44100\n'),(),None),
        ('unsupported-rate',dict(hw='format: S32_LE\nchannels: 2\nrate: 12345\n'),(),None),
        ('mono',dict(hw='format: S32_LE\nchannels: 1\nrate: 44100\n'),(),None),
        ('native-dsd-container',dict(hw='format: DSD_U32_LE\nchannels: 2\nrate: 176400\n'),(),None),
        ('unsupported-float-container',dict(hw='format: FLOAT_LE\nchannels: 2\nrate: 44100\n'),(),None),
        ('card-replaced',dict(card='AnotherCard\n'),(),None),
        ('empty-card-id',dict(card=''),(),None),
        ('missing-card',{},('card',),None),('missing-status',{},('status',),None),
        ('missing-process',{},('stat',),None),('missing-hardware',{},('hw',),None),
        ('invalid-text-hardware',dict(hw=b'format: S32_LE\nchannels: 2\nrate: \xff\n'),(),None)])
    assert len(cases)==30
    for name,changes,missing,wanted in cases:
        files.reset(changes,missing)
        a=old['fast_loopback_params'](files.player,files.target)
        b=new['fast_loopback_params'](files.player,files.target)
        checks[name]=a==b==wanted
    files.reset()
    unknown=types.SimpleNamespace()
    checks['unknown-session-refused']=old['fast_loopback_params'](unknown,files.target) is None and new['fast_loopback_params'](unknown,files.target) is None
    checks['different-target-refused']=old['fast_loopback_params'](files.player,dict(mac='other')) is None and new['fast_loopback_params'](files.player,dict(mac='other')) is None
    base=new['os']
    try:
        probe=ReaderProbe(base);new['os']=probe
        checks['four-fresh-opens-all-closed']=new['fast_loopback_params'](files.player,files.target)==expected and len(probe.opened)==4 and probe.opened==probe.closed
        probe=ReaderProbe(base,short=True);new['os']=probe
        checks['partial-reads-continue-until-real-eof']=new['fast_loopback_params'](files.player,files.target)==expected and probe.reads>8 and probe.opened==probe.closed
        files.reset(dict(hw='format: S32_LE\nchannels: 2\nrate: 44100\n'+('x'*5000)))
        checks['oversized-observation-fails-closed']=new['fast_loopback_params'](files.player,files.target) is None
        files.reset();probe=ReaderProbe(base,fail=True);new['os']=probe
        checks['read-error-closes-descriptor']=new['fast_loopback_params'](files.player,files.target) is None and len(probe.opened)==1 and probe.opened==probe.closed
    finally:new['os']=base
    files.reset();first=new['fast_loopback_params'](files.player,files.target)
    files.paths['stat'].unlink();files.paths['stat'].write_text(Files.stat(start='1000'))
    checks['next-poll-reopens-replaced-process-file']=first==expected and new['fast_loopback_params'](files.player,files.target) is None
    guard_cases=[('stable',{},(),0),('rate-changed',dict(hw='format: S32_LE\nchannels: 2\nrate: 96000\n'),(),1),
        ('format-changed',dict(hw='format: S16_LE\nchannels: 2\nrate: 44100\n'),(),1),
        ('writer-changed',dict(status='state: RUNNING\nowner_pid: 124\n'),(),1),
        ('birth-changed',dict(stat=Files.stat(start='1000')),(),1),('device-removed',{},('card',),1),
        ('dsd-format',dict(hw='format: DSD_U32_LE\nchannels: 2\nrate: 176400\n'),(),1),
        ('closed-idempotent',dict(hw='closed\n'),(),1)]
    for name,changes,missing,stops in guard_cases:
        files.reset(changes,missing);results=[]
        for namespace in (old,new):
            events=[];journals=[];stopped=[]
            audio=types.SimpleNamespace(lock=threading.RLock(),info=dict(mode='pcm-active'),target=files.target,current=dict(expected),
                player=types.SimpleNamespace(fast_loopback_params=lambda target,namespace=namespace:namespace['fast_loopback_params'](files.player,target)),
                event=lambda name:events.append(name),stop_pipeline=lambda:stopped.append(True),journal=lambda phase:journals.append(phase))
            pipeline_guard(audio);pipeline_guard(audio)
            results.append((audio.info,len(stopped),events,journals))
        checks['pipeline-'+name]=results[0]==results[1] and results[1][1]==stops and results[1][0]['mode']==('waiting-pcm' if stops else 'pcm-active')
    assert len(checks)==45
    files.reset()
    return checks


def benchmark(files,old,new,iterations,rounds):
    functions=dict(old=old['fast_loopback_params'],new=new['fast_loopback_params'])
    for function in functions.values():
        for _ in range(100):function(files.player,files.target)
    rows=[]
    for turn in range(rounds):
        order=('old','new') if turn%2==0 else ('new','old')
        for name in order:
            digest=hashlib.sha256();function=functions[name]
            wall=time.perf_counter_ns();cpu=time.process_time_ns()
            for _ in range(iterations):
                result=function(files.player,files.target)
                digest.update(json.dumps(result,sort_keys=True,separators=(',',':')).encode('ascii'))
            rows.append(dict(round=turn+1,variant=name,iterations=iterations,
                cpu_ns=time.process_time_ns()-cpu,wall_ns=time.perf_counter_ns()-wall,output_sha256=digest.hexdigest()))
    medians={name:statistics.median(row['cpu_ns']/iterations for row in rows if row['variant']==name) for name in functions}
    gain=100*(1-medians['new']/medians['old'])
    return dict(kind='regular-file-fixture-only',iterations_per_round=iterations,rounds=rounds,
        output_checksums_equal=len({row['output_sha256'] for row in rows})==1,
        median_cpu_ns_per_call=medians,median_cpu_reduction_percent=gain,rows=rows,
        qualifier='No live procfs/audio or whole-worker CPU qualification; JSON/checksum overhead included equally')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=Path,default=Path(__file__).parent/'original')
    parser.add_argument('--working',type=Path,default=Path(__file__).parent/'working')
    parser.add_argument('--iterations',type=int,default=1000);parser.add_argument('--rounds',type=int,default=6)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.iterations<=100000 or not 2<=args.rounds<=20:parser.error('iterations 1..100000 and rounds 2..20 required')
    load_parser(args.original)
    with tempfile.TemporaryDirectory(prefix='camilla-guard-fixture-') as directory:
        files=Files(Path(directory)/'proc');files.reset()
        old=load_guard(args.original,files.root);new=load_guard(args.working,files.root)
        checks=run_checks(files,old,new,load_pipeline_guard(args.original))
        result=dict(status='PASS' if all(checks.values()) else 'FAIL',scope='local fixtures only; production untouched',
            platform=platform.platform(),python=sys.version,checks=checks)
        if all(checks.values()):
            result['benchmark']=benchmark(files,old,new,args.iterations,args.rounds)
            if not result['benchmark']['output_checksums_equal']:result['status']='FAIL'
        encoded=json.dumps(result,indent=2,allow_nan=False)+'\n'
        if args.output:args.output.write_text(encoded,encoding='utf8')
        print(encoded,end='')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
