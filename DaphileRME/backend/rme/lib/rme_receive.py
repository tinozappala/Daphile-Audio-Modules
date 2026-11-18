"""Bounded ALSA GET receiver with drained input and measured complete dumps.

No arbitrary payload or SET is accepted. A new identity/request first learns
the complete inventory over the original four-second window. Later reads may
finish after that same inventory, complete SysEx and a stable configuration
window have all been observed. Status chatter does not extend that window.
"""
import ctypes
import errno
import time

MAX_BYTES=1024*1024
SND_RAWMIDI_NONBLOCK=0x0002  # ALSA include/rawmidi.h; 0x0001 is APPEND, not nonblocking.

class Unavailable(RuntimeError):pass

def validate_request(request):
    if request==bytes.fromhex('F0 00 20 0D 71 03 09 F7'):return
    if not isinstance(request,bytes) or len(request)!=8 or request[:6]!=bytes.fromhex('F0 00 20 0D 71 03') or request[-1]!=247 or not 10<=request[6]<=29:
        raise ValueError('Only bounded ADI-2 DAC GET requests are permitted')

class RawMidi:
    def __init__(self,port):
        try:self.lib=ctypes.CDLL('libasound.so.2')
        except OSError as exc:raise Unavailable('ALSA RawMIDI library unavailable') from exc
        signatures={
            'snd_rawmidi_open':([ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p),ctypes.c_char_p,ctypes.c_int],ctypes.c_int),
            'snd_rawmidi_close':([ctypes.c_void_p],ctypes.c_int),
            'snd_rawmidi_read':([ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t],ctypes.c_ssize_t),
            'snd_rawmidi_write':([ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t],ctypes.c_ssize_t),
        }
        for name,(args,result) in signatures.items():
            try:fn=getattr(self.lib,name)
            except AttributeError as exc:raise Unavailable('ALSA RawMIDI symbol unavailable') from exc
            fn.argtypes=args;fn.restype=result
        self.input=ctypes.c_void_p();self.output=ctypes.c_void_p()
        status=self.lib.snd_rawmidi_open(ctypes.byref(self.input),ctypes.byref(self.output),port.encode('ascii'),SND_RAWMIDI_NONBLOCK)
        if status<0:raise RuntimeError('Cannot open the selected ALSA MIDI port: '+str(status))
    def close(self):
        for handle in (self.input,self.output):
            if handle.value:self.lib.snd_rawmidi_close(handle);handle.value=None
    def begin(self,request):
        validate_request(request)
        # Start input before enqueueing the new GET, using the same ALSA handles.
        status=self.lib.snd_rawmidi_read(self.input,None,0)
        if status<0 and status!=-errno.EAGAIN:raise RuntimeError('Cannot activate ALSA MIDI input')
        # Drain by nonblocking reads, also on ALSA plugins without drop support.
        end=time.monotonic()+.2;discarded=0
        while True:
            pending=self.read()
            if not pending:break
            discarded+=len(pending)
            if discarded>MAX_BYTES or time.monotonic()>=end:raise RuntimeError('Stale MIDI input did not drain; no GET sent')
        sent=0;end=time.monotonic()+.5
        while sent<len(request):
            chunk=ctypes.create_string_buffer(request[sent:]);status=self.lib.snd_rawmidi_write(self.output,chunk,len(request)-sent)
            if status>0:sent+=status
            elif status not in (0,-errno.EAGAIN,-errno.EINTR):raise RuntimeError('MIDI GET transport failed: '+str(status))
            elif time.monotonic()>=end:raise RuntimeError('MIDI GET enqueue timed out')
            else:time.sleep(.005)
    def read(self):
        buffer=ctypes.create_string_buffer(4096);size=self.lib.snd_rawmidi_read(self.input,buffer,len(buffer))
        if size in (0,-errno.EAGAIN,-errno.EINTR):return b''
        if size<0:raise RuntimeError('MIDI GET receive failed: '+str(size))
        return buffer.raw[:size]

def profile(parsed):
    if parsed['parse_issues'] or not parsed['parameters']:return None,None
    indexed={}
    for p in parsed['parameters']:
        key=(p['address'],p['index'])
        if key in indexed and indexed[key]!=p['raw']:return None,None
        indexed[key]=p['raw']
    names={}
    for item in parsed.get('hardware_names',[]):
        key=item['identifier']
        if key in names and names[key]!=item['name']:return None,None
        names[key]=item['name']
    versions={s['protocol_revision'] for s in parsed.get('status_messages',[])}
    if len(versions)>1:return None,None
    inventory=(frozenset(indexed),frozenset(names),tuple(sorted(versions)))
    signature=(tuple(sorted(indexed.items())),tuple(sorted(names.items())))
    return inventory,signature

def collect(read,decode,expected=None,clock=time.monotonic,sleep=time.sleep):
    start=clock();last_change=start;signature=None;data=bytearray();inventory=None
    while clock()-start<4:
        chunk=read()
        if chunk:
            data.extend(chunk)
            if len(data)>MAX_BYTES:raise RuntimeError('Oversized MIDI response')
            parsed=decode(bytes(data));inventory,current=profile(parsed)
            if current!=signature:signature=current;last_change=clock()
        elapsed=clock()-start
        if expected is not None and inventory==expected and signature is not None and elapsed>=.2 and clock()-last_change>=.12:
            return bytes(data),inventory,{'completion':'qualified-inventory-quiescence','receive_seconds':elapsed}
        sleep(.005)
    parsed=decode(bytes(data));inventory,_=profile(parsed)
    return bytes(data),inventory,{'completion':'full-window','receive_seconds':clock()-start}

def exchange(port,request,decode,expected=None):
    validate_request(request);transport=RawMidi(port)
    try:
        transport.begin(request)
        return collect(transport.read,decode,expected)
    finally:transport.close()
