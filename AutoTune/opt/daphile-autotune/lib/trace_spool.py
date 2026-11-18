"""Buffer raw scheduler events privately; decode after the capture interval.

Kernel timestamps and loss counters remain authoritative. The size bound fails
the observation instead of dropping events or claiming partial coverage.
"""
import tempfile


class TraceSpool:
    def __init__(self, limit=512 * 1024 * 1024):
        if type(limit) is not int or limit < 1:
            raise ValueError('Positive trace byte limit required')
        self.stream = tempfile.TemporaryFile(mode='w+b')
        self.limit = limit
        self.size = 0

    def append(self, chunk):
        if self.size + len(chunk) > self.limit:
            raise RuntimeError('Scheduler capture exceeds private byte budget; observation incomplete')
        if self.stream.write(chunk) != len(chunk):
            raise OSError('Incomplete scheduler capture write')
        self.size += len(chunk)

    def replay(self, counter):
        self.stream.seek(0)
        while True:
            line = self.stream.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                raise RuntimeError('Malformed oversized scheduler trace line')
            if not line.endswith(b'\n'):
                if line.strip():
                    counter.unparsed += 1
                continue
            counter.feed(line.decode(errors='replace'))

    def close(self):
        self.stream.close()

__release_signature__ = 'Tino Zappala'
