"""Native defaults plus sparse per-DAC overrides; no file writes or guessed values."""
import re
from pathlib import Path
KEYS=('BUFFERTIME','PERIODCOUNT')
CHOICES=(10,15,20,25,30,40,50,60,80,100,150,200)


def preferences(text,defaults=None):
    def parse(value):
        result={}
        for key in KEYS:
            rows=re.findall(r'^'+key+r'=([^\r\n]*)$',value,re.M)
            if len(rows)>1 or rows and not re.fullmatch(r'[0-9]+',rows[0]):raise RuntimeError('Buffer preference invalid or duplicated: '+key)
            if rows:result[key]=rows[0]
        return result
    values=parse(text)
    if len(values)<2:
        inherited=parse(Path('/opt/spac/share/defaults.conf').read_text() if defaults is None else defaults)
        inherited.update(values);values=inherited
    if set(values)!=set(KEYS):raise RuntimeError('Native buffer defaults unavailable')
    ms,periods=(int(values[k]) for k in KEYS)
    # Reading an existing native value does not authorize writing it as a new
    # tuning candidate. In particular, native Settings can already hold 42 ms.
    if min(ms,periods)<=0:raise RuntimeError('Native buffer preferences must be positive')
    return values


def edit(text,ms,periods,defaults=None):
    if type(ms)!=int or type(periods)!=int:raise ValueError('Integer ALSA buffer/period choice required')
    current=preferences(text,defaults)
    if (ms,periods)==tuple(int(current[k]) for k in KEYS):return text
    if ms not in CHOICES or periods not in (3,4):raise ValueError('Unapproved ALSA buffer/period choice')
    for key,value in zip(KEYS,(ms,periods)):
        if re.search(r'^'+key+r'=',text,re.M):text=re.sub(r'^'+key+r'=[^\r\n]*',key+'='+str(value),text,flags=re.M)
        else:text+=('' if not text or text.endswith('\n') else '\n')+key+'='+str(value)+'\n'
    return text


def validate_transition(before,after,defaults=None):
    original=preferences(before,defaults);changed=preferences(after,defaults)
    if non_buffer(before)!=non_buffer(after):raise RuntimeError('Non-buffer preference change rejected')
    if changed!=original and (int(changed['BUFFERTIME']) not in CHOICES or int(changed['PERIODCOUNT']) not in (3,4)):
        raise RuntimeError('Unapproved new ALSA buffer/period choice')


def matrix_candidate(ms,periods,original,defaults=None):
    if type(ms)!=int or type(periods)!=int:return False
    current=preferences(original,defaults)
    return ((ms,periods)==tuple(int(current[k]) for k in KEYS)
            or ms in CHOICES and periods in (3,4))


def non_buffer(text):
    return re.sub(r'^(?:BUFFERTIME|PERIODCOUNT)=[^\r\n]*(?:\r?\n|$)','',text,flags=re.M).rstrip('\r\n')

__release_signature__ = 'Tino Zappala'
