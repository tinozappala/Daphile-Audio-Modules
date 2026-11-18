"""Standalone RME API; only the original bounded, revision-checked operations."""
import hmac
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import threading
import urllib.parse

def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result

def private_token(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or not 1<=info.st_size<=128:raise RuntimeError('Private owner-only RME access token required')
        token=os.read(fd,129).decode('ascii').strip()
        if not token or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in token):raise RuntimeError('Invalid RME access token')
        return token
    finally:os.close(fd)


def make_server(release, state, port, transaction_state=None):
    sys.path.insert(0, str(Path(release)/'rme/lib'))
    import rme_peq as peq, rme_audio as audio
    import rme_control as control, rme_profiles as profiles, rme_memory as memory
    from rme_state import SnapshotCache
    from slots import Slots
    state = Path(state)
    transaction_state = Path(transaction_state) if transaction_state is not None else state
    token_path = state/'access.token'
    if not token_path.exists():
        import os
        fd = os.open(token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write(secrets.token_urlsafe(32))
    token = private_token(token_path)
    operations = threading.Lock()
    cache=SnapshotCache(lambda:control.snapshot(transaction_state),operations)
    def validate_preset(profile):
        return profiles.validate(profile)
    slots=Slots(state/'preset-slots.json',validate_preset)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def setup(self): super().setup(); self.connection.settimeout(15)
        def send(self, status, body, mime='application/json', cookie=None):
            if not isinstance(body, bytes): body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            for key, value in {'Content-Type':mime, 'Content-Length':str(len(body)), 'Cache-Control':'no-store',
                'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"}.items():
                self.send_header(key, value)
            if cookie: self.send_header('Set-Cookie', cookie)
            self.end_headers(); self.wfile.write(body)
        def allowed(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        def authorized(self):
            cookies = http.cookies.SimpleCookie()
            try: cookies.load(self.headers.get('Cookie', ''))
            except http.cookies.CookieError: return False
            item = cookies.get('peq_session')
            return bool(item and hmac.compare_digest(item.value, token))
        def do_GET(self):
            if not self.allowed(): return self.send(403, {'error':'Invalid Host'})
            path = urllib.parse.urlsplit(self.path).path
            if path == '/api/session':
                if self.headers.get('Sec-Fetch-Site')=='cross-site' or self.headers.get('Origin') not in (None,'','http://'+self.headers.get('Host','')):
                    return self.send(403,{'error':'Invalid Origin'})
                return self.send(200, {'authenticated':True,'access':'local-no-password'},cookie='peq_session='+token+'; HttpOnly; SameSite=Strict; Path=/')
            if path == '/api/status':
                return self.send(200, {'module':'DaphileRME', 'standalone':True}) if self.authorized() else self.send(401, {'error':'Authentication required'})
            if path in ('/api/rme/state','/api/rme/backup','/api/rme/memory'):
                if not self.authorized():return self.send(401,{'error':'Authentication required'})
                try:
                    current=cache.get()
                    if path=='/api/rme/state':return self.send(200,current)
                    if path=='/api/rme/memory':return self.send(200,{'limits':memory.LIMITS,'names':(current.get('state') or {}).get('hardware_names',[])})
                    return self.send(200,profiles.backup(slots.snapshot(),(current.get('state') or {}).get('identity','unknown')))
                except (ValueError,OSError,RuntimeError):return self.send(503,{'error':'Dati RME temporaneamente non disponibili'})
            if path == '/api/slots':
                if not self.authorized():return self.send(401, {'error':'Authentication required'})
                try:return self.send(200,slots.snapshot())
                except (ValueError,OSError):return self.send(503,{'error':'Preset slots unavailable'})
            files = {'/':('index.html','text/html; charset=utf-8'), '/app.js':('app.js','text/javascript; charset=utf-8'), '/studio.js':('studio.js','text/javascript; charset=utf-8'), '/eq-graph.js':('eq-graph.js','text/javascript; charset=utf-8'), '/style.css':('style.css','text/css; charset=utf-8')}
            if path not in files: return self.send(404, {'error':'Not found'})
            name, mime = files[path]; self.send(200, (Path(release)/'rme/ui'/name).read_bytes(), mime)
        def do_POST(self):
            if not self.allowed(): return self.send(403, {'error':'Invalid Host'})
            origin = self.headers.get('Origin')
            if (origin and origin != 'http://'+self.headers.get('Host', '')) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send(403, {'error':'Invalid Origin'})
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                return self.send(400, {'error':'Bounded JSON request required'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 65536: return self.send(413, {'error':'Invalid body size'})
                data = json.loads(self.rfile.read(length), object_pairs_hook=unique_object,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
                if not isinstance(data, dict): raise ValueError('Object required')
                path = urllib.parse.urlsplit(self.path).path
                if not self.authorized(): return self.send(401, {'error':'Authentication required'})
                if not operations.acquire(blocking=False): return self.send(409, {'error':'RME operation in progress'})
                try:
                    if path in ('/api/slots/save','/api/slots/delete'):
                        result=slots.mutate(data,delete=path.endswith('/delete'))
                    elif path == '/api/rme/read':
                        if data: raise ValueError('Unexpected read fields')
                        reply = peq.verified_read()
                        values, caps = audio.view(reply)
                        result = dict(current_eq=reply['current_eq'], revision=peq.revision(reply),
                                      capabilities={o:peq.eligibility(reply,o) for o in peq.OUTPUTS},
                                      current_audio=values, audio_capabilities=caps, settings_write_commands_sent=0)
                        cache.seed(control.from_reply(reply,transaction_state))
                    elif path=='/api/rme/snapshot':
                        if data:raise ValueError('Unexpected snapshot fields')
                        result=control.snapshot(transaction_state);cache.seed(result)
                    elif path in ('/api/rme/control/apply','/api/rme/eq/apply','/api/rme/preset/apply'):
                        if set(data)!={'patch','confirm'} or data['confirm'] is not True:raise ValueError('Explicit modification acknowledgement required')
                        kind='eq' if '/eq/' in path else 'preset' if '/preset/' in path else 'settings'
                        result=control.apply(transaction_state,data['patch'],kind)
                        if 'state' in result:cache.seed(result['state'])
                        else:cache.invalidate()
                    elif path=='/api/rme/control/restore':
                        if set(data)!={'output','expected','confirm'} or data['confirm'] is not True:raise ValueError('Explicit restore acknowledgement required')
                        result=control.restore(transaction_state,data['output'],data['expected'])
                        if 'state' in result:cache.seed(result['state'])
                        else:cache.invalidate()
                    elif path=='/api/rme/control/recover':
                        if set(data)!={'confirm'} or data['confirm'] is not True:raise ValueError('Explicit recovery acknowledgement required')
                        result=control.recover(transaction_state);cache.invalidate()
                    elif path=='/api/rme/context':
                        result=control.set_context(transaction_state,data);cache.invalidate()
                    elif path=='/api/rme/memory/read':
                        if set(data)!={'slot'}:raise ValueError('Select one hardware preset')
                        result=memory.read(data['slot'])
                    elif path=='/api/rme/backup/preview':
                        if set(data)!={'text'} or not isinstance(data['text'],str) or len(data['text'])>65536:raise ValueError('Bounded backup JSON text required')
                        document=json.loads(data['text'],object_pairs_hook=unique_object,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
                        result=profiles.import_preview(document)
                    elif path in ('/api/rme/peq/apply','/api/rme/audio/apply'):
                        if set(data) != {'patch','confirm'} or data['confirm'] is not True: raise ValueError('Explicit modification acknowledgement required')
                        result = (peq if '/peq/' in path else audio).apply(data['patch'],transaction_state)
                        cache.invalidate()
                    elif path in ('/api/rme/peq/restore','/api/rme/audio/restore'):
                        if set(data) != {'output','expected','confirm'} or data['confirm'] is not True: raise ValueError('Explicit restore acknowledgement required')
                        result = (peq if '/peq/' in path else audio).restore(data['output'], data['expected'], transaction_state)
                        cache.invalidate()
                    elif path in ('/api/rme/peq/recover','/api/rme/audio/recover'):
                        if set(data) != {'confirm'} or data['confirm'] is not True: raise ValueError('Explicit recovery acknowledgement required')
                        result = (peq if '/peq/' in path else audio).recover(transaction_state)
                        cache.invalidate()
                    else: return self.send(404, {'error':'Unknown endpoint'})
                    self.send(200, result)
                finally: operations.release()
            except control.RevisionConflict as exc:self.send(412,{'error':str(exc),'state':exc.current,'settings_write_commands_sent':0})
            except (ValueError, KeyError, TypeError, OverflowError) as exc: self.send(400, {'error':str(exc)})
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc: self.send(503, {'error':str(exc)})
    server = ThreadingHTTPServer(('127.0.0.1',port), Handler)
    server.daemon_threads = False  # Complete transactions before graceful exit.
    return server
