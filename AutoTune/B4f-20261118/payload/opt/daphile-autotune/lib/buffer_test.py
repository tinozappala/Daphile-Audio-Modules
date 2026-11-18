"""Explicit PCM-only, exclusive-device tests. No live player restart/config edit."""
import math,os,re,subprocess,tempfile,time
from pathlib import Path
from detection import scan,command
from runtime import ERROR

def validate_target(discovery,current):
 d=discovery['dac']
 if d['status']!='verified-active' or not d.get('usb'):raise RuntimeError('Discovery must identify the active USB DAC')
 matches=[u for u in current['usb'] if all(u.get(k)==v for k,v in d['identity'].items())]
 cards=[c for c in current['alsa']['cards'] if c['id']==d['alsa_id'] and c['usb_path']==d['usb']['path']]
 if len(matches)!=1 or len(cards)!=1:raise RuntimeError('DAC changed or ambiguous')
 if any(p['active'] for p in cards[0]['pcms']):raise RuntimeError('DAC is in use; playback is never interrupted automatically')
 return cards[0],d

def run(discovery,seconds=5,buffers=(100,80,60,50,40,30,25,20,15,10),margin=1.5):
 if os.geteuid()!=0:raise RuntimeError('Audio benchmark requires root')
 if not 2<=seconds<=60 or not 1.2<=margin<=4:raise ValueError('Invalid test duration or safety margin')
 current=scan();card,d=validate_target(discovery,current);rates=card['declared_pcm_rates']
 # Only explicit ALSA PCM declarations. DSD and DoP are not synthesized or inferred.
 formats=[f for f in ['S16_LE','S24_3LE','S24_LE','S32_LE'] if f in card['declared_formats']]
 if not rates or not formats:raise RuntimeError('No explicit discrete playback capability matrix')
 rates=[r for r in rates if r in [44100,48000,88200,96000,176400,192000,352800,384000]]
 if not rates:raise RuntimeError('No supported test rates in discrete capability matrix')
 results=[]
 for ms in buffers:
  for rate in rates:
   for fmt in formats:
    validate_target(discovery,scan());before=command(['dmesg']);sample_bytes={'S16_LE':2,'S24_3LE':3,'S24_LE':4,'S32_LE':4}[fmt]
    args=['aplay','-D','hw:CARD='+d['alsa_id']+',DEV='+str(d['device']),'-t','raw','-f',fmt,'-c','2','-r',str(rate),'--buffer-time='+str(ms*1000),'--period-time='+str(ms*250),'-d',str(seconds),'-v']
    # Signed PCM zero is silence, no volume or DSP setting is touched.
    with tempfile.TemporaryFile() as data:
     remaining=seconds*rate*2*sample_bytes
     while remaining:n=min(remaining,1048576);data.write(bytes(n));remaining-=n
     data.seek(0)
     try:
      p=subprocess.run(args,stdin=data,capture_output=True,text=True,timeout=seconds+15);output=p.stdout+p.stderr;code=p.returncode
     except subprocess.TimeoutExpired:output='playback timeout';code=-1
    after=command(['dmesg']);known=before is not None and after is not None and after.startswith(before)
    errors=ERROR.findall(after[len(before):]) if known else None
    b=re.search(r'buffer_size\s*:\s*(\d+)',output);period=re.search(r'period_size\s*:\s*(\d+)',output)
    actual_ms=int(b[1])*1000/rate if b else None
    result=dict(requested_buffer_ms=ms,rate=rate,format=fmt,exit=code,actual_buffer_ms=actual_ms,actual_period_frames=int(period[1]) if period else None,kernel_errors=errors,aplay_errors=ERROR.findall(output),status='inconclusive')
    if known and b and period and code==0 and not errors and not result['aplay_errors']:result['status']='passed-short-test'
    elif code or errors or result['aplay_errors']:result['status']='failed'
    results.append(result)
  if any(x['status']!='passed-short-test' for x in results if x['requested_buffer_ms']==ms):break
 tested=[ms for ms in buffers if all(x['status']=='passed-short-test' for x in results if x['requested_buffer_ms']==ms) and len([x for x in results if x['requested_buffer_ms']==ms])==len(rates)*len(formats)]
 minimum=min(tested) if tested else None
 return dict(test='exclusive generated silent PCM',matrix=results,minimum_tested_stable_request_ms=minimum,recommended_request_ms=math.ceil(minimum*margin) if minimum is not None else None,margin=margin,applied_to_daphile=False,status='short-test evidence only',limitations=['Not a minimum proven for all workloads','DSD/DoP not tested','No long-duration stress certification','Squeezelite buffer configuration unchanged'])
