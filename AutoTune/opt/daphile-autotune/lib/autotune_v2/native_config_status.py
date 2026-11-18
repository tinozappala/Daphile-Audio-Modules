"""Narrow supplemental boot proof for one observed native mount timestamp.

The default byte-equality guard is unchanged. No general exclusion of status
fields, settings, comments, ordering, whitespace or connection-state changes.
"""
import hashlib,math,re

WRITER_SHA256='471073a24af3cc6ce71473421451e0492615f47b4a9c162c6a48dc93b78d1835'
INIT_SHA256='9b0d334d406759d4910e1249ab3255f0d0e5400e60c3c30a4dfe1fac8dbb6989'
FIELD=b'net_drive_status_0'


def online_timestamp_difference(before,after,writer_sha256,init_sha256,boot_started,observed):
    if (writer_sha256,init_sha256)!=(WRITER_SHA256,INIT_SHA256):
        raise ValueError('Installed native mount writer/startup source not verified')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (boot_started,observed)) or observed<boot_started:
        raise ValueError('Actual boot and observation times required')
    if not all(isinstance(v,bytes) and 0<len(v)<=1024*1024 for v in (before,after)):
        raise ValueError('Complete bounded native configuration bytes required')
    def parse(data):
        values={}
        for line in data.decode('utf8').splitlines():
            if not line or line.startswith('#'):continue
            row=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)=(.*)',line)
            if not row or row[1] in values:raise ValueError('Unparsed or duplicate native setting')
            values[row[1]]=row[2]
        return values
    old,new=parse(before),parse(after)
    expression=re.compile(rb'(?m)^(net_drive_status_0=online_)([0-9]{10})(?=\r?$)')
    a=list(expression.finditer(before));b=list(expression.finditer(after))
    if len(a)!=1 or len(b)!=1:raise ValueError('Exactly one online timestamp for the observed drive required')
    first,last=int(a[0][2]),int(b[0][2])
    if not first<boot_started<=last+5 or last>observed+5 or last<=first:
        raise ValueError('Online timestamp is not a forward refresh during this boot')
    normalize=lambda value:expression.sub(lambda m:m[1]+b'@BOOT_STATUS_TIMESTAMP@',value)
    normalized=normalize(before)
    if normalized!=normalize(after):raise ValueError('A byte outside the known mount timestamp changed')
    changed=[k for k in old.keys()&new.keys() if old[k]!=new[k]]
    if set(old)!=set(new) or changed!=['net_drive_status_0']:
        raise ValueError('Native settings differ outside the observed timestamp')
    return dict(status='PASS_NATIVE_CONFIG_ONLY_ONLINE_TIMESTAMP_CHANGED',
        before_sha256=hashlib.sha256(before).hexdigest(),after_sha256=hashlib.sha256(after).hexdigest(),
        normalized_sha256=hashlib.sha256(normalized).hexdigest(),setting_count=len(old),changed_keys=changed,
        all_other_bytes_identical=True,online_state_unchanged=True,timestamp_advanced_during_this_boot=True,
        native_writer_sha256=writer_sha256,native_startup_sha256=init_sha256,
        raw_byte_equal=False,configuration_writes=0,user_values_disclosed=False)

__release_signature__ = 'Tino Zappala'
