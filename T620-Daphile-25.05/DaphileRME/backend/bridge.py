"""Same-origin Daphile CGI bridge to the owned loopback-only PEQ controller."""
import http.client,http.cookies,ipaddress,os,sys,urllib.parse
ALLOWED={'/eq-graph.js','/api/slots','/api/slots/save','/api/slots/delete','/','/app.js','/style.css','/api/session','/api/status','/api/profile','/api/presets','/api/login','/api/presets/save','/api/presets/delete','/api/presets/restore','/api/preview','/api/rew/preview','/api/compile','/api/audio/start','/api/audio/bypass','/api/audio/enable','/api/apply'}
ALLOWED.update({'/api/levels','/api/profile/prepare','/api/profile/state'})
RME_EXTENDED={'/studio.js','/api/rme/state','/api/rme/snapshot','/api/rme/memory','/api/rme/memory/read','/api/rme/memory/store',
 '/api/rme/setup/apply','/api/rme/setup/rename','/api/rme/context','/api/rme/control/apply','/api/rme/control/restore','/api/rme/control/recover','/api/rme/eq/apply','/api/rme/preset/apply'}
ALLOWED.update(RME_EXTENDED)

def forward(env,body=b'',port=8876):
 host=env.get('HTTP_HOST','');scheme='https' if env.get('HTTPS','').lower() in ('on','1') else 'http'
 try:
  parsed=urllib.parse.urlsplit('http://'+host);address=parsed.hostname
  if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or (parsed.port and not 1<=parsed.port<=65535):raise ValueError()
  if address!='localhost':
   ip=ipaddress.ip_address(address)
   if ip.version!=4 or not ip.is_private or ip.is_unspecified or ip.is_multicast:raise ValueError()
 except (ValueError,TypeError):return 403,{},b'Invalid host'
 if env.get('HTTP_ORIGIN') not in (None,'',scheme+'://'+host) or env.get('HTTP_SEC_FETCH_SITE')=='cross-site':return 403,{},b'Invalid origin'
 method=env.get('REQUEST_METHOD','GET');path=env.get('PATH_INFO','')
 if env.get('SCRIPT_NAME','/cgi-bin/PEQ')!='/cgi-bin/PEQ':return 400,{},b'Unexpected bridge path'
 if method not in ('GET','POST'):return 405,{},b'Method unsupported'
 if not path and method=='GET':return 303,{'Location':'/cgi-bin/PEQ/'},b''
 if path not in ALLOWED:return 404,{},b'Not found'
 if len(body)>131072:return 413,{},b'Body too large'
 headers={'Host':f'127.0.0.1:{port}'}
 cookies=http.cookies.SimpleCookie()
 try:cookies.load(env.get('HTTP_COOKIE',''))
 except http.cookies.CookieError:return 400,{},b'Invalid cookie'
 if 'peq_session' in cookies:headers['Cookie']='peq_session='+cookies['peq_session'].value
 if method=='POST':headers['Content-Type']=env.get('CONTENT_TYPE','')
 # RME transactions include full MIDI readback, bounded retries and recovery.
 # A short proxy timeout must not report failure while a SET still completes.
 timeout=180 if method=='POST' and (path=='/api/apply' or path.startswith('/api/audio/') or (path.startswith('/api/rme/') and path.endswith(('/apply','/restore','/recover','/store','/rename')))) else 30 if path in ('/api/rme/read','/api/rme/snapshot','/api/rme/memory/read') else 10
 connection=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
 try:
  connection.request(method,path,body if method=='POST' else None,headers);response=connection.getresponse();data=response.read(4*1024*1024+1)
  if len(data)>4*1024*1024:raise ValueError('Oversized controller response')
  selected={k:v for k,v in response.getheaders() if k.lower() in ('content-type','content-security-policy','x-content-type-options','referrer-policy')}
  cookie=response.getheader('Set-Cookie')
  if cookie:selected['Set-Cookie']=cookie.replace('Path=/','Path=/cgi-bin/PEQ/')+('; Secure' if scheme=='https' else '')
  return response.status,selected,data
 except (OSError,ValueError,http.client.HTTPException):return 503,{},b'PEQ controller unavailable'
 finally:connection.close()

def main():
 try:
  length=int(os.environ.get('CONTENT_LENGTH','0'))
  if not 0<=length<=131072:status,headers,data=413,{},b'Body too large'
  else:status,headers,data=forward(os.environ,sys.stdin.buffer.read(length))
 except (ValueError,OSError):status,headers,data=400,{},b'Invalid request'
 headers.update({'Cache-Control':'no-store','Content-Length':str(len(data))});headers.setdefault('Content-Type','text/plain; charset=utf-8')
 sys.stdout.buffer.write(('Status: '+str(status)+'\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n').encode()+data)
if __name__=='__main__':main()
