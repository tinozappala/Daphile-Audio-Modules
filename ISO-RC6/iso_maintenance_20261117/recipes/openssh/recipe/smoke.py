import getpass, json, os, pathlib, socket, subprocess, tempfile, time
root=pathlib.Path('stage').resolve()
bin=root/'usr/bin'
helpers=root/'usr/lib64/misc'
def run(tool,*args,stdin=None,check=True):
 return subprocess.run([str(tool),*map(str,args)],input=stdin,capture_output=True,text=True,check=check,timeout=12)
with tempfile.TemporaryDirectory(prefix='daphile-ssh-test-', dir=pathlib.Path.cwd()) as t:
 p=pathlib.Path(t)
 key=p/'id_ed25519'; host=p/'host_key'
 for k in (key,host):run(bin/'ssh-keygen','-q','-t','ed25519','-N','','-f',k)
 message=p/'message';message.write_text('OpenSSH fixture\n')
 run(bin/'ssh-keygen','-Y','sign','-f',key,'-n','daphile-test',message)
 signers=p/'signers';signers.write_text('test '+key.with_suffix('.pub').read_text())
 run(bin/'ssh-keygen','-Y','verify','-f',signers,'-I','test','-n','daphile-test','-s',str(message)+'.sig',stdin=message.read_text())
 assert 'read' in run(helpers/'sftp-server','-Q','requests').stdout.splitlines()
 with socket.socket() as s:
  s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 config=p/'sshd_config'
 config.write_text(f'''ListenAddress 127.0.0.1
Port {port}
HostKey {host}
PidFile {p/'pid'}
AuthorizedKeysFile {key}.pub
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
PermitRootLogin no
AuthenticationMethods publickey
SshdSessionPath {helpers/'sshd-session'}
SshdAuthPath {helpers/'sshd-auth'}
Subsystem sftp {helpers/'sftp-server'}
''')
 known=p/'known_hosts';known.write_text(f'[127.0.0.1]:{port} '+host.with_suffix('.pub').read_text())
 args=['-F','/dev/null','-o',f'UserKnownHostsFile={known}','-o','GlobalKnownHostsFile=/dev/null',
       '-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','BatchMode=yes',
       '-o','ConnectTimeout=4','-i',str(key)]
 account=f'{getpass.getuser()}@127.0.0.1'
 result={'ssh_version':run(bin/'ssh','-V').stderr.strip(),'ed25519_sign_and_verify':'PASS','sftp_helper_requests':'PASS'}
 for flag in ('-t','-T'):
  parsed=run(bin/'sshd',flag,'-f',config,check=False)
  result['sshd_'+flag[1:]]={'exit':parsed.returncode,'stderr':parsed.stderr.replace(str(p),'$FIXTURE')}
  if flag=='-T' and parsed.returncode==0:
   cfg=dict(line.split(' ',1) for line in parsed.stdout.splitlines())
   if 'usepam' in cfg: assert cfg['usepam']=='yes'
   if 'sshdauthpath' in cfg: assert cfg['sshdauthpath']==str(helpers/'sshd-auth')
 if result['sshd_t']['exit']==0 and result['sshd_T']['exit']==0:
  with (p/'server.log').open('w+') as log:
   proc=subprocess.Popen([str(bin/'sshd'),'-D','-e','-f',str(config)],stdout=log,stderr=log)
   try:
    started=False
    for _ in range(25):
     if proc.poll() is not None: break
     try:
      with socket.create_connection(('127.0.0.1',port),timeout=0.15):pass
      started=True;break
     except OSError:time.sleep(0.1)
    if started:
     r=run(bin/'ssh',*args,'-p',port,account,'printf ssh-fixture',check=False)
     if r.returncode==0 and r.stdout=='ssh-fixture':
      result['SSH']='PASS'
      r=run(bin/'sftp',*args,'-P',port,'-b','-',account,stdin=f'put {message} {p/"upload"}\n',check=False)
      result['SFTP']='PASS' if r.returncode==0 and (p/'upload').read_bytes()==message.read_bytes() else {'exit':r.returncode,'stderr':r.stderr.replace(str(p),'$FIXTURE')}
      r=run(bin/'scp',*args,'-P',port,message,f'{account}:{p/"scp-upload"}',check=False)
      result['SCP']='PASS' if r.returncode==0 and (p/'scp-upload').read_bytes()==message.read_bytes() else {'exit':r.returncode,'stderr':r.stderr.replace(str(p),'$FIXTURE')}
     else:result['SSH']={'exit':r.returncode,'stderr':r.stderr.replace(str(p),'$FIXTURE')}
    else:result['SSH']='local server did not start under sandbox'
   finally:
    if proc.poll() is None:proc.terminate()
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    log.seek(0);result['server_diagnostic']=log.read().replace(str(p),'$FIXTURE')[-4000:]
 else:result['SSH']='not exercised: sshd configuration check failed'
 print(json.dumps(result,indent=2))
