"""Experimental private ALSA PCM route; no writes or device access at import.

This is a laboratory proposal, not an installed module.  It uses the stock
configuration and maps only the selected DAC's PCM device 0 and subdevice
-1/0 to the reserved loopback subdevice, for both hw and plughw.  The latter
is deliberately direct hardware on the selected route: it cannot resample.
"""
import argparse
import re


def make_config(alsa_id, subdevice):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alsa_id):
        raise ValueError("invalid ALSA card id")
    if type(subdevice) is not int or subdevice not in (6, 7):
        raise ValueError("reserved Loopback subdevice must be 6 or 7")
    target = 'type hw\n card "Loopback"\n device 0\n subdevice %d' % subdevice
    # card_id normalizes a verified numeric card and its ALSA id at open time.
    # The key also restricts device and subdevice; an unmatched request keeps
    # the stock direct-hw or plughw definition, respectively.
    path = ('name { @func concat strings [ "darc_lab_route." '
            '{ @func card_id card $CARD } "." $DEV "." $SUBDEV ] }')
    args = ('@args [ CARD DEV SUBDEV ]\n'
            '@args.CARD { type string default { @func getenv vars [ ALSA_PCM_CARD ALSA_CARD ] '
            'default { @func refer name defaults.pcm.card } } }\n'
            '@args.DEV { type integer default { @func igetenv vars [ ALSA_PCM_DEVICE ] '
            'default { @func refer name defaults.pcm.device } } }\n'
            '@args.SUBDEV { type integer default { @func refer name defaults.pcm.subdevice } }\n')
    selected = ''.join('darc_lab_route."%s"."0"."%s" { %s }\n' %
                       (alsa_id, dev, target) for dev in ('-1', '0'))
    direct = 'type hw\n card $CARD\n device $DEV\n subdevice $SUBDEV'
    plug = 'type plug\n slave.pcm { %s }' % direct
    nodes = ''.join('pcm.!%s {\n%s @func refer\n %s\n default { %s }\n}\n' %
                    (name, args, path, fallback)
                    for name, fallback in (('hw', direct), ('plughw', plug)))
    return '</usr/share/alsa/alsa.conf>\n' + selected + nodes


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('alsa_id')
    parser.add_argument('subdevice', type=int, choices=(6, 7))
    args = parser.parse_args()
    print(make_config(args.alsa_id, args.subdevice), end='')
