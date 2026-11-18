"""Complete POWER decision inventory; observations never imply applied settings."""
from pathlib import Path
import json,re,subprocess

# Every visible control in the recovered Settings POWER section. Inapplicable
# controls are retained in the report instead of silently disappearing.
CONTROLS = {
    'hw_cpufreq': ('Frequenza CPU', 'measured-trial', 'Confronti ripetuti di latenza, carico per core, temperatura e margine; niente massimo fisso senza evidenza.'),
    'grub_mitigations_off': ('Protezioni CPU', 'user-choice', 'Mantieni le protezioni correnti; una scelta di sicurezza non è un punteggio audio.'),
    'hw_clksrc': ('Clocksource', 'measured-trial', 'Solo sorgenti offerte dal kernel, confronto completo e ripristino della sorgente iniziale.'),
    'hw_audio_radio': ('Wi-Fi e Bluetooth', 'guarded-trial', 'Non spegnere rete attiva, controllo remoto o periferiche necessarie.'),
    'hw_audio_usb_empty_ports': ('USB inutilizzate', 'boot-port-policy', 'All’avvio, se è già presente un DAC USB, disattiva le porte vuote supportate; conserva porte occupate e rispettive porte associate. Il taglio dei 5 V dipende dall’hub.'),
    'hw_audio_rt': ('Priorità audio USB', 'measured-trial', 'Identità del thread e IRQ verificate; latenza non peggiorata, niente saturazione RT.'),
    'hw_hyperthreading': ('Hyper-threading', 'reboot-trial', 'Gruppi fisici completi, housekeeping sufficiente, confronto prima/dopo riavvio.'),
    'hw_wol': ('Wake-on-LAN', 'user-choice', 'Conserva la scelta di accensione remota e verifica supporto adattatore/BIOS.'),
    'hw_t620_usb_idle': ('USB inattive HP t620', 'hardware-specific', 'Vecchia policy di risparmio dei controller secondari, distinta dalla disattivazione delle porte vuote. Solo HP t620 riconosciuti; nessuna applicazione al t630 o riattivazione di hook sostituiti.'),
    'hw_autotune_gui': ('Backend AutoTune', 'user-choice', 'Conserva la scelta di accesso locale; non è un parametro di qualità audio.'),
    'hw_power_button': ('Pulsante di accensione', 'user-choice', 'Conserva il comportamento scelto e la compatibilità con sospensione.'),
    'hw_screen_blank': ('Spegnimento schermo', 'user-choice', 'Preferenza utente; possibile confronto energetico, nessun beneficio audio presunto.'),
    'hw_sata_link_pm': ('Risparmio SATA', 'storage-trial', 'Richiede backup e verifica I/O/errori/riattivazione; nessuna attivazione automatica non collaudata.'),
    'hw_fw_caching': ('Cache firmware', 'memory-trial', 'Richiede RAM disponibile, margine senza swap/OOM, latenza I/O e riavvio verificati.'),
    'hw_hddspindown_new': ('Arresto dischi HDD', 'storage-trial', 'Proteggi libreria e registrazioni; misura ripartenza e continuità prima di cambiare timeout.'),
    'hw_battery_indicator': ('Indicatore batteria', 'user-choice', 'Preferenza disponibile solo su hardware compatibile.'),
}

TRIAL_PARAMETERS={
    'hw_cpufreq':{'governor_search','frequency_limits'},
    'hw_clksrc':{'clocksource'},
    'hw_audio_rt':{'audio_schedule_search','thread_affinity','usb_irq_affinity'},
    'hw_audio_usb_empty_ports':{'empty_usb_pm'},
}


def read_setting(key):
    try:
        result = subprocess.run(['sysvar.sh', key], capture_output=True, text=True, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def report(hardware, reader=read_setting, devpm_root=Path('/run/devpm'),application=None,sys_root=Path('/sys'),native_usb_status=Path('/run/daphile-audio-policy/status.json')):
    rows = []
    for key, (label, strategy, criterion) in CONTROLS.items():
        saved = reader(key)
        applicable = None
        if key == 'hw_t620_usb_idle':
            model = hardware.get('system', {}).get('product_name')
            applicable = model in ('HP t620 Thin Client','HP t620 Quad Core TC') if model else None
        if key=='hw_hyperthreading':
            cpu=hardware.get('cpu',{});groups={}
            for core in cpu.get('cores',[]):
                topology=core.get('topology',{});identity=(topology.get('physical_package_id'),topology.get('core_id'))
                if None not in identity:groups.setdefault(identity,set()).add(core['cpu'])
            if cpu.get('smt') is False or (groups and all(len(g)==1 for g in groups.values())):applicable=False
        evidence={}
        if key=='hw_audio_usb_empty_ports':
            evidence.update(disabled_ports=[],unsupported_controls=[],ports_readback={},port_readback_errors=[])
            # The native status file records the LAST invocation's actions.
            # Its next idle pass empties actions without undoing earlier gates.
            # Inventory effective sysfs values independently of that log.
            for control in sorted((Path(sys_root)/'bus/usb/devices').glob('*/*-port*/disable')):
                try:
                    resolved=control.resolve(strict=True)
                    if not resolved.is_relative_to(Path(sys_root).resolve()):raise ValueError('Outside sysfs')
                    value=control.read_text().strip()
                    if value not in ('0','1'):raise ValueError('Unknown port state')
                    evidence['ports_readback'][str(resolved)]=value
                    if value=='1':evidence['disabled_ports'].append(str(resolved))
                except (OSError,ValueError):evidence['port_readback_errors'].append(str(control))
            try:
                path=Path(native_usb_status)
                if path.is_symlink():raise ValueError('Indirect native USB status')
                native=json.loads(path.read_text())
                for action in native.get('actions',[]):
                    control=Path(action.get('path',''))
                    if (control.name=='disable' and re.fullmatch(r'.*-port[0-9]+',control.parent.name)
                            and control.resolve().is_relative_to(Path(sys_root).resolve())
                            and action.get('current')=='1' and control.read_text().strip()=='1'):
                        if str(control.resolve()) not in evidence['disabled_ports']:evidence['disabled_ports'].append(str(control.resolve()))
                evidence['unsupported_controls']=native.get('unsupported_ports',[])
                evidence['diagnostic_hold']=native.get('usb_diagnostic_hold') is True
            except (OSError,ValueError,TypeError):pass
        if key=='hw_battery_indicator':
            evidence['battery_devices']=[p.name for p in (Path(sys_root)/'class/power_supply').glob('BAT*')]
            applicable=bool(evidence['battery_devices'])
        if key=='hw_audio_radio':
            evidence['rfkill_devices']=[p.name for p in (Path(sys_root)/'class/rfkill').glob('rfkill*')]
            # Absence is authoritative only with a readable Linux sysfs.
            if (Path(sys_root)/'class').is_dir():applicable=bool(evidence['rfkill_devices'])
        if key=='hw_sata_link_pm':
            evidence['policies']={};observable=True
            for p in (Path(sys_root)/'class/scsi_host').glob('host*/link_power_management_policy'):
                try:evidence['policies'][str(p)]=p.read_text().strip()
                except OSError:observable=False
            if observable and (Path(sys_root)/'class').is_dir():applicable=bool(evidence['policies'])
        if key=='hw_hddspindown_new':
            evidence['rotational_devices']=[];observed=False
            for p in (Path(sys_root)/'block').glob('*/queue/rotational'):
                try:
                    rotational=p.read_text().strip()
                    if rotational in ('0','1'):observed=True
                    if rotational=='1':evidence['rotational_devices'].append(p.parent.parent.name)
                except OSError:pass
            if observed:applicable=bool(evidence['rotational_devices'])
        if key=='hw_clksrc':evidence['runtime']=hardware.get('cpu',{}).get('clocksource',{})
        if key=='hw_cpufreq':
            evidence['governors']=sorted({c.get('cpufreq',{}).get('scaling_governor') for c in hardware.get('cpu',{}).get('cores',[]) if c.get('cpufreq',{}).get('scaling_governor')})
        applied=bool(application and application.get('status')=='committed' and any(c.get('verified') is True and c.get('parameter') in TRIAL_PARAMETERS.get(key,set()) for c in application.get('changes',[])))
        if applicable is False:decision='Non applicabile a questo hardware'
        elif strategy=='user-choice':decision='Conservato: preferenza utente'
        elif saved is None:decision='Conservato: valore non osservabile'
        elif applied:decision='Applicato dopo confronto e verifica'
        elif key in TRIAL_PARAMETERS:decision='Conservato salvo beneficio misurato nella ricerca'
        else:decision='Conservato: collaudo specifico necessario'
        if key=='hw_audio_usb_empty_ports':
            applied=bool(evidence['disabled_ports'])
            if applied:
                decision=str(len(evidence['disabled_ports']))+' porte logiche disattivate e rilette in questo avvio'
                if evidence['unsupported_controls']:decision+='; '+str(len(evidence['unsupported_controls']))+' controlli non disponibili'
                if evidence.get('diagnostic_hold'):decision+='; sospensione diagnostica richiesta ma ripristino porte incompleto'
            elif evidence.get('diagnostic_hold'):
                decision='Regola temporaneamente sospesa per diagnosi; preferenza POWER conservata'
                if not evidence['ports_readback'] or evidence['port_readback_errors']:decision+='; stato completo delle porte non verificato'
            elif saved in ('on','enable'):decision='Previsto all’avvio: disattiva le porte vuote supportate se è già presente un DAC USB'
            else:decision='Disattivazione automatica delle porte non abilitata'
        rows.append(dict(key=key, label=label, saved_value=saved,
                         observation='saved-only' if saved is not None else 'unavailable',
                         applicable=applicable, strategy=strategy, criterion=criterion,
                         decision=decision, applied=applied,persisted=False,
                         measured_adapters=sorted(TRIAL_PARAMETERS.get(key,set())),runtime_evidence=evidence))
    for bus in ('pci', 'usb'):
        for path in sorted((Path(devpm_root) / bus).glob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                saved, node, label = path.read_text().strip().split(';', 2)
                target = Path(node)
                current = target.read_text().strip() if target.is_file() and target.resolve().is_relative_to('/sys') else None
            except (OSError, ValueError):
                saved = current = None; label = path.name
            rows.append(dict(key=path.name, label=label, bus=bus, saved_value=saved,
                             observed_value=current, observation='runtime-readback' if current is not None else 'unavailable',
                             strategy='guarded-device-trial', criterion='Identità stabile, ruoli DAC/rete/storage protetti, errori e resume verificati.',
                             decision='preserve-pending-evidence', applied=False))
    return dict(status='reviewed', settings=rows, writes_performed=False,
                optimization_completed=False, decision_coverage_complete=True,
                measured_optimization_coverage_complete=False,sound_quality_claim=False,
                message='Tutti i controlli POWER hanno un criterio esplicito. Le preferenze restano conservate; i cambiamenti tecnici richiedono prove dedicate.')

__release_signature__ = 'Tino Zappala'
