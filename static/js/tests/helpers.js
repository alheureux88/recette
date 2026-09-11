/* Harness UMD-free : charge les scripts statiques dans un bac à sable `vm`
 * avec un DOM/stockage factices. Aucune dépendance (node:test suffit). */
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_DIR = path.join(__dirname, '..');

function makeClassList() {
    const set = new Set();
    return {
        add(...cls) { cls.forEach((c) => set.add(c)); },
        remove(...cls) { cls.forEach((c) => set.delete(c)); },
        contains(c) { return set.has(c); },
        _set: set,
    };
}

function makeButton() {
    return {
        textContent: '',
        _handlers: {},
        addEventListener(ev, fn) {
            (this._handlers[ev] = this._handlers[ev] || []).push(fn);
        },
        click(extra = {}) {
            const evt = { preventDefault() {}, stopPropagation() {}, ...extra };
            (this._handlers.click || []).forEach((fn) => fn(evt));
        },
    };
}

/** Faux widget `.cook-timer` avec les 5 hooks data-role. */
function makeTimerEl({ stepIndex = '0', duration = '300' } = {}) {
    const roles = {
        remaining: makeButton(),
        'start-pause': makeButton(),
        reset: makeButton(),
        minus: makeButton(),
        plus: makeButton(),
    };
    return {
        dataset: { stepIndex: String(stepIndex), defaultDuration: String(duration) },
        classList: makeClassList(),
        querySelector(sel) {
            const m = /\[data-role="([^"]+)"\]/.exec(sel);
            return m ? roles[m[1]] || null : null;
        },
        _roles: roles,
    };
}

function makeStore(initial = {}) {
    const data = { ...initial };
    return {
        getItem: (k) => (k in data ? data[k] : null),
        setItem: (k, v) => { data[k] = String(v); },
        removeItem: (k) => { delete data[k]; },
        _data: data,
    };
}

/** Charge timers.js ; renvoie { RecetteTimers, store, intervals, sandbox }. */
function loadTimers({ elements = [], storage = {}, notification, vibrate } = {}) {
    const store = makeStore(storage);
    const intervals = new Map();
    let nextId = 1;
    const navigator = {};
    if (vibrate !== undefined) navigator.vibrate = vibrate;
    const sandbox = {
        console,
        Date,
        setInterval: (fn) => { const id = nextId++; intervals.set(id, fn); return id; },
        clearInterval: (id) => { intervals.delete(id); },
        localStorage: store,
        navigator,
        document: { visibilityState: 'visible', addEventListener() {} },
    };
    sandbox.window = sandbox;
    if (notification !== undefined) sandbox.Notification = notification;
    sandbox.scope = { querySelectorAll: () => elements };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.join(JS_DIR, 'timers.js'), 'utf8'), sandbox);
    return { RecetteTimers: sandbox.RecetteTimers, store, intervals, sandbox };
}

const LABELS = { start: 'Start', pause: 'Pause', resume: 'Resume', done: "Time's up!" };

function makeManager(RecetteTimers, overrides = {}) {
    return RecetteTimers.createManager({
        scope: { querySelectorAll: () => [] },
        storageKey: 'recette:timers:1',
        recipeId: 1,
        labels: LABELS,
        detailedNotifications: true,
        notificationIcon: '/static/icon-192.png',
        notificationBadge: '/static/favicon-32x32.png',
        ...overrides,
    });
}

module.exports = { makeClassList, makeButton, makeTimerEl, makeStore, loadTimers, LABELS, makeManager };
