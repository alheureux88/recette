/* Tests du helper wake-lock — node:test, sans dépendance. */
'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadWakeLock({ wakeLock, statusEl } = {}) {
    const docHandlers = {};
    const winHandlers = {};
    const navigator = {};
    if (wakeLock !== undefined) navigator.wakeLock = wakeLock;
    const sandbox = {
        console,
        document: {
            visibilityState: 'visible',
            addEventListener: (ev, fn) => { docHandlers[ev] = fn; },
        },
        navigator,
        addEventListener: (ev, fn) => { winHandlers[ev] = fn; },
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '..', 'wake_lock.js'), 'utf8'),
        sandbox
    );
    return {
        RecetteWakeLock: sandbox.RecetteWakeLock,
        docHandlers,
        winHandlers,
        document: sandbox.document,
    };
}

function makeStatusEl() {
    return { textContent: '', hidden: true };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

describe('wake-lock', () => {
    it('silencieux sans support ni statut : ne lève pas', async () => {
        const { RecetteWakeLock } = loadWakeLock();
        const api = RecetteWakeLock.init({});
        await flush();
        assert.ok(api.request && api.release);
    });

    it('demande le verrouillage et le relâche', async () => {
        let released = false;
        const fakeSentinel = {
            addEventListener() {},
            release: async () => { released = true; },
        };
        let requested = null;
        const { RecetteWakeLock } = loadWakeLock({
            wakeLock: { request: async (kind) => { requested = kind; return fakeSentinel; } },
        });
        const api = RecetteWakeLock.init({});
        await flush();
        assert.equal(requested, 'screen');
        await api.release();
        assert.equal(released, true);
    });

    it('affiche un message quand non supporté', async () => {
        const statusEl = makeStatusEl();
        const { RecetteWakeLock } = loadWakeLock({ statusEl });
        RecetteWakeLock.init({ statusEl, unsupportedMsg: 'Non supporté' });
        await flush();
        assert.equal(statusEl.hidden, false);
        assert.equal(statusEl.textContent, 'Non supporté');
    });

    it('rappelle onVisible/onHidden aux changements de visibilité', async () => {
        const calls = [];
        const loaded = loadWakeLock({
            wakeLock: { request: async () => ({ addEventListener() {}, release: async () => {} }) },
        });
        loaded.RecetteWakeLock.init({
            onVisible: () => calls.push('visible'),
            onHidden: () => calls.push('hidden'),
        });
        await flush();
        loaded.document.visibilityState = 'hidden';
        loaded.docHandlers.visibilitychange();
        loaded.document.visibilityState = 'visible';
        loaded.docHandlers.visibilitychange();
        loaded.winHandlers.pagehide();
        assert.deepEqual(calls, ['hidden', 'visible', 'hidden']);
    });
});
