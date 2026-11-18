import hashlib,json,math,os,pathlib,struct,subprocess,tempfile,wave
root=pathlib.Path('stage').resolve()
ff=root/'usr/bin/ffmpeg';probe=root/'usr/bin/ffprobe'
def run(program,*args):
 return subprocess.run([str(program),*map(str,args)],check=True,capture_output=True,timeout=24).stdout
def ffmpeg(*args):
 return run(ff,'-nostdin','-hide_banner','-loglevel','error','-y',*args)
out={}
with tempfile.TemporaryDirectory(prefix='ffmpeg-pcm-') as d:
 d=pathlib.Path(d)
 for rate in (44100,48000,96000):
  for bits in (16,24,32):
   width=bits//8
   frames=rate//10
   cap=2**(bits-1)-1
   samples=b''.join(int(cap*math.sin(i*.17+phase)*.7).to_bytes(width,'little',signed=True)
                    for i in range(frames) for phase in (0,.4))
   pcm=d/f'pcm-{rate}-{bits}.wav'
   with wave.open(str(pcm),'wb') as f:
    f.setparams((2,width,rate,0,'NONE',''))
    f.writeframes(samples)
   derived=d/'roundtrip.wav'
   codec=f'pcm_s{bits}le';fmt=f's{bits}le'
   ffmpeg('-i',pcm,'-c:a',codec,derived)
   result=ffmpeg('-i',derived,'-f',fmt,'-c:a',codec,'pipe:1')
   assert result==samples,(rate,bits)
   info=json.loads(run(probe,'-v','error','-show_streams','-of','json',derived))['streams'][0]
   assert int(info['sample_rate'])==rate and info['channels']==2 and info['codec_name']==codec
   item={'wav_sha256':hashlib.sha256(result).hexdigest(),'bytes':len(result)}
   if bits<=24:
    flac=d/'roundtrip.flac'
    ffmpeg('-i',pcm,'-c:a','flac','-sample_fmt','s32' if bits==24 else 's16',flac)
    actual=ffmpeg('-i',flac,'-f',fmt,'-c:a',codec,'pipe:1')
    assert actual==samples, (rate,bits,len(actual),len(samples))
    start=rate*3//100;end=rate*5//100
    seek=ffmpeg('-i',flac,'-ss','0.03','-t','0.02','-f',fmt,'-c:a',codec,'pipe:1')
    assert seek==samples[start*2*width:end*2*width],(rate,bits,len(seek))
    item['flac_sha256']=hashlib.sha256(actual).hexdigest();item['seek']='exact bytes 30–50 ms'
   out[f'{rate}Hz_{bits}bit_stereo']=item
 for file,encoder,decoder in [('test.m4a','aac','aac'),('test.ogg','libvorbis','vorbis'),('test.wav','adpcm_ima_wav','adpcm_ima_wav')]:
  f=d/file
  ffmpeg('-i',d/'pcm-48000-16.wav','-c:a',encoder,f)
  data=ffmpeg('-i',f,'-f','s16le','-c:a','pcm_s16le','pipe:1')
  info=json.loads(run(probe,'-v','error','-show_streams','-of','json',f))['streams'][0]
  assert info['codec_name']==decoder and int(info['sample_rate'])==48000 and info['channels']==2
  assert len(data)%4==0 and abs(len(data)//4-4800)<=1024 and any(data), (encoder,len(data))
  out[encoder]={'decoded_bytes':len(data),'decoded_frames':len(data)//4,'input_frames':4800,
                'codec_frame_padding_or_trim_tolerance':1024,'format':info['codec_name']}
 c=pathlib.Path('api-client').resolve()
 out['c_client']=run(c,d/'pcm-48000-16.wav').decode().strip()
print(json.dumps(out,indent=2))
