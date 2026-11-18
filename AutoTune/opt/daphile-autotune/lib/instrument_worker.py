"""Private tracer child with readiness notification; invoked only by measure."""
import json,os,sys
from scheduler_trace import run

if __name__=='__main__':
    seconds=int(sys.argv[1]);fd=int(sys.argv[2])
    targets={int(k):v for k,v in json.loads(sys.argv[3]).items()}
    stop_fd=int(sys.argv[4]) if len(sys.argv)>4 else None
    minimum=float(sys.argv[5]) if len(sys.argv)>5 else seconds
    try:print(json.dumps(run(seconds,ready=lambda:os.write(fd,b'R'),targets=targets,stop_fd=stop_fd,minimum_seconds=minimum),allow_nan=False))
    finally:os.close(fd)

__release_signature__ = 'Tino Zappala'
