#!/usr/bin/env python3
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time

source = Path(sys.argv[1]).read_text()
guard, = re.findall(r'^  #if OPENSSL_VERSION_NUMBER < 0x40000000L[^\n]*(?:\\\n[^\n]*)*?\n(?=  if \(log_epoch_secs >= 1792728000\))', source, re.M)
cases = {'3.4': (0x30400000, True), '3.5.0': (0x30500000, False),
         '3.5.7': (0x30500070, False), '3.5.9': (0x30500090, False),
         '3.6': (0x30600000, True), '4.0': (0x40000000, False)}
for version, (value, expected) in cases.items():
    fragment = f'#define OPENSSL_VERSION_NUMBER {value:#x}L\n{guard}EOL_BRANCH\n#endif\n'
    pp = subprocess.run(['gcc', '-E', '-P', '-x', 'c', '-'], input=fragment,
                        text=True, capture_output=True, check=True)
    assert ('EOL_BRANCH' in pp.stdout) == expected, (version, pp.stdout)

def invoke(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, timeout=10)

def https(stage, root, expected_warning):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = root / 'lighttpd.conf'
    config.write_text(f'''
server.bind = "127.0.0.1"
server.port = {port}
server.document-root = "{root}/www"
server.modules = ("mod_openssl")
ssl.engine = "enable"
ssl.pemfile = "{root}/server.crt"
ssl.privkey = "{root}/server.key"
''')
    logpath = root / 'lighttpd.log'
    context = ssl.create_default_context(cafile=str(root / 'ca.crt'))
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    with logpath.open('w') as log:
        proc = subprocess.Popen([str(stage / 'usr/bin/lighttpd'), '-D', '-f', str(config),
                                 '-m', str(stage / 'usr/lib64/lighttpd')],
                                stdout=log, stderr=log, env=dict(os.environ, LD_BIND_NOW='1'))
        try:
            for _ in range(80):
                assert proc.poll() is None, logpath.read_text()
                connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=3, context=context)
                try:
                    connection.request('GET', '/probe.txt')
                    result = connection.getresponse()
                    assert result.status == 200
                    assert result.read() == b'HTTPS localhost fixture\n'
                    break
                except ConnectionRefusedError:
                    time.sleep(0.05)
                finally:
                    connection.close()
            else:
                raise RuntimeError('HTTPS listener did not start')
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    warned = 'openssl library version is outdated and has reached end-of-life' in logpath.read_text()
    assert warned == expected_warning, logpath.read_text()
    return {'https_verified': True, 'eol_warning': warned}

with tempfile.TemporaryDirectory(prefix='lighttpd-r2-https-') as path:
    root = Path(path)
    (root / 'www').mkdir()
    (root / 'www/probe.txt').write_bytes(b'HTTPS localhost fixture\n')
    invoke('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
           '-subj', '/CN=Ephemeral lighttpd test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
           '-keyout', str(root / 'ca.key'), '-out', str(root / 'ca.crt'))
    invoke('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-noenc', '-subj', '/CN=localhost',
           '-keyout', str(root / 'server.key'), '-out', str(root / 'server.csr'))
    (root / 'extensions').write_text('subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n')
    invoke('openssl', 'x509', '-req', '-in', str(root / 'server.csr'), '-CA', str(root / 'ca.crt'),
           '-CAkey', str(root / 'ca.key'), '-CAcreateserial', '-days', '1',
           '-extfile', str(root / 'extensions'), '-out', str(root / 'server.crt'))
    results = {'preprocessor_eol_branch': {k: expected for k, (_, expected) in cases.items()},
               'r2': https(Path(sys.argv[2]).resolve(), root, False)}
    if len(sys.argv) > 3:
        results['r1_control'] = https(Path(sys.argv[3]).resolve(), root, True)
    print(json.dumps(results, indent=2))
