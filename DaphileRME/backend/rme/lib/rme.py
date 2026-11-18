"""RME ADI-2 DAC read-only SysEx client. Exactly one allowed request; no SET API."""
import collections,contextlib,ctypes,fcntl,hashlib,os,shutil,signal,subprocess,tempfile,time
from pathlib import Path
from detection import alsa_cards,usb_devices

REQUEST_ALL = bytes.fromhex('F0 00 20 0D 71 03 09 F7')
TRANSPORT_LOCK=Path('/run/daphile-rme-read.lock')
RECEIVE_INVENTORIES={}

def wait_lock(lock,timeout=10):
    """Serialize MIDI access, with a bounded wait and no command on timeout."""
    end=time.monotonic()+timeout
    while True:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return
        except BlockingIOError:
            remaining=end-time.monotonic()
            if remaining<=0:raise RuntimeError('Canale RME occupato: attesa scaduta, nessun comando MIDI inviato. Attendi e aggiorna il DAC.') from None
            time.sleep(min(.05,remaining))

@contextlib.contextmanager
def transport():
    # Keep the previous read-lock pathname so installed readers also serialize.
    fd=os.open(TRANSPORT_LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        wait_lock(lock)
        yield

def frames(data):
    result=[];errors=[];current=None
    for value in data:
        if value>=0xf8: continue  # MIDI real-time messages may occur within SysEx.
        if value==0xf0:
            if current is not None: errors.append('Interrupted SysEx frame')
            current=bytearray([value])
        elif value==0xf7:
            if current is not None:
                current.append(value);result.append(bytes(current));current=None
        elif current is not None:
            if value>=0x80: errors.append('Unexpected MIDI status in SysEx');current=None
            else:
                current.append(value)
                if len(current)>4096:errors.append('Oversized SysEx frame');current=None
    if current is not None:errors.append('Incomplete final SysEx frame')
    return result,errors

def parameter(triple):
    a,b,c=triple;address=(a>>3)&15
    if address==12:
        return dict(address=address,index=((a&7)<<3)|(b>>4),raw=((b&15)<<7)|c)
    raw=((b&31)<<7)|c
    return dict(address=address,index=((a&7)<<2)|(b>>5),raw=raw,
                signed=raw-4096 if raw&2048 else raw,
                frequency_mantissa=((b&15)<<7)|c,frequency_factor=10 if b&16 else 1)

def decode(data):
    packets,errors=frames(data);counts=collections.Counter();parameters=[];lengths=[];names=[];status=[]
    for f in packets:
        if len(f)<7 or f[1:5]!=bytes.fromhex('00 20 0D 71'):
            errors.append('Unexpected device header');continue
        lengths.append(len(f));cmd=f[5];counts[hex(cmd)]+=1
        if len(f)>256:errors.append('Frame exceeds the limit in the 2023 specification')
        if cmd==1:
            if len(f[6:-1])%3:errors.append('Unaligned parameter payload');continue
            parameters.extend(parameter(f[i:i+3]) for i in range(6,len(f)-1,3))
        elif cmd==5:
            payload=f[6:-1]
            if not payload or payload[0] not in [*range(1,21),*range(33,42)]:errors.append('Unknown name identifier');continue
            text=bytes(payload[1:15]).split(b'\0',1)[0]
            if any(c<32 or c>126 for c in text):errors.append('Invalid hardware name');continue
            names.append(dict(identifier=payload[0],name=text.decode('ascii').strip(),raw=payload.hex()))
        elif cmd==7:
            payload=f[6:-1]
            if len(payload)<12:errors.append('Incomplete status message');continue
            status.append(dict(raw=payload.hex(),active_output_bits=payload[0]&3,device_revision_bits=(payload[0]>>2)&3,
                               active_line_bits=(payload[0]>>4)&3,phones34_active=bool(payload[0]&64),
                               io_status1=list(payload[1:5]),io_status2=list(payload[5:9]),
                               mode_bits=payload[9]&7 if payload[10]==1 else None,
                               dsd_outputs_bits=(payload[9]>>4)&7 if payload[10]==1 else None,
                               protocol_revision=payload[10],documented_status_schema=payload[10]==1))
        else:errors.append('Unexpected device command '+hex(cmd))
    return dict(message_counts=dict(counts),frame_lengths=lengths,parameters=parameters,parse_issues=errors,hardware_names=names,status_messages=status)

def eq_summary(parameters):
    # Channel settings only; preset flags have a documented table/example inconsistency.
    indices={1:{'type':3,'gain':4,'frequency':5,'q':6},2:{'gain':7,'frequency':8,'q':9},
             3:{'gain':10,'frequency':11,'q':12},4:{'gain':13,'frequency':14,'q':15},
             5:{'type':16,'gain':17,'frequency':18,'q':19}}
    result={}
    for name,left,right,channel in [('line',4,5,3),('phones',7,8,6),('iem',10,11,9)]:
        relevant=[p for p in parameters if p['address'] in [left,right,channel]]
        if not relevant:continue
        indexed={(p['address'],p['index']):p for p in relevant};out={}
        for side,address in [('left',left),('right',right)]:
            bands=[]
            for band,keys in indices.items():
                row={'band':band}
                for label,index in keys.items():
                    value=indexed.get((address,index))
                    if not value: row[label]=None;continue
                    row[label]=(value['signed']*.5 if label=='gain' else value['raw']/10 if label=='q'
                                else value['frequency_mantissa']*value['frequency_factor'] if label=='frequency'
                                else value['raw'])
                if any(v is not None for k,v in row.items() if k!='band'):bands.append(row)
            out[side]=bands
        enabled=indexed.get((left,2));dual=indexed.get((channel,11));volume=indexed.get((channel,12))
        right_enabled=indexed.get((right,2))
        out.update(eq_enabled=enabled['raw'] if enabled else None,eq_enabled_right=right_enabled['raw'] if right_enabled else None,dual_eq=dual['raw'] if dual else None,
                   volume_db=volume['signed']/10 if volume else None)
        result[name]=out
    return result

def select(cards,usb):
    matches=[]
    for c in cards:
        u=next((u for u in usb if u['path']==c.get('usb_path')),None)
        if u and (u.get('idVendor'),u.get('idProduct'))==('2a39','3fd3') and u.get('serial'):
            matches.append((c,u))
    if len(matches)!=1:raise RuntimeError('Exactly one identifiable RME ADI-2 DAC required')
    return matches[0]

def child_cleanup(parent_pid):
    # A cancelled GUI job must not leave an amidi receiver holding the port.
    def setup():
        libc=ctypes.CDLL(None,use_errno=True)
        if libc.prctl(1,signal.SIGTERM,0,0,0)!=0:raise OSError(ctypes.get_errno(),'PR_SET_PDEATHSIG failed')
        if os.getppid()!=parent_pid:os._exit(1)
    return setup

def read_request(request):
    if request==REQUEST_ALL:return
    if not isinstance(request,bytes) or len(request)!=8 or request[:6]!=bytes.fromhex('F0 00 20 0D 71 03') or request[-1]!=247 or not 10<=request[6]<=29:
        raise ValueError('Outside read-only MIDI request allowlist')

def _query_once(request_bytes=REQUEST_ALL):
    read_request(request_bytes)
    if os.geteuid()!=0:raise RuntimeError('Root required for the local MIDI read client')
    tool=shutil.which('amidi')
    if not tool:raise RuntimeError('amidi unavailable')
    with transport():
        card,usb=select(alsa_cards(),usb_devices());port='hw:'+str(card['card'])+',0,0'
        import rme_receive
        cache_key=(usb['serial'],request_bytes)
        try:
            data,inventory,metrics=rme_receive.exchange(port,request_bytes,decode,RECEIVE_INVENTORIES.get(cache_key))
        except rme_receive.Unavailable:
            pass  # Retain the former bounded receiver on systems without libasound.
        else:
            result=decode(data)
            if inventory is None:RECEIVE_INVENTORIES.pop(cache_key,None)
            else:RECEIVE_INVENTORIES[cache_key]=inventory
            valid=bool(result['message_counts']) and not result['parse_issues'] and inventory is not None
            result.update(status=('read-response-received' if result['parameters'] else 'status-only') if valid else 'inconclusive',
                read_only=True,request_hex=request_bytes.hex(' '),settings_write_commands_sent=0,
                bounded_receive_window=metrics['completion']=='full-window',received_bytes=len(data),
                reply_sha256=hashlib.sha256(data).hexdigest(),usb_serial=usb['serial'],port=port,
                current_eq=eq_summary(result['parameters']),transport='alsa-rawmidi-get',**metrics,
                limitations=['First read per DAC/request learns full inventory; later reads still require complete stable dumps',
                             'Firmware and undocumented status fields are not inferred'])
            return result
        with tempfile.TemporaryDirectory(prefix='daphile-rme-read-') as tmp:
            root=Path(tmp);request=root/'request.syx';reply=root/'reply.syx'
            request.write_bytes(request_bytes)
            # No caller-supplied payload, SysEx command, MIDI port or shell arguments.
            proc=subprocess.Popen([tool,'--port='+port,'--send='+str(request),'--receive='+str(reply)],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,preexec_fn=child_cleanup(os.getpid()))
            bounded=False
            try:out,err=proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                bounded=True;proc.terminate()
                try:out,err=proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:proc.kill();out,err=proc.communicate()
            data=reply.read_bytes() if reply.exists() else b''
    if len(data)>1024*1024:raise RuntimeError('Oversized MIDI response')
    result=decode(data);valid=bool(result['message_counts']) and not result['parse_issues']
    result.update(status=('read-response-received' if result['parameters'] else 'status-only') if valid else 'inconclusive',read_only=True,
                  request_hex=request_bytes.hex(' '),settings_write_commands_sent=0,
                  bounded_receive_window=bounded,amidi_exit=proc.returncode,received_bytes=len(data),
                  reply_sha256=hashlib.sha256(data).hexdigest(),usb_serial=usb['serial'],port=port,
                  current_eq=eq_summary(result['parameters']),
                  limitations=['Read-only MIDI client; writes require the independent typed transaction module',
                               'GUI PEQ SET uses separate rme_peq module; no stored preset or volume commands',
                               'Firmware versions and full IOStatus bit fields not decoded'])
    if err:result['transport_error']=err.decode(errors='replace')[-800:]
    if not data:result['reason']='No response: MIDI Control, firmware compatibility or MIDI port access remains to be checked'
    return result


def query(request=REQUEST_ALL):
    read_request(request)
    # Retry whole GET exchanges; never hide parse errors in an individual response.
    attempts=[]
    for number in range(3):
        result=_query_once(request)
        attempts.append({key:result.get(key) for key in ['status','received_bytes','reply_sha256','parse_issues']})
        if result['status']=='read-response-received':break
    result['transport_attempts']=attempts
    return result
