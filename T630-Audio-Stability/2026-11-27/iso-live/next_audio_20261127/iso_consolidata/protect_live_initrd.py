"""Isolate a generic live-ISO boot from all attached writable DATA disks."""
import gzip
import hashlib

STOCK_INITRD_SHA='b5f266cb32bc69855bcda8f37dc7043f975401511ace0bd615f5335a7c913961'

ORIGINAL_BOOT=b'''mount_boot() {
\tlocal uuid="${boot_uuid}"
\tlocal dev

\tfor timeout in $(seq 50)
\tdo
\t\tif [[ -n "${uuid}" ]]
\t\tthen
\t\t\tdev=$(findfs UUID=${uuid} 2> /dev/null)
\t\t\t[[ -b "${dev}" ]] && mount_dev ${dev} /boot && return 0
\t\telse
\t\t\tfor dev in $(find /dev/ -type b)
\t\t\tdo
\t\t\t\t[[ "${dev##*/}" =~ ^loop[0-9]+$ ]] && continue
\t\t\t\tblkid ${dev} >&/dev/null && mount_dev ${dev} /boot || continue
\t\t\t\t[[ -f "/boot/boot/${daphile}/rootfs" && -e "/boot/boot/live" ]] && return 0
\t\t\t\tumount -ln /boot
\t\t\tdone
\t\tfi
\t\tsleep 0.1
\tdone
\treturn 1
}'''

SAFE_BOOT=b'''mount_boot() {
\t# A live ISO searches only read-only ISO9660 media, never mounts DATA.
\tlocal dev devices fs
\tfor timeout in $(seq 50)
\tdo
\t\tif [[ -n "${boot_uuid}" ]]; then
\t\t\tdevices=$(findfs UUID=${boot_uuid} 2>/dev/null)
\t\telse
\t\t\tdevices=$(find /dev/ -type b)
\t\tfi
\t\tfor dev in ${devices}; do
\t\t\t[[ -b "${dev}" && ! "${dev##*/}" =~ ^loop[0-9]+$ ]] || continue
\t\t\tfs=$(blkid "${dev}" 2>/dev/null | sed 's/.* TYPE="\\([^"]*\\)".*/\\1/')
\t\t\t[[ "${fs}" = iso9660 ]] || continue
\t\t\tmount -n -t iso9660 -o ro "${dev}" /boot 2>/dev/null || continue
\t\t\t[[ -f "/boot/boot/${daphile}/rootfs" && -e "/boot/boot/live" ]] && return 0
\t\t\tumount -ln /boot
\t\tdone
\t\tsleep 0.1
\tdone
\treturn 1
}'''

ORIGINAL_DATA=b'''mount_data() {
\tlocal uuid="${data_uuid}"
\tlocal target="$1"
\tlocal dev fs

\tif [[ -n "${uuid}" ]] && dev=$(findfs UUID=${uuid} 2> /dev/null)
\tthen
\t\tfs=$(blkid ${dev} 2>/dev/null | sed 's/.* TYPE="\\([^"]*\\)".*/\\1/')
\tfi

\tmount_dev ${dev:-none} ${target} || rescue_shell
}'''

SAFE_DATA=b'''mount_data() {
\t# Even an inherited boot variable cannot select a persistent DATA disk.
\tmount_dev none "$1" || rescue_shell
}'''


def records(data):
    """Yield validated newc offsets and payloads up to and including trailer."""
    pos=0
    while True:
        start=pos
        if data[pos:pos+6] not in (b'070701',b'070702') or len(data)<pos+110:raise ValueError('invalid cpio header')
        h=data[pos:pos+110]
        fields=[int(h[6+i*8:14+i*8],16) for i in range(13)]
        size,namesize=fields[6],fields[11]
        if namesize<1 or namesize>65536:raise ValueError('invalid cpio name')
        nameend=pos+110+namesize
        if nameend>len(data) or data[nameend-1]!=0:raise ValueError('invalid cpio name extent')
        name=data[pos+110:nameend-1].decode('utf8')
        content=(nameend+3)&~3;end=(content+size+3)&~3
        if end>len(data):raise ValueError('invalid cpio payload extent')
        yield name,start,content,size,end
        pos=end
        if name=='TRAILER!!!':return


def rewrite_last_archive(data):
    entries=list(records(data));changed=0;out=bytearray()
    for name,start,content,size,end in entries:
        if name!='init':out+=data[start:end];continue
        raw=data[content:content+size]
        if raw.count(ORIGINAL_BOOT)!=1 or raw.count(ORIGINAL_DATA)!=1:raise RuntimeError('unknown stock initramfs')
        new=raw.replace(ORIGINAL_BOOT,SAFE_BOOT).replace(ORIGINAL_DATA,SAFE_DATA)
        header=bytearray(data[start:start+110]);header[54:62]=('%08x'%len(new)).encode('ascii')
        out+=header;out+=data[start+110:content];out+=new;out+=b'\0'*((-len(out))%4);changed+=1
    if changed!=1:raise RuntimeError('unique initramfs init required')
    out+=data[entries[-1][4]:]
    return bytes(out)


def protect(initrd):
    if hashlib.sha256(initrd).hexdigest()!=STOCK_INITRD_SHA:raise RuntimeError('unknown stock initrd digest')
    first=list(records(initrd));offset=(first[-1][4]+511)&~511
    if initrd[offset:offset+3]!=b'\x1f\x8b\x08':raise RuntimeError('unknown concatenated initrd format')
    original=gzip.decompress(initrd[offset:]);changed=rewrite_last_archive(original)
    # The boot-time base archive (including kernel modules) is byte-identical.
    result=initrd[:offset]+gzip.compress(changed,compresslevel=9,mtime=0)
    if gzip.decompress(result[offset:])!=changed:raise RuntimeError('protected initrd roundtrip mismatch')
    return result
