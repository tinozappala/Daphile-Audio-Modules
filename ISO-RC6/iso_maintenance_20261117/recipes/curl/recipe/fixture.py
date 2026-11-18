"""Synthetic loopback-only HTTP, TLS, QUIC and DNS fixtures."""
import asyncio
import gzip
import http.client
import http.server
import json
import os
from pathlib import Path
import ssl
import struct
import subprocess
import sys
import tempfile
import threading

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded
from h2.errors import ErrorCodes

binary, c_client = (str(Path(x).resolve()) for x in sys.argv[1:3])
BODY = b'curl fixture\n'

class HTTP(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def respond(self, data, status=200, **headers):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k.replace('_', '-'), v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path == '/redirect':
            self.respond(b'', 302, Location='/cookie', Set_Cookie='fixture=ok; Path=/')
        elif self.path == '/cookie':
            self.respond(self.headers.get('Cookie', '').encode())
        elif self.path == '/gzip':
            self.respond(gzip.compress(BODY), Content_Encoding='gzip')
        else:
            self.respond(BODY)
    def do_PUT(self):
        self.respond(self.rfile.read(int(self.headers['Content-Length'])))
    def log_message(self, *_):
        pass

class Proxy(HTTP):
    def do_GET(self):
        assert self.path == f'http://fixture.test:{self.server.origin}/plain', self.path
        origin = http.client.HTTPConnection('127.0.0.1', self.server.origin, timeout=3)
        try:
            origin.request('GET', '/plain')
            reply = origin.getresponse()
            self.respond(reply.read(), reply.status, X_Fixture_Proxy='used')
        finally:
            origin.close()

class H3(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http = H3Connection(self._quic)
    def quic_event_received(self, event):
        for request in self.http.handle_event(event):
            if isinstance(request, HeadersReceived):
                self.http.send_headers(request.stream_id, [(b':status', b'200')])
                self.http.send_data(request.stream_id, b'h3 fixture\n', end_stream=True)

class DNS(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport
    def datagram_received(self, packet, peer):
        off = 12
        while packet[off]:
            off += packet[off] + 1
        off += 1
        qtype, qclass = struct.unpack_from('!HH', packet, off)
        question = packet[12:off+4]
        match = question[:-4] == b'\x07fixture\x04test\x00' and qtype == 1 and qclass == 1
        answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4) + bytes([127, 0, 0, 1]) if match else b''
        header = packet[:2] + struct.pack('!HHHHH', 0x8180, 1, bool(answer), 0, 0)
        self.transport.sendto(header + question + answer, peer)

async def h2_handler(reader, writer):
    conn = H2Connection(config=H2Configuration(client_side=False))
    conn.initiate_connection()
    writer.write(conn.data_to_send())
    bodies, paths = {}, {}
    try:
        while data := await reader.read(65536):
            for event in conn.receive_data(data):
                s = getattr(event, 'stream_id', None)
                if isinstance(event, RequestReceived):
                    paths[s] = dict(event.headers)[b':path']
                    bodies[s] = bytearray()
                elif isinstance(event, DataReceived):
                    bodies[s].extend(event.data)
                    conn.acknowledge_received_data(event.flow_controlled_length, s)
                elif isinstance(event, StreamEnded):
                    if paths[s] == b'/reset':
                        conn.reset_stream(s, ErrorCodes.INTERNAL_ERROR)
                    else:
                        result = bytes(bodies[s]) if paths[s] == b'/upload' else b'h2 fixture\n'
                        conn.send_headers(s, [(b':status', b'200'), (b'content-length', str(len(result)).encode())])
                        conn.send_data(s, result, end_stream=True)
            writer.write(conn.data_to_send())
            await writer.drain()
    finally:
        writer.close()

async def fixture(root):
    env = {k:v for k,v in os.environ.items() if k != 'LD_LIBRARY_PATH'}
    async def openssl(*args):
        await asyncio.to_thread(subprocess.run, ['openssl', *args], cwd=root, env=env,
                                check=True, timeout=12, capture_output=True)
    for name in ('good', 'wrong'):
        await openssl('req','-x509','-newkey','rsa:2048','-noenc','-days','1','-subj',f'/CN=curl fixture {name}',
                      '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign',
                      '-keyout',name+'.key','-out',name+'.crt')
    await openssl('req','-new','-newkey','rsa:2048','-noenc','-subj','/CN=localhost','-keyout','srv.key','-out','srv.csr')
    (root/'extensions').write_text('subjectAltName=IP:127.0.0.1,DNS:fixture.test\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\n')
    await openssl('x509','-req','-in','srv.csr','-CA','good.crt','-CAkey','good.key','-CAcreateserial','-days','1',
                  '-extfile','extensions','-out','srv.crt')
    def context(alpn):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(root/'srv.crt', root/'srv.key')
        ctx.set_alpn_protocols(alpn)
        return ctx
    h1 = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
    h1.socket = context(['http/1.1']).wrap_socket(h1.socket, server_side=True)
    proxy = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    plain = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
    proxy.origin = plain.server_port
    for srv in (h1, proxy, plain):
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    h2 = await asyncio.start_server(h2_handler, '127.0.0.1', 0, ssl=context(['h2']))
    q = QuicConfiguration(is_client=False, alpn_protocols=H3_ALPN)
    q.load_cert_chain(root/'srv.crt', root/'srv.key')
    h3 = await serve('127.0.0.1', 0, configuration=q, create_protocol=H3)
    udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(DNS, local_addr=('127.0.0.1', 0))
    good, wrong = (str(root/(x+'.crt')) for x in ('good','wrong'))
    url1 = f'https://127.0.0.1:{h1.server_port}'
    url2 = f'https://127.0.0.1:{h2.sockets[0].getsockname()[1]}'
    url3 = f'https://127.0.0.1:{h3._transport.get_extra_info("sockname")[1]}'
    results = {}
    async def curl(name, options, expected=None, exit_code=0):
        cmd = [binary, '-q', '--silent', '--show-error', '--max-time', '8', *options]
        reply = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=10)
        assert reply.returncode == exit_code, (name, reply.returncode, reply.stderr)
        if expected is not None:
            assert reply.stdout == expected, (name, reply.stdout, reply.stderr)
        results[name] = {'status':'PASS','exit':reply.returncode,'evidence':reply.stdout.decode(errors='replace'),
                         'diagnostic':reply.stderr.decode(errors='replace')}
    loopback = ['--noproxy','*']
    verified = [*loopback, '--cacert',good]
    upload = root/'upload'
    upload.write_bytes(b'upload fixture\n')
    try:
        await curl('HTTP1.1', [*verified,'--http1.1','-w','%{http_version}',url1+'/plain'], BODY+b'1.1')
        await curl('cookie_redirect', [*verified,'-L','-b','',url1+'/redirect'], b'fixture=ok')
        await curl('gzip', [*verified,'--compressed',url1+'/gzip'], BODY)
        await curl('HTTP1.1_upload', [*verified,'-T',str(upload),url1+'/upload'], upload.read_bytes())
        await curl('HTTP2', [*verified,'--http2','-w','%{http_version}',url2+'/plain'], b'h2 fixture\n2')
        await curl('HTTP2_upload', [*verified,'--http2','-T',str(upload),url2+'/upload'], upload.read_bytes())
        await curl('HTTP2_RST_STREAM', [*verified,'--http2',url2+'/reset'], b'', 92)
        await curl('HTTP3', [*verified,'--http3-only','-w','%{http_version}',url3+'/plain'], b'h3 fixture\n3')
        await curl('TLS_wrong_CA', [*loopback,'--cacert',wrong,url1+'/plain'], b'', 60)
        await curl('HTTP3_wrong_CA', [*loopback,'--cacert',wrong,'--http3-only',url3+'/plain'], b'', 60)
        await curl('proxy', ['--noproxy','','-x',f'http://127.0.0.1:{proxy.server_port}','-i',
                             f'http://fixture.test:{plain.server_port}/plain'])
        assert 'X-Fixture-Proxy: used' in results['proxy']['evidence']
        await curl('c-ares_DNS', [*verified,'--dns-servers',f'127.0.0.1:{udp.get_extra_info("sockname")[1]}',
                                 '-4',f'https://fixture.test:{h1.server_port}/plain'], BODY)
        process = await asyncio.to_thread(subprocess.run, [c_client,url2+'/plain',good], capture_output=True,
                                          check=True, text=True, timeout=10)
        results['libcurl_C_HTTP2'] = {'status':'PASS','evidence':process.stdout.strip()}
    finally:
        udp.close()
        h3.close()
        h2.close()
        await h2.wait_closed()
        for srv in (h1, proxy, plain):
            await asyncio.to_thread(srv.shutdown)
            srv.server_close()
    return results

with tempfile.TemporaryDirectory(prefix='curl-loopback-') as path:
    print(json.dumps(asyncio.run(fixture(Path(path))), indent=2))
