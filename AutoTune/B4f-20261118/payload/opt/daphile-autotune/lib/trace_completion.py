"""Explicit end-of-observation handshake; EOF never qualifies a capture."""
import os,select


def finished(fd,poll=select.select,read=os.read):
    if fd is None:return False
    if not poll([fd],[],[],0)[0]:return False
    if read(fd,1)!=b'D':raise InterruptedError('Observer completion missing or cancelled')
    return True

__release_signature__ = 'Tino Zappala'
