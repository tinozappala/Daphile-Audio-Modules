(function () {
    'use strict';
    if (window.__daphileRamNavigation) return;
    window.__daphileRamNavigation = true;
    const marker = 'data-daphile-ram-nav';
    const selector = '[' + marker + ']';
    let observedMenu = null, observer = null, queued = false;

    function nativeURL() {
        const url = new URL('/settings/server/ram-nextboot.html', window.location.origin);
        url.protocol = 'http:';
        url.port = '9000';
        return url.href;
    }
    function directChild(menu, node) {
        while (node && node.parentElement !== menu) node = node.parentElement;
        return node;
    }
    function afterItem(menu) {
        const camilla = menu.querySelector('[data-audio-module="DaphileCamillaDSP"]') ||
            menu.querySelector('a[href*="/cgi-bin/CamillaPlugin"]');
        const rme = menu.querySelector('[data-audio-module="DaphileRME"]') ||
            menu.querySelector('a[href*="/cgi-bin/RMEPlugin"]');
        const shutdown = Array.from(menu.children).find(function (node) {
            return (node.getAttribute('onclick') || '').includes('Shutdown(');
        });
        return directChild(menu, camilla || rme || shutdown);
    }
    function layout(menu, link) {
        if (!document.body.classList.contains('left-menu')) return;
        const logo = menu.querySelector('.logo');
        if (logo && link.getBoundingClientRect().bottom + 5 > logo.getBoundingClientRect().top) {
            logo.classList.add('daphile-ram-nav-logo-flow');
            menu.classList.add('daphile-ram-nav-scroll');
        }
    }
    function ensure() {
        queued = false;
        const menu = document.getElementById('header-menu');
        if (!menu) return;
        if (observedMenu !== menu) {
            if (observer) observer.disconnect();
            observer = new MutationObserver(schedule);
            observer.observe(menu, {childList: true});
            observedMenu = menu;
        }
        const anchor = afterItem(menu);
        if (!anchor) return;
        const found = Array.from(menu.querySelectorAll(selector));
        let link = found.shift();
        found.forEach(function (extra) { extra.remove(); });
        if (!link) {
            link = document.createElement('a');
            link.className = 'menuitem daphile-ram-nav';
            link.setAttribute(marker, '1');
            link.target = '_blank';
            link.rel = 'noopener';
            link.title = 'RAM · impostazioni native LMS';
            link.setAttribute('aria-label', 'RAM · impostazioni native LMS');
            link.innerHTML = '<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">' +
                '<rect x="5" y="9" width="30" height="19" rx="2"/>' +
                '<rect x="10" y="14" width="8" height="9" rx="1"/>' +
                '<rect x="22" y="14" width="8" height="9" rx="1"/>' +
                '<path d="M9 28v4m5-4v4m5-4v4m5-4v4m5-4v4"/>' +
                '</svg><span class="menutitle">RAM</span>';
        }
        link.href = nativeURL();
        if (link.previousElementSibling !== anchor) anchor.insertAdjacentElement('afterend', link);
        layout(menu, link);
    }
    function schedule() {
        if (!queued) { queued = true; window.requestAnimationFrame(ensure); }
    }
    function start() {
        if (!document.getElementById('daphile-ram-nav-style')) {
            const style = document.createElement('style');
            style.id = 'daphile-ram-nav-style';
            style.textContent = '#header-menu .daphile-ram-nav{height:56px;display:inline-flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;text-decoration:none;vertical-align:top;padding:3px 0;color:#8edfff}' +
                '#header-menu .daphile-ram-nav svg{width:31px;height:31px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}' +
                '#header-menu .daphile-ram-nav .menutitle{position:static;display:block;line-height:13px;font-size:10px;width:auto;color:inherit}' +
                '#header-menu .daphile-ram-nav:hover{background:rgba(130,130,150,.18)}' +
                '#header-menu .daphile-ram-nav:focus-visible{outline:2px solid currentColor;outline-offset:-3px}' +
                'body:not(.left-menu) #header-menu .daphile-ram-nav{height:70px}' +
                'body.left-menu #header-menu.daphile-ram-nav-scroll{overflow-y:auto!important}' +
                'body.left-menu #header-menu .logo.daphile-ram-nav-logo-flow{position:relative!important;float:left;top:auto!important;bottom:auto!important;margin-top:8px}';
            document.head.appendChild(style);
        }
        ensure();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
    window.addEventListener('resize', schedule);
    window.addEventListener('pageshow', schedule);
    window.addEventListener('pagehide', function () { if (observer) observer.disconnect(); observedMenu = null; });
})();

(function () {
    'use strict';
    if (window.__daphileRamReadingBadge) return;
    window.__daphileRamReadingBadge = true;
    let timer = null, request = null, frame = null, frameDocument = null;
    let nextAllowed = 0, installedController = null, stateKey = null;
    const interval = 5000;
    function hide() {
        try { const cell = frameDocument && frameDocument.getElementById('daphile-ram-reading'); if (cell) cell.hidden = true; } catch (_) {}
    }
    function player() {
        try {
            const w = frame.contentWindow;
            const controller = w.SqueezeJS && w.SqueezeJS.Controller;
            return (controller && controller.getPlayer && controller.getPlayer()) || w.playerid || '';
        } catch (_) { return ''; }
    }
    function visible() {
        return !document.hidden && frame && frame.isConnected && frame.getClientRects().length &&
            getComputedStyle(frame).visibility !== 'hidden' && frameDocument && !frameDocument.hidden;
    }
    function invalidate() {
        hide();
        if (request) request.abort();
    }
    function observeController() {
        const controller = frame.contentWindow.SqueezeJS && frame.contentWindow.SqueezeJS.Controller;
        if (!controller || controller === installedController || !controller.on) return;
        installedController = controller;
        // These are the original SqueezeJS events. A track/player change hides
        // old evidence immediately; events cannot start an unbounded poll loop.
        controller.on('playerselected', invalidate);
        controller.on('playlistchange', invalidate);
        controller.on('playerstatechange', function (value) {
            const loop = value && value.playlist_loop;
            const key = JSON.stringify([player(), value && value.mode, value && value.playlist_cur_index, loop && loop[0] && loop[0].url]);
            if (stateKey !== null && stateKey !== key) invalidate();
            stateKey = key;
        });
    }
    function locate() {
        const current = document.getElementById('sb-iframe');
        if (current !== frame) {
            invalidate(); frame = current; frameDocument = null; installedController = null; stateKey = null;
            if (frame) frame.addEventListener('load', function () { invalidate(); frameDocument = null; installedController = null; stateKey = null; });
        }
        if (!frame) return null;
        try {
            const doc = frame.contentDocument;
            if (!doc) return null;
            if (frameDocument !== doc) { hide(); frameDocument = doc; installedController = null; stateKey = null; }
            const next = doc.getElementById('ctrlNext');
            if (!next || next.tagName !== 'TD') return null;
            let cell = doc.getElementById('daphile-ram-reading');
            if (!cell) {
                cell = doc.createElement('td'); cell.id = 'daphile-ram-reading';
                cell.hidden = true; cell.setAttribute('data-daphile-ram-reading', '1');
                cell.style.cssText = 'white-space:nowrap;padding:0 8px;color:#26633b;font:600 11px/1.2 sans-serif;vertical-align:middle;text-align:right';
                const text = doc.createElement('span'); text.textContent = 'LETTURA DA RAM';
                text.title = 'Il file del brano corrente è su tmpfs nella vista del server musicale';
                text.setAttribute('role', 'status'); cell.appendChild(text);
                next.insertAdjacentElement('afterend', cell);
            }
            observeController();
            return cell;
        } catch (_) { hide(); return null; }
    }
    async function tick() {
        const cell = locate();
        if (!cell || !visible()) { invalidate(); return; }
        if (request || Date.now() < nextAllowed) return;
        nextAllowed = Date.now() + interval;
        const selected = player(), doc = frameDocument;
        const abort = new AbortController(); request = abort;
        const bound = setTimeout(function () { abort.abort(); }, 3500);
        try {
            const url = new URL('/cgi-bin/RamReadStatus', location.origin);
            if (selected) url.searchParams.set('player', selected);
            const response = await fetch(url, {credentials:'omit',cache:'no-store',signal:abort.signal});
            const text = await response.text();
            if (text.length > 4096 || !response.ok) throw Error('STATUS_UNAVAILABLE');
            const value = JSON.parse(text);
            cell.hidden = !(value.reading_ram === true && visible() && doc === frameDocument &&
                selected === player() && (!selected || value.player_id === selected));
        } catch (_) { hide(); }
        finally { clearTimeout(bound); if (request === abort) request = null; }
    }
    function start() { if (!timer) { tick(); timer = setInterval(tick, 1000); } }
    document.addEventListener('visibilitychange', function () { if (document.hidden) invalidate(); else tick(); });
    window.addEventListener('pagehide', function () { invalidate(); if (timer) clearInterval(timer); timer = null; });
    window.addEventListener('pageshow', start);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
    else start();
})();
