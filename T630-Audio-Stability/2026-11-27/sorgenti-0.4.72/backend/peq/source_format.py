"""Conservative source policy: a DSF selection never authorises PCM processing."""
from pathlib import PurePosixPath
import re
from urllib.parse import unquote,urlsplit

PCM_SUFFIXES={'.flac','.wav','.aif','.aiff','.mp3','.m4a','.aac','.ogg','.opus','.alac'}
DSD_SUFFIXES={'.dsf','.dff','.dsd'}


def classify_source(url,metadata=None):
    """Return only format facts; URLs and track names never enter public status.

    PCM-container recognition still requires the runtime byte gate. In
    particular WAV/FLAC can contain DoP, and WavPack can contain native DSD.
    No metadata field here authorises implicit DSD-to-PCM conversion.
    """
    if not isinstance(url,str) or not url:return {'kind':'unknown','reason':'missing-source'}
    try:suffix=PurePosixPath(unquote(urlsplit(url).path)).suffix.lower()
    except ValueError:return {'kind':'unknown','reason':'invalid-source'}
    metadata=metadata if isinstance(metadata,dict) else {}
    declared=' '.join(str(metadata.get(key,'')) for key in
        ('type','content_type','format','audioformat','filetype'))
    if suffix in DSD_SUFFIXES or re.search(r'(?i)(\bds[fd]\b|\bdsd(?:\d+)?\b|\bdop\b)',declared):
        return {'kind':'DSD','reason':'native-bypass-required'}
    if metadata.get('samplesize')==1 or metadata.get('bits_per_sample')==1:
        return {'kind':'DSD','reason':'one-bit-source'}
    if suffix in PCM_SUFFIXES:return {'kind':'PCM','reason':'pcm-container-runtime-gate-required'}
    return {'kind':'unknown','reason':'source-format-not-qualified'}
