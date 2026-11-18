"""Owned raw-DSD silence fixtures; never PCM encoding or resampling.

Container fields: Sony DSF specification 1.01:
https://dsd-guide.com/sites/default/files/white-papers/DSFFileFormatSpec_E.pdf
Silence byte: Linux sound/core/pcm_misc.c, SNDRV_PCM_FORMAT_DSD_U8.
DSF stores bit-reversed 0x96 with bits=1 (LSB first), so the decoder
recovers 0x69 in MSB-first order. Daphile SoX rejects bits=8 DSF files.
Higher rates are used only when the Daphile device capability list declares
them; they extend the rate list in the historical Sony specification.
"""
import hashlib,os,stat,struct,uuid
from pathlib import Path

DIRECTORY=Path('/mnt/hdd/daphile-autotune-samples')
RATES={2822400,5644800,11289600,22579200,3072000,6144000,12288000}
BLOCK=4096

def header(rate,seconds):
    if isinstance(rate,bool) or not isinstance(rate,int) or rate not in RATES:raise ValueError('Unsupported DSD fixture rate')
    if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=645:raise ValueError('DSF fixture duration 1..645 seconds')
    # Round sample count upward to full blocks: no unused zero padding reaches
    # the end of the test stream, including through a buggy external decoder.
    blocks=(rate*seconds+BLOCK*8-1)//(BLOCK*8);count=blocks*BLOCK*8;payload=blocks*BLOCK*2
    data=struct.pack('<4sQQQ',b'DSD ',28,92+payload,0)
    data+=struct.pack('<4sQ6IQ2I',b'fmt ',52,1,0,2,2,rate,1,count,BLOCK,0)
    data+=struct.pack('<4sQ',b'data',12+payload)
    assert len(data)==92
    return data,payload,count

def inspect(path):
    p=Path(path)
    with p.open('rb') as stream:data=stream.read(92)
    if len(data)!=92:raise RuntimeError('Truncated DSF header')
    magic,size,total,metadata=struct.unpack_from('<4sQQQ',data)
    fmt,fsize,version,kind,layout,channels,rate,bits,count,block,reserved=struct.unpack_from('<4sQ6IQ2I',data,28)
    dmagic,dsize=struct.unpack_from('<4sQ',data,80)
    if magic!=b'DSD ' or size!=28 or total!=p.stat().st_size:raise RuntimeError('Invalid DSF container size')
    if fmt!=b'fmt ' or fsize!=52 or version!=1 or kind!=0 or layout!=2 or channels!=2 or bits not in [1,8] or count<1 or rate<1 or block!=BLOCK or reserved:raise RuntimeError('Invalid stereo raw-DSD format')
    expected=((count+BLOCK*8-1)//(BLOCK*8))*BLOCK*channels
    if dmagic!=b'data' or dsize!=expected+12 or 80+dsize>total:raise RuntimeError('DSF payload size inconsistent')
    if metadata and not 80+dsize<=metadata<total:raise RuntimeError('DSF metadata pointer outside file')
    return dict(rate=rate,bits=bits,channels=channels,sample_count=count,seconds=count/rate,block_size=block,data_bytes=expected)

def owned(path):
    p=Path(path)
    if p.is_symlink() or p.parent!=DIRECTORY or not p.name.startswith('dat-dsf-') or p.suffix!='.dsf':return False
    if DIRECTORY.is_symlink() or DIRECTORY.resolve()!=DIRECTORY:return False
    try:
        parent=DIRECTORY.stat();file=p.stat()
        return parent.st_uid==0 and not parent.st_mode&0o022 and file.st_uid==0 and not file.st_mode&0o022 and p.is_file()
    except OSError:return False

def verify_owned(path,expected):
    """Hash an owned file before playback; never trust a supplied path or digest."""
    if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise RuntimeError('Invalid generated fixture digest')
    if not owned(path):raise RuntimeError('Generated fixture ownership changed')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>4*1024**3:raise RuntimeError('Invalid generated fixture file')
        digest=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
    current=os.stat(path,follow_symlinks=False)
    signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if digest!=expected or signature(before)!=signature(after) or signature(after)!=signature(current) or not owned(path):raise RuntimeError('Generated fixture changed before playback')
    return inspect(path)

def create(rate,seconds=75):
    if os.geteuid()!=0:raise RuntimeError('Root required to create registered test fixtures')
    data,payload,count=header(rate,seconds)
    if DIRECTORY.is_symlink():raise RuntimeError('Unsafe sample directory')
    DIRECTORY.mkdir(mode=0o755,exist_ok=True)
    if DIRECTORY.resolve()!=DIRECTORY or DIRECTORY.stat().st_uid!=0 or DIRECTORY.stat().st_mode&0o022:raise RuntimeError('Sample directory not root controlled')
    os.chmod(DIRECTORY,0o755);free=os.statvfs(DIRECTORY)
    if payload+64*1024*1024>free.f_bavail*free.f_frsize:raise RuntimeError('Insufficient DSF sample disk space')
    p=DIRECTORY/('dat-dsf-'+uuid.uuid4().hex+'.dsf')
    try:
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        digest=hashlib.sha256(data)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);chunk=b'\x96'*(BLOCK*2*128);remaining=payload
            while remaining:
                part=chunk[:min(remaining,len(chunk))];stream.write(part);digest.update(part);remaining-=len(part)
            stream.flush();os.fsync(stream.fileno())
        os.chmod(p,0o644);info=inspect(p)
        return dict(path=str(p),sha256=digest.hexdigest(),generated=True,signal='raw-DSD silence; not PCM conversion',**info)
    except BaseException:
        if p.exists():p.unlink()
        raise
