#!/usr/bin/env python3
"""Synthetic localhost checks against the staged executable and modules."""
import gzip
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

stage = Path(sys.argv[1]).resolve()
binary = stage / 'usr/bin/lighttpd'
modules = stage / 'usr/lib64/lighttpd'
module_names = sorted(p.stem for p in modules.glob('mod_*.so'))
payload = b'lighttpd localhost fixture\n' * 200
env = dict(os.environ, LD_BIND_NOW='1')
class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.path.encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

with tempfile.TemporaryDirectory(prefix='lighttpd-smoke-') as temp:
    root = Path(temp)
    (root / 'static.txt').write_bytes(payload)
    (root / 'echo.pl').write_text(
        'print "Content-Type: text/plain\\r\\n\\r\\n";\n'
        'print join("|", @ENV{qw(HTTP_COOKIE PATH_INFO QUERY_STRING)});\n'
    )
    (root / 'echo.pl').chmod(0o755)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]

    with http.server.HTTPServer(('127.0.0.1', 0), Backend) as backend, (root / 'server.log').open('w+') as log:
        config = root / 'lighttpd.conf'
        settings = f'''
server.bind = "127.0.0.1"
server.port = {port}
server.document-root = "{root}"
server.compat-module-load = "disable"
mimetype.assign = (".txt" => "text/plain")
'''
        command = [str(binary), '-f', str(config), '-m', str(modules)]
        static_modules = ['mod_rewrite', 'mod_redirect', 'mod_alias', 'mod_access',
                          'mod_setenv', 'mod_indexfile', 'mod_fastcgi', 'mod_staticfile']
        # Shared modules can import symbols from modules loaded earlier (auth/authn).
        module_list = ', '.join(json.dumps(name) for name in static_modules + module_names)
        config.write_text(settings + f'server.modules = ({module_list})\n')
        subprocess.run(command + ['-tt'], check=True, env=env, timeout=5)
        config.write_text(settings + f'''
server.modules = ("mod_proxy", "mod_cgi", "mod_deflate", "mod_staticfile")
cgi.assign = (".pl" => "/usr/bin/perl")
deflate.allowed-encodings = ("gzip")
deflate.mimetypes = ("text/plain")
proxy.server = ("/backend" => (("host" => "127.0.0.1", "port" => {backend.server_port})))
''')
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        server = None
        try:
            server = subprocess.Popen(command + ['-D'], stdout=log, stderr=log, env=env)
            for _ in range(80):
                if server.poll() is not None:
                    raise RuntimeError(f'lighttpd exited with status {server.returncode}')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError('lighttpd did not start')
            def get(path, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
                try:
                    conn.request('GET', path, headers=headers or {})
                    reply = conn.getresponse()
                    body = reply.read()
                    assert reply.status == 200, (path, reply.status, body[:200])
                    return reply, body
                finally:
                    conn.close()

            assert get('/static.txt')[1] == payload
            assert get('/echo.pl/path/info?name=value', {'Cookie': 'fixture=ok'})[1] == b'fixture=ok|/path/info|name=value'
            assert get('/backend?fixture=1')[1] == b'/backend?fixture=1'
            response, compressed = get('/static.txt', {'Accept-Encoding': 'gzip'})
            assert response.getheader('Content-Encoding') == 'gzip'
            assert gzip.decompress(compressed) == payload
            print(json.dumps({'static': True, 'cgi_cookie_path_query': True, 'proxy': True, 'gzip': True,
                              'loaded_shared_modules': module_names}, indent=2))
        except Exception:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
            backend.shutdown()
            thread.join()
