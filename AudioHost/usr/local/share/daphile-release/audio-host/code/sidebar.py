"""Read-only native enable state and Daphile navigation, independent of audio."""
import json
from pathlib import Path

MARKER='data-daphile-audio-sidebar'
TAG="<script defer src='/cgi-bin/AudioPlugins/sidebar.js?v=1' "+MARKER+"></script>\n"

def configure(root):
 """Publish only two native filesystem paths; keep the host config private."""
 from common import atomic
 root=Path(root);config=json.loads((root/'config.json').read_text())
 value={k:config[k] for k in ('prefs','plugins')}
 if any(not isinstance(v,str) or not Path(v).is_absolute() for v in value.values()):raise RuntimeError('Invalid native navigation paths')
 target=root/'navigation-config.json'
 if target.is_symlink():raise RuntimeError('Indirect navigation config refused')
 atomic(target,value);target.chmod(0o644)

def enabled_modules(root):
 import yaml
 root=Path(root);config=json.loads((root/'navigation-config.json').read_text())
 prefs=Path(config['prefs']);plugins=Path(config['plugins'])
 if prefs.is_symlink():raise RuntimeError('Indirect native preferences refused')
 try:states=yaml.safe_load(prefs.read_text()) if prefs.exists() else {}
 except yaml.YAMLError as exc:raise RuntimeError('Invalid native plugin state') from exc
 if not isinstance(states,dict):raise RuntimeError('Invalid native plugin state')
 names=[]
 for name in ('DaphileRME','DaphileCamillaDSP'):
  source=plugins/name
  if states.get(name)=='enabled' and source.is_dir() and not source.is_symlink() and (source/'install.xml').is_file() and not (source/'install.xml').is_symlink():names.append(name)
 return names

SCRIPT=r'''(function(){
'use strict';
if(window.__daphileAudioSidebar)return;window.__daphileAudioSidebar=true;
const defs={
 DaphileRME:{label:'RME',title:'RME · controllo DAC',url:'/cgi-bin/RMEPlugin/',color:'#67dfc6',icon:'<rect x="4" y="8" width="32" height="24" rx="4"/><path d="M9 15h9M9 20h6M9 25h9"/><circle cx="28" cy="20" r="5"/><path d="M28 15v4"/>'},
 DaphileCamillaDSP:{label:'CamillaDSP',title:'CamillaDSP · equalizzatore software',url:'/cgi-bin/CamillaPlugin/',color:'#c2a9ff',icon:'<path d="M4 30h32M7 31V7M7 24c5 0 4-13 10-13s5 14 11 14 5-6 8-6"/><circle cx="17" cy="11" r="2.6"/><circle cx="28" cy="25" r="2.6"/>'}
};
const css='#header-menu .daphile-audio-shortcut{height:56px;display:inline-flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;text-decoration:none;vertical-align:top;padding:3px 0;color:var(--audio-accent)}#header-menu .daphile-audio-shortcut svg{width:31px;height:31px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}#header-menu .daphile-audio-shortcut .menutitle{position:static;display:block;line-height:13px;font-size:10px;width:auto;color:inherit}#header-menu .daphile-audio-shortcut:hover{background:rgba(130,130,150,.18);border-color:var(--audio-accent)}#header-menu .daphile-audio-shortcut:focus-visible{outline:2px solid var(--audio-accent);outline-offset:-3px}body:not(.left-menu) #header-menu .daphile-audio-shortcut{height:70px}#header-menu .daphile-audio-logo-flow{position:relative;float:left;top:auto;bottom:auto;margin-top:8px}';
let busy=false,timer=null,last='';
function layout(menu){menu.style.overflowY='';const logo=menu.querySelector('.logo');if(!logo)return;logo.classList.remove('daphile-audio-logo-flow');const buttons=menu.querySelectorAll('[data-audio-module]');if(!document.body.classList.contains('left-menu')||!buttons.length)return;const bottom=buttons[buttons.length-1].getBoundingClientRect().bottom;if(bottom+5>logo.getBoundingClientRect().top){logo.classList.add('daphile-audio-logo-flow');menu.style.overflowY='auto';}}
function render(names){const menu=document.getElementById('header-menu');if(!menu)return;const shutdown=Array.from(menu.children).find(x=>x.getAttribute('onclick')?.includes("Shutdown('dialog')"));if(!shutdown)return;const selected=Object.keys(defs).filter(n=>names.includes(n)),key=selected.join(',');if(key===last){layout(menu);return;}last=key;
 menu.querySelectorAll('[data-audio-module]').forEach(x=>x.remove());let after=shutdown;
 for(const name of selected){const d=defs[name],a=document.createElement('a');a.className='menuitem daphile-audio-shortcut';a.dataset.audioModule=name;a.href=d.url;a.target='_blank';a.rel='noopener';a.title=d.title;a.setAttribute('aria-label',d.title);a.style.setProperty('--audio-accent',d.color);a.innerHTML='<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">'+d.icon+'</svg><span class="menutitle">'+d.label+'</span>';after.insertAdjacentElement('afterend',a);after=a;}
 layout(menu);
}
async function refresh(){if(busy||document.hidden)return;busy=true;const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);try{const response=await fetch('/cgi-bin/AudioPlugins/navigation.json',{cache:'no-store',signal:controller.signal});if(!response.ok)return;const state=await response.json();if(Array.isArray(state.modules))render(state.modules);}catch{}finally{clearTimeout(timeout);busy=false;}}
function start(){if(!document.getElementById('header-menu'))return;const style=document.createElement('style');style.id='daphile-audio-sidebar-style';style.textContent=css;document.head.append(style);refresh();timer=setInterval(refresh,3000);}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});window.addEventListener('resize',()=>{const menu=document.getElementById('header-menu');if(menu)layout(menu);});window.addEventListener('pagehide',()=>{clearInterval(timer);timer=null;});window.addEventListener('pageshow',e=>{if(e.persisted){clearInterval(timer);timer=setInterval(refresh,3000);refresh();}});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
'''
