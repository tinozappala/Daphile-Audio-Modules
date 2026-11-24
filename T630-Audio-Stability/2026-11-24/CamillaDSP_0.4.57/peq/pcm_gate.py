"""Byte-preserving stereo PCM gate. Recognised DoP never reaches a DSP filter.

Fifteen frames of lookahead cover marker runs split across reads. Detection
does not establish the source format: the upstream PCM contract is also needed.
"""
import argparse
import sys


class GateError(RuntimeError):
    pass


FORMATS = {
    'S16_LE': (2, ()),
    'S24_3_LE': (3, (2,)),
    'S24_4_RJ_LE': (4, (2,)),
    'S32_LE': (4, (2, 3)),
}
PATTERNS = (b'\x05\xfa' * 8, b'\xfa\x05' * 8)


class PCMGate:
    def __init__(self, sample_format):
        if sample_format not in FORMATS:
            raise GateError('unsupported-pcm-format')
        self.width, self.marker_offsets = FORMATS[sample_format]
        self.frame_bytes = self.width * 2
        self.pending = b''
        self.failed = False
        self.finished = False

    def _check(self, data):
        for offset in self.marker_offsets:
            left = data[offset::self.frame_bytes]
            right = data[offset+self.width::self.frame_bytes]
            for pattern in PATTERNS:
                at = left.find(pattern)
                while at >= 0:
                    if right[at:at+16] == pattern:
                        self.failed = True
                        raise GateError('unsupported-dop')
                    at = left.find(pattern, at+1)

    def feed(self, data):
        if self.failed or self.finished:
            raise GateError('gate-closed')
        if not isinstance(data, bytes) or len(data) > 65536:
            self.failed = True
            raise GateError('invalid-pcm-block')
        block = self.pending + data
        complete = len(block) // self.frame_bytes * self.frame_bytes
        self._check(block[:complete])
        emit = max(0, complete-15*self.frame_bytes)
        result, self.pending = block[:emit], block[emit:]
        return result

    def finish(self):
        if self.failed or self.finished:
            raise GateError('gate-closed')
        if len(self.pending) % self.frame_bytes:
            self.failed = True
            raise GateError('incomplete-pcm-frame')
        self._check(self.pending)
        self.finished = True
        result, self.pending = self.pending, b''
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--format', required=True, choices=FORMATS)
    args = parser.parse_args()
    gate = PCMGate(args.format)
    try:
        while True:
            block = sys.stdin.buffer.read1(16384)
            if not block:
                break
            output = gate.feed(block)
            if output:
                sys.stdout.buffer.write(output)
                sys.stdout.buffer.flush()
        sys.stdout.buffer.write(gate.finish())
        sys.stdout.buffer.flush()
    except (GateError, BrokenPipeError) as error:
        print('PCM_GATE:'+str(error) if isinstance(error, GateError) else 'PCM_GATE:downstream-closed', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
