"""Read a unique Daphile USB output without pretending idle PCM is active."""
import re
from pathlib import Path


def resolve(hardware,player=None,read=None,runtime=Path('/run/spacd/c'),identity=None,identified=None):
    if player is None:
        import player
    if hardware.get('dac',{}).get('status')=='verified-active':return player.target(hardware)
    if read is None:
        from detection import read
    if identity is None or identified is None:
        from usb_target import identity,identified
    if hardware.get('dac',{}).get('status') not in ('unknown',None):raise RuntimeError('Ambiguous active DAC')
    candidates=[]
    for card in hardware.get('alsa',{}).get('cards',[]):
        matches=[u for u in hardware.get('usb',[]) if u.get('path')==card.get('usb_path')]
        if len(matches)!=1 or not identified(identity(matches[0])):continue
        directory=Path(runtime)/str(card['card'])
        home=(directory/'home').resolve(strict=True);preferences=home/'volume.conf'
        if not str(home).startswith('/mnt/hdd/system/spac/') or preferences.is_symlink() or not preferences.is_file():
            raise RuntimeError('Native idle preference location is not verified')
        macs=list((directory/'d').glob('*/home/macaddr'))
        if len(macs)!=1:raise RuntimeError('Ambiguous native idle player')
        mac=read(macs[0])
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac or ''):raise RuntimeError('Invalid idle player identity')
        candidates.append(dict(host_uuid=hardware['system']['product_uuid'],path=str(preferences),
            identity=identity(matches[0]),alsa_id=card['id'],card=card['card'],mac=mac))
    if len(candidates)!=1:raise RuntimeError('One uniquely identified native USB output required')
    return candidates[0]


def rate(hardware,target,read=None):
    if read is None:
        from detection import read
    cards=[c for c in hardware.get('alsa',{}).get('cards',[]) if c['card']==target['card'] and c['id']==target['alsa_id']]
    if len(cards)!=1:raise RuntimeError('Exact native card capabilities required')
    card=cards[0];rates=set()
    devices={p['device'] for p in card.get('pcms',[])}
    for device in devices:
        info=read('/run/spacd/info/'+str(target['card'])+'.'+str(device)) or ''
        matches=re.findall(r'^PCM_RATES=([0-9 ]+)$',info,re.M)
        if len(matches)==1:rates.update(map(int,matches[0].split()))
    rates.intersection_update(card.get('declared_pcm_rates',[]))
    for value in (192000,96000,48000,44100):
        if value in rates:return value
    raise RuntimeError('No controlled PCM rate declared by both ALSA and Daphile')

__release_signature__ = 'Tino Zappala'
