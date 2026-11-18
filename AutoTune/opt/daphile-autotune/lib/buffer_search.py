"""Verify the existing buffer first; expand only to recover an unstable setup."""
import copy


def candidates(original):
    ms,periods=original
    if type(ms)!=int or ms<=0 or type(periods)!=int or periods<=0:
        raise RuntimeError('Il buffer ALSA corrente non contiene valori interi positivi verificabili')
    # The first pair comes from the real, parsed native configuration. The
    # matrix adapter independently permits a nonstandard value only when it
    # exactly matches that original configuration. Alternatives stay allowlisted.
    larger=[n for n in (100,150,200) if n>=ms]
    # A native value may exceed the allowed alternatives. Falling back to
    # 200 ms in that case would shrink a failing buffer, contradicting this
    # recovery-only policy. Keep the original when no approved expansion exists.
    choices=[tuple(original)]
    if larger:choices.extend(((larger[0],4),(larger[-1],4)))
    return list(dict.fromkeys(choices))


def verify(matrix,manifest,original,state,budget):
    attempts=[]
    for index,choice in enumerate(candidates(original)):
        if index:budget.require(120+len(manifest['entries'])*20)
        trial=copy.deepcopy(manifest);trial['buffer_candidates']=[choice]
        measured=matrix.run(trial,10,state,1.5)
        attempts.append(measured)
        if measured.get('status')!='finished' or measured.get('restoration')!='rolled-back':
            raise RuntimeError('Buffer verification did not restore its original configuration')
        group=next((g for g in measured.get('groups',[]) if (g.get('buffer_ms'),g.get('periods'))==choice),None)
        if group and group.get('complete') is True:
            result=dict(measured,groups=[g for a in attempts for g in a['groups']],
                        results=[r for a in attempts for r in a.get('results',[])],
                        buffer_attempts=attempts,current_first=True,alternatives_required=index>0)
            reason=('Buffer attuale verificato e conservato; nessuna alternativa necessaria.' if index==0 else
                    'Buffer attuale non stabile: alternativa verificata sulle finestre rappresentative. Non è una certificazione prolungata.')
            return result,choice,reason
    raise RuntimeError('No complete stable buffer candidate; originals retained')

__release_signature__ = 'Tino Zappala'
