import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const [jsPath, htmlPath] = process.argv.slice(2);
const js = readFileSync(jsPath, 'utf8');
const html = readFileSync(htmlPath, 'utf8');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);

class Element {
  constructor() { this.parts = []; this.value = ''; }
  set textContent(text) { this.value = text; this.parts = []; }
  get textContent() { return [this.value, ...this.parts.map(item => item.textContent)].join(' '); }
  append(...items) { this.parts.push(...items); }
  replaceChildren(...items) { this.parts = items; this.value = ''; }
  removeAttribute() {}
}

async function launch(result) {
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  const window = { addEventListener() {} };
  const calls = [];
  const timers = [];
  const jobs = [];
  const sandbox = {
    window, parent: window, location: { origin: 'http://fixture.invalid' }, AbortSignal,
    ResizeObserver: class { observe() {} },
    document: { getElementById: id => nodes[id], createElement: () => new Element(),
      createTextNode: text => ({ textContent: text }), body: new Element() },
    confirm() { throw new Error('A second confirmation was requested'); },
    setTimeout(work) { timers.push(work); },
    async fetch(path, options) {
      assert.equal(path, '/cgi-bin/AutoTune');
      const request = JSON.parse(options.body);
      calls.push(request);
      let reply;
      if (request.action === 'status') reply = { enabled: true, token: 'fixture', jobs: [...jobs] };
      else if (request.action === 'automatic-analyze') {
        jobs.push({ id: 'analysis', action: request.action, result, running: false });
        reply = { id: 'analysis', status: 'started' };
      } else {
        assert.equal(request.action, 'automatic-run');
        reply = { id: 'trial', status: 'started' };
      }
      return { ok: true, async json() { return reply; } };
    }
  };
  runInNewContext(js, sandbox);
  await new Promise(setImmediate);
  await timers.shift()();
  return { nodes, calls };
}

const preparedByClick = await launch({ status: 'analyzed', eligible: false, can_prepare: true,
  reason_code: 'pause-first', eligibility_reason: 'PRIVATE diagnostic in English' });
assert.equal(preparedByClick.nodes.configure.disabled, false);
assert.match(preparedByClick.nodes.result.textContent, /Premi Avvia AutoTune/);
assert.doesNotMatch(preparedByClick.nodes.result.textContent, /PRIVATE diagnostic/);
preparedByClick.nodes.configure.onclick();
await new Promise(setImmediate);
const mutators = preparedByClick.calls.filter(call => call.action === 'automatic-run');
assert.deepEqual(mutators, [{ action: 'automatic-run', confirm_audio: true, prepare_player: true, token: 'fixture' }]);

const scanning = await launch({ status: 'analyzed', eligible: false, can_prepare: false,
  reason_code: 'library-scan', eligibility_reason: 'PRIVATE scanner process diagnostic' });
assert.equal(scanning.nodes.configure.disabled, true);
assert.match(scanning.nodes.result.textContent, /Attendi la fine dell.aggiornamento/);
assert.match(scanning.nodes.result.textContent, /Ricontrolla/);
assert.doesNotMatch(scanning.nodes.result.textContent, /PRIVATE|library-scan/);
assert.match(scanning.nodes.diagnostic.textContent, /PRIVATE scanner/);
console.log('PASS: one click without a modal, readable blocker, diagnostics in advanced controls');
