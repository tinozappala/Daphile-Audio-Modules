"""Read a unique physical Daphile output without claiming idle PCM is active."""
import re
from pathlib import Path


def resolve(hardware,player=None,read=None,runtime=Path('/run/spacd/c'),identity=None,identified=None):
    if player is None:
        import player
    if identity is None or identified is None:
        from physical_audio import identity,identified
    active=hardware.get('dac',{})
    if active.get('status')=='verified-active' and identified(active.get('identity')):
        return player.target(hardware)
    if read is None:
        from detection import read
    # An active virtual loopback is not the physical USB output. Resolve the
    # unique configured physical card without claiming its idle PCM is active.
    if active.get('status') not in ('unknown',None,'verified-active'):raise RuntimeError('Ambiguous active DAC')
    candidates=[]
    for card in hardware.get('alsa',{}).get('cards',[]):
        ident=identity(card,hardware.get('usb',[]))
        if not identified(ident):continue
        directory=Path(runtime)/str(card['card'])
        if not directory.is_dir() or not (directory/'home').exists():continue
        home=(directory/'home').resolve(strict=True);preferences=home/'volume.conf'
        if not str(home).startswith('/mnt/hdd/system/spac/') or preferences.is_symlink() or not preferences.is_file():
            raise RuntimeError('Native idle preference location is not verified')
        macs=list((directory/'d').glob('*/home/macaddr'))
        if not macs:continue  # Discovered cards can be disabled/unconfigured.
        if len(macs)!=1:raise RuntimeError('Ambiguous native idle player')
        mac=read(macs[0])
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac or ''):raise RuntimeError('Invalid idle player identity')
        device=macs[0].parent.parent.name
        if not device.isdigit():raise RuntimeError('Invalid native playback device')
        candidates.append(dict(host_uuid=hardware['system']['product_uuid'],path=str(preferences),
            identity=ident,alsa_id=card['id'],card=card['card'],device=int(device),mac=mac))
    if len(candidates)!=1:raise RuntimeError('One uniquely identified native physical ALSA output required')
    return candidates[0]


def rate(hardware,target,read=None):
    if read is None:
        from detection import read
    cards=[c for c in hardware.get('alsa',{}).get('cards',[]) if c['card']==target['card'] and c['id']==target['alsa_id']]
    if len(cards)!=1:raise RuntimeError('Exact native card capabilities required')
    card=cards[0];rates=set();device=target.get('device')
    if type(device) is not int or device<0 or device not in {p['device'] for p in card.get('pcms',[])}:
        raise RuntimeError('Exact native playback device capabilities required')
    info=read('/run/spacd/info/'+str(target['card'])+'.'+str(device)) or ''
    matches=re.findall(r'^PCM_RATES=([0-9 ]+)$',info,re.M)
    if not re.search(r'^PCM_RATES=',info,re.M):
        # Native HDA/HDMI discovery publishes RATES, while USB/DSD discovery
        # may additionally split PCM_RATES from the transport capabilities.
        matches=re.findall(r'^RATES=([0-9 ]+)$',info,re.M)
    if len(matches)==1:rates.update(map(int,matches[0].split()))
    from physical_audio import identity,identified
    ident=identity(card,hardware.get('usb',[]))
    if not identified(ident) or ident!=target.get('identity'):raise RuntimeError('Physical native output capabilities required')
    declared=card.get('declared_pcm_rates',[])
    if declared:rates.intersection_update(declared)
    from audio_setup import preferences
    defaults=read('/opt/spac/share/defaults.conf');configured=read(target['path'])
    if defaults is None or configured is None:raise RuntimeError('Native audio configuration unavailable for controlled PCM verification')
    settings=preferences(defaults);settings.update(preferences(configured))
    # The observed native contract guarantees declared-rate passthrough only
    # for this mode. Preserve other user modes instead of inventing their
    # output rate or changing them to make the qualification pass.
    if settings.get('RESAMPLE')!='exception' or settings.get('RATELIMIT')!='0':
        raise RuntimeError('Prova PCM non disponibile con il ricampionamento nativo corrente '
            '(RESAMPLE='+str(settings.get('RESAMPLE'))+', RATELIMIT='+str(settings.get('RATELIMIT'))+'). Impostazioni conservate.')
    for value in (192000,96000,48000,44100):
        if value in rates:return value
    raise RuntimeError('No controlled PCM rate declared by the native physical output')

__release_signature__ = 'Tino Zappala'
