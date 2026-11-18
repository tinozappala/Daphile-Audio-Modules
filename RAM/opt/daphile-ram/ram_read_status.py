"""Read-only current-track evidence. No controller, hashes or audio mutators."""
import ctypes
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import struct
import time
import urllib.parse

TMPFS_MAGIC=0x01021994
LIBC=ctypes.CDLL(None,use_errno=True)

def filesystem_type(fd):
    data=ctypes.create_string_buffer(256)
    if LIBC.fstatfs(fd,ctypes.byref(data))!=0:raise OSError(ctypes.get_errno(),'FSTATFS')
    return ctypes.c_long.from_buffer(data).value

def rooted_open(root_fd,path):
    # Linux openat2 RESOLVE_IN_ROOT also resolves absolute NAS/music symlinks
    # under the *actual LMS root*, rather than accidentally using the CGI root.
    if not path.startswith('/')or '\x00'in path:raise ValueError('NONLOCAL_TRACK')
    class OpenHow(ctypes.Structure):
        _fields_=[('flags',ctypes.c_uint64),('mode',ctypes.c_uint64),('resolve',ctypes.c_uint64)]
    how=OpenHow(os.O_RDONLY|os.O_CLOEXEC|os.O_NONBLOCK,0,0x10)
    fd=LIBC.syscall(ctypes.c_long(437),ctypes.c_int(root_fd),ctypes.c_char_p(os.fsencode(path)),ctypes.byref(how),ctypes.sizeof(how))
    if fd<0:raise OSError(ctypes.get_errno(),'TRACK_OPEN')
    return int(fd)

def local_track_path(url):
    if type(url)is not str:return None
    if url.startswith('/'):return url
    uri=urllib.parse.urlsplit(url)
    if uri.scheme not in('file','tmp')or uri.netloc not in('','localhost'):return None
    path=urllib.parse.unquote(uri.path)
    return path if path.startswith('/')and '\x00'not in path else None

def file_evidence(fd):
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):return dict(reading_ram=False,reason='TRACK_NOT_REGULAR')
    actual=filesystem_type(fd)==TMPFS_MAGIC
    return dict(reading_ram=actual,reason='CURRENT_TRACK_TMPFS'if actual else'CURRENT_TRACK_NOT_TMPFS',evidence='LMS_ROOT_OPENAT2_FSTATFS')

def track_evidence(root_fd,url):
    path=local_track_path(url)
    if path is None:return dict(reading_ram=False,reason='NONLOCAL_OR_UNKNOWN_TRACK')
    try:
        fd=rooted_open(root_fd,path)
        try:result=file_evidence(fd)
        finally:os.close(fd)
    except OSError:return dict(reading_ram=False,reason='TRACK_UNAVAILABLE')
    return result

def readonly_rpc(player,command,deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('READ_STATUS_DEADLINE')
    connection=http.client.HTTPConnection('127.0.0.1',9000,timeout=min(remaining,1.5))
    try:
        body=json.dumps(dict(id='ram-current-track',method='slim.request',params=[player,command]))
        connection.request('POST','/jsonrpc.js',body,{'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read(65537)
        if response.status!=200 or response.getheader('WWW-Authenticate')is not None or len(raw)>65536:raise ValueError('NATIVE_READ_UNAVAILABLE')
        value=json.loads(raw)
        if value.get('error')or type(value.get('result'))is not dict:raise ValueError('NATIVE_READ_UNAVAILABLE')
        return value['result']
    finally:connection.close()

def current_ram_status(player=None):
    deadline=time.monotonic()+2.5
    pid=int(Path('/run/mediaserver/mediaserver.pid').read_text())
    pidfd=os.pidfd_open(pid)
    try:
        status=Path('/proc',str(pid),'status').read_text()
        ids=next(row for row in status.splitlines()if row.startswith('Uid:')).split()[1:]
        if ids!=['1000']*4 or select.select([pidfd],[],[],0)[0]:raise ValueError('NATIVE_NOT_LIVE')
        if not player:
            connected=[row for row in readonly_rpc('',['players','0','100'],deadline).get('players_loop',[])if row.get('connected')in(1,'1',True)]
            if len(connected)!=1:return dict(reading_ram=False,reason='NO_UNAMBIGUOUS_PLAYER')
            player=connected[0]['playerid']
        if type(player)is not str or not re.fullmatch(r'[A-Za-z0-9:_.-]{1,128}',player):raise ValueError('PLAYER_UNAVAILABLE')
        value=readonly_rpc(player,['status','-','1','tags:u'],deadline)
        if value.get('player_connected')not in(1,'1',True):return dict(reading_ram=False,reason='PLAYER_NOT_CONNECTED')
        tracks=value.get('playlist_loop',[])
        if len(tracks)!=1 or value.get('mode')not in('play','pause'):return dict(reading_ram=False,reason='NO_CURRENT_PLAYING_OR_PAUSED_TRACK',player_id=player)
        root=os.open('/proc/'+str(pid)+'/root',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
        try:result=track_evidence(root,tracks[0].get('url'))
        finally:os.close(root)
        if select.select([pidfd],[],[],0)[0]:return dict(reading_ram=False,reason='NATIVE_EXITED')
        result.update(player_id=player,mode=value['mode'],poll_seconds=5)
        return result
    finally:os.close(pidfd)

def cgi_main():
    import sys
    method=os.environ.get('REQUEST_METHOD','GET')
    status=200
    if method not in('GET','HEAD'):
        status=405;value=dict(reading_ram=False,reason='READONLY_GET_REQUIRED')
    elif method=='HEAD':value=dict(reading_ram=False,reason='HEAD_NO_NATIVE_QUERY')
    else:
        def expired(*_):raise TimeoutError('READ_STATUS_DEADLINE')
        signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,3)
        try:
            query=os.environ.get('QUERY_STRING','')
            if len(query)>512:raise ValueError('QUERY_SIZE')
            values=urllib.parse.parse_qs(query,max_num_fields=4)
            value=current_ram_status(values.get('player',[None])[0])
        except(Exception,):value=dict(reading_ram=False,reason='NATIVE_READ_STATUS_UNAVAILABLE')
        finally:signal.setitimer(signal.ITIMER_REAL,0)
    body=(json.dumps(value,separators=(',',':'))+'\n').encode()
    headers=f'Status: {status}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n'
    sys.stdout.buffer.write(headers.encode())
    if method!='HEAD':sys.stdout.buffer.write(body)

if __name__=='__main__':cgi_main()
