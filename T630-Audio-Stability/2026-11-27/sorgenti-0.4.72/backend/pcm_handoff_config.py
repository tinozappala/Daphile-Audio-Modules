"""Experimental open guard around the tested selective private PCM route."""
import re
from pcm_selective_route import make_config


def make_handoff_config(alsa_id, subdevice, socket_path='/tmp/darc-handoff.sock',
                        guard_library='/bin/pcm-handoff-guard.so'):
    if not re.fullmatch(r'/[A-Za-z0-9_./-]{1,99}', socket_path) or '..' in socket_path.split('/'):
        raise ValueError('unsafe laboratory socket path')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]{1,511}', guard_library) or '..' in guard_library.split('/'):
        raise ValueError('unsafe laboratory PCM guard library path')
    config = make_config(alsa_id, subdevice)
    direct = 'type hw\n card "Loopback"\n device 0\n subdevice %d' % subdevice
    guarded = 'type darc_lab_guard\n socket "%s"' % socket_path
    if config.count(direct) != 2:
        raise ValueError('unexpected selective PCM configuration')
    return (config.replace(direct, guarded) +
            '\npcm.darc_lab_direct { %s }\n' % direct +
            'pcm_type.darc_lab_guard { lib "%s" '
            'open "_snd_pcm_darc_lab_guard_open" }\n' % guard_library)
