"""Verify local HTTPS with the staged Python SSL client, plus DNS and public TLS."""
import http.server
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import urllib.request

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Python HTTPS fixture')
    def log_message(self, *_):
        pass

def openssl(*args):
    subprocess.run(('openssl', *args), check=True, timeout=10, capture_output=True)

result = {}
with tempfile.TemporaryDirectory(prefix='python-https-') as tmp:
    root = Path(tmp)
    for name in ('good', 'wrong'):
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
                '-subj', f'/CN=Ephemeral Python test CA {name}', '-addext', 'basicConstraints=critical,CA:TRUE',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                '-keyout', str(root / (name + '.key')), '-out', str(root / (name + '.crt')))
    crt, key, csr = (str(root / ('server.' + ext)) for ext in ('crt', 'key', 'csr'))
    openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-subj', '/CN=localhost', '-keyout', key, '-out', csr)
    extensions = root / 'extensions'
    extensions.write_text('subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\n')
    openssl('x509', '-req', '-in', csr, '-CA', str(root/'good.crt'), '-CAkey', str(root/'good.key'),
            '-CAcreateserial', '-days', '1', '-extfile', str(extensions), '-out', crt)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(crt, key)
    with http.server.HTTPServer(('127.0.0.1', 0), Handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f'https://127.0.0.1:{server.server_port}/'
            for name in ('good', 'wrong'):
                client = ssl.create_default_context(cafile=str(root/(name+'.crt')))
                assert client.verify_mode == ssl.CERT_REQUIRED and client.check_hostname
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=client))
                try:
                    with opener.open(url, timeout=5) as response:
                        assert name == 'good' and response.read() == b'Python HTTPS fixture'
                        result['trusted_local_ca'] = 'PASS'
                except urllib.error.URLError as error:
                    assert name == 'wrong' and isinstance(error.reason, ssl.SSLCertVerificationError), error
                    result['wrong_local_ca_rejected'] = 'PASS'
        finally:
            server.shutdown()
            thread.join()
try:
    addresses = socket.getaddrinfo('www.python.org', 443, type=socket.SOCK_STREAM)
    assert addresses
    result['public_dns'] = 'PASS'
except socket.gaierror as e:
    result['public_dns'] = {'status': 'environment_error', 'message': str(e)}
try:
    with urllib.request.urlopen('https://www.python.org/', context=ssl.create_default_context(), timeout=8) as response:
        assert response.status == 200 and response.read(1024)
        result['public_https'] = 'PASS'
except OSError as e:
    result['public_https'] = {'status': 'environment_error', 'message': str(e)}
print(json.dumps(result, indent=2))
