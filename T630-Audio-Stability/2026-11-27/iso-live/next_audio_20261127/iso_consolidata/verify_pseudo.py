"""Compare all pseudo filesystem contents and hardlink groups offline."""
import argparse
from collections import defaultdict
import hashlib
import json
import mmap
from pathlib import Path

from build_iso import Pseudo,name_and_rest


def inventory(path):
    p=Pseudo(path); result={};links={}
    with p.path.open('rb') as f, mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as data:
        for name,row in p.rows.items():
            kind=row['kind'];fields=row['fields']
            if kind=='L':
                target,remaining=name_and_rest(row['rest'][2:])
                if remaining:raise RuntimeError('ambiguous hardlink target')
                links[name]=target
            elif kind=='R':
                start=p.start+int(fields[6]);size=int(fields[5]);h=hashlib.sha256()
                for pos in range(start,start+size,1024*1024):h.update(data[pos:min(pos+1024*1024,start+size)])
                result[name]=('R',*(x.decode() for x in fields[1:6]),h.hexdigest())
            else:
                result[name]=(kind,row['rest'].decode('utf-8','surrogateescape'))
    def resolve(name,seen=()):
        if name in seen:raise RuntimeError('hardlink cycle')
        return resolve(links[name],seen+(name,)) if name in links else name
    groups=defaultdict(set)
    for name in links:
        target=resolve(name)
        if target not in result or result[target][0]!='R':raise RuntimeError('hardlink target missing')
        result[name]=result[target];groups[target].update((name,target))
    return result,{tuple(sorted(v)) for v in groups.values()}


def compare(expected,actual):
    left,left_links=inventory(expected);right,right_links=inventory(actual)
    changed=[name for name in left.keys()|right.keys() if left.get(name)!=right.get(name)]
    if changed or left_links!=right_links:
        raise RuntimeError('filesystem readback differs: '+repr(changed[:20])+
                           '; hardlink groups differ='+str(left_links!=right_links))
    return {'status':'FULL_ROOTFS_READBACK_PASS','nodes':len(left),'hardlink_groups':len(left_links)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected',type=Path,required=True)
    parser.add_argument('--actual',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(compare(args.expected,args.actual),indent=2))
