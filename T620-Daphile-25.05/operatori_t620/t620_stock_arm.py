from pathlib import Path
import hashlib, http.client, json, os, subprocess, time

private=Path('/mnt/hdd/system/stock-2505251550-private')
baseline=private/(private/'baseline').read_text().strip()
stage=json.loads((private/'stage.json').read_text())
boot=Path('/boot')
firmware=stage['stock']
old=stage['previous']
assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'
assert 'daphile='+old in Path('/proc/cmdline').read_text().split()
assert not (private/'armed').exists()
assert 'daphile='+firmware+' ' in (boot/'grub/grub.cfg').read_text()
assert 'set default=9\nset fallback=6\n' in (boot/'grub/grub.cfg').read_text()
assert (private.parent/'conf/config.sys').read_bytes()==(baseline/'conf/config.sys').read_bytes()
def lms(player,args):
    connection=http.client.HTTPConnection('127.0.0.1',9000,timeout=8)
    try:
        connection.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':[player,args]}),{'Content-Type':'application/json'})
        return json.load(connection.getresponse())['result']
    finally:
        connection.close()
for player,saved in json.loads((baseline/'queue.json').read_text()).items():
    now=lms(player,['status',0,10000,'tags:u'])
    for field in ('playlist_tracks','playlist_cur_index','mode','power','mixer volume','playlist shuffle','playlist repeat'):
        assert saved.get(field)==now.get(field),'Playback or queue changed during preparation: refresh baseline'
    assert [x['url'] for x in saved.get('playlist_loop',[])]==[x['url'] for x in now.get('playlist_loop',[])], 'Queue changed during preparation: refresh baseline'
bootid=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
with (private/'armed').open('x') as f:
    json.dump({'boot_id':bootid,'timestamp':time.time(),'trial_entry':stage['trial_entry']},f)
    f.flush(); os.fsync(f.fileno())
def run(*args):
    return subprocess.run(args,check=True,timeout=15,capture_output=True).stdout.decode()
run('mount','-o','remount,rw','/boot')
try:
    path=str(boot/'grub/grubenv')
    assert 'next_entry=' in run('grub-editenv',path,'list').splitlines()
    wanted='next_entry='+str(stage['trial_entry'])
    run('grub-editenv',path,'set',wanted)
    assert wanted in run('grub-editenv',path,'list').splitlines()
    os.sync()
finally:
    run('mount','-o','remount,ro','/boot')
print('Stock trial armed; previous default intact; reboot scheduled',flush=True)
subprocess.Popen(['/bin/sh','-c','sleep 2; /usr/bin/openrc-shutdown -r now'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
