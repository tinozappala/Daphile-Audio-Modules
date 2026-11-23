#!/usr/bin/python3
"""Daphile t630 audio-first CPU affinity guard. No audio settings changed."""
import os, fcntl, signal, time, subprocess
from pathlib import Path
running=True
def stop(sig, frame):
    global running
    running=False
def classify(args):
    if not args: return None
    executable=os.path.basename(os.fsdecode(args[0]))
    joined=b" ".join(args)
    if executable in ("squeezelite","camilladsp"): return {3}
    if executable=="arecord" and b"hw:Loopback,1,6" in joined: return {3}
    if b"peq.pcm_gate" in joined: return {3}
    # Perl may launch slimserver.pl and scanner.pl with an interpreter argv[0].
    # Restrict matching to exact script basenames, never generic Perl tasks.
    if executable.startswith("perl"):
        scripts={os.path.basename(os.fsdecode(arg)) for arg in args[1:]}
        if "scanner.pl" in scripts: return {0}
        if "slimserver.pl" in scripts: return {0,1}
    if executable in ("bash","sh"):
        scripts={os.path.basename(os.fsdecode(arg)) for arg in args[1:]}
        if "spacd" in scripts or "Decode" in scripts: return {0,1}
    if executable=="scanner.pl": return {0}
    if executable in ("sox","ffmpeg","Decode"): return {0,1}
    if executable in ("slimserver.pl","spacd"): return {0,1}
    if b"/daphile-audio-host/code/broker.py" in joined: return {0,1}
    if b"/daphile-audio-host/modules/" in joined and b"/worker.py" in joined: return {0,1}
    return None
def record(s):
    with open("/run/daphile-audio-affinity.log","a") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S%z ")+s+"\n")
_scanner_seen=set()
def tune_scanner(pid):
    """Lower only scanner.pl, not slimserver.pl or external audio decoders."""
    pid=int(pid)
    status=Path("/proc")/str(pid)/"stat"
    start=status.read_text().rsplit(")",1)[1].split()[19]
    key=(pid,start)
    if key in _scanner_seen:return
    if os.getpriority(os.PRIO_PROCESS,pid)<10:
        os.setpriority(os.PRIO_PROCESS,pid,10)
    cmd=["/usr/bin/ionice","-c2","-n7","-p",str(pid)]
    result=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=2)
    if result.returncode:
        raise OSError("scanner ionice rejected: "+result.stderr.decode(errors="replace")[:80])
    verification=subprocess.run(["/usr/bin/ionice","-p",str(pid)],capture_output=True,text=True,timeout=2)
    if verification.returncode or "best-effort" not in verification.stdout or "prio 7" not in verification.stdout:
        raise OSError("scanner I/O priority not verified")
    if os.getpriority(os.PRIO_PROCESS,pid)<10:
        raise OSError("scanner nice level not verified")
    _scanner_seen.add(key)
    record("scanner-throttled pid=%s nice=10 ionice=best-effort/7 cpu=0"%pid)

def sweep():
    # The PCI legacy IRQ can differ from the actual xHCI MSI-X thread.
    # Derive the IRQ(s) from the PCI MSI directory; never hardcode IRQ 27.
    try:
        irq_dir=Path("/sys/bus/pci/devices/0000:00:10.0/msi_irqs")
        usb_irq_names={"irq/"+p.name+"-xhci_hcd" for p in irq_dir.iterdir() if p.name.isdigit()}
    except OSError:
        usb_irq_names=set()
    for ent in os.scandir("/proc"):
        if not ent.name.isdigit(): continue
        pid=ent.name
        try:
            with open("/proc/"+pid+"/cmdline","rb") as f:
                args=[v for v in f.read(16384).split(b"\0") if v]
            mask=classify(args)
            if mask is None and not args and usb_irq_names:
                try:
                    if Path("/proc/"+pid+"/comm").read_text().strip() in usb_irq_names:
                        mask={2}
                except OSError:
                    pass
            if mask is None: continue
            if mask=={0}:
                try:tune_scanner(pid)
                except (OSError,subprocess.TimeoutExpired) as error:
                    record("scanner-throttle-pending pid=%s type=%s"%(pid,type(error).__name__))
            for name in os.listdir("/proc/"+pid+"/task"):
                if not name.isdigit(): continue
                tid=int(name)
                try:
                    if os.sched_getaffinity(tid)!=mask:
                        os.sched_setaffinity(tid,mask)
                        record("pid=%s tid=%s cpus=%s"%(pid,tid,sorted(mask)))
                except OSError: pass
        except OSError: continue
def main():
    lock=os.open("/run/daphile-audio-affinity.lock",os.O_CREAT|os.O_RDWR|getattr(os,"O_NOFOLLOW",0),0o600)
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: return
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try: os.nice(10)
    except OSError: pass
    # Keep the maintenance guard off the USB IRQ and PCM cores.
    os.sched_setaffinity(0,{1})
    record("started cpus=services:0,1; usb-irq:2; audio:3; guard:1")
    while running:
        sweep()
        time.sleep(2)
    record("stopped")
    os.close(lock)
if __name__=="__main__": main()
