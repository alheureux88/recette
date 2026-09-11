/* Tests du moteur minuteurs partagé — node:test, sans dépendance. */
'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { loadTimers, makeManager, makeTimerEl, LABELS } = require('./helpers');

function setup({ duration = '300', storage, notification, managerOpts } = {}) {
    const el = makeTimerEl({ duration });
    const loaded = loadTimers({ elements: [el], storage, notification });
    const manager = makeManager(loaded.RecetteTimers, {
        scope: { querySelectorAll: () => [el] },
        ...managerOpts,
    });
    manager.init();
    return { el, manager, ...loaded };
}

describe('formatTime', () => {
    it('formate les secondes et minutes', () => {
        const { RecetteTimers } = loadTimers();
        assert.equal(RecetteTimers.formatTime(0), '00:00');
        assert.equal(RecetteTimers.formatTime(45), '00:45');
        assert.equal(RecetteTimers.formatTime(300), '05:00');
    });

    it('bascule en H:MM:SS au-delà d\u2019une heure et borne le négatif', () => {
        const { RecetteTimers } = loadTimers();
        assert.equal(RecetteTimers.formatTime(3600), '1:00:00');
        assert.equal(RecetteTimers.formatTime(3723), '1:02:03');
        assert.equal(RecetteTimers.formatTime(-10), '00:00');
    });
});

describe('cycle de vie', () => {
    it('affiche la durée formatée à l\u2019init', () => {
        const { el } = setup({ duration: '300' });
        assert.equal(el._roles.remaining.textContent, '05:00');
    });

    it('démarre, décrémente puis termine', () => {
        const { el, manager, intervals } = setup({ duration: '3' });
        const t = manager.timers['0'];
        el._roles['start-pause'].click();
        assert.equal(t.running, true);
        assert.ok(el.classList.contains('cook-timer--running'));
        assert.equal(el._roles['start-pause'].textContent, LABELS.pause);

        const tick = [...intervals.values()][0];
        tick(); tick();
        assert.equal(el._roles.remaining.textContent, '00:01');
        tick();
        assert.equal(t.running, false);
        assert.ok(el.classList.contains('cook-timer--done'));
        assert.equal(el._roles['start-pause'].textContent, LABELS.start);
        // Un seul intervalle actif à la fois : terminé et nettoyé.
        assert.equal(intervals.size, 0);
    });

    it('pause et reprise via le même bouton', () => {
        const { el, manager, intervals } = setup({ duration: '60' });
        const t = manager.timers['0'];
        el._roles['start-pause'].click();
        el._roles['start-pause'].click();
        assert.equal(t.running, false);
        assert.equal(el._roles['start-pause'].textContent, LABELS.resume);
        assert.equal(intervals.size, 0);
        el._roles['start-pause'].click();
        assert.equal(t.running, true);
    });

    it('reset restaure la durée par défaut', () => {
        const { el, manager } = setup({ duration: '60' });
        const t = manager.timers['0'];
        el._roles['start-pause'].click();
        el._roles.reset.click();
        assert.equal(t.remainingSeconds, 60);
        assert.equal(t.running, false);
        assert.equal(el._roles.remaining.textContent, '01:00');
    });

    it('ajuste ±60s sans passer sous zéro', () => {
        const { el, manager } = setup({ duration: '30' });
        const t = manager.timers['0'];
        el._roles.plus.click();
        assert.equal(t.remainingSeconds, 90);
        el._roles.minus.click();
        el._roles.minus.click();
        assert.equal(t.remainingSeconds, 0);
        el._roles.minus.click();
        assert.equal(t.remainingSeconds, 0);
    });

    it('ne démarre pas à zéro', () => {
        const { el, manager, intervals } = setup({ duration: '0' });
        el._roles['start-pause'].click();
        assert.equal(manager.timers['0'].running, false);
        assert.equal(intervals.size, 0);
    });
});

describe('régression démarrage (Notification sans promesse)', () => {
    it('démarre même si requestPermission renvoie undefined', () => {
        // Bug réel : `.then()` sur undefined levait et avortait le démarrage.
        const { el, manager } = setup({
            duration: '60',
            notification: { permission: 'default', requestPermission: () => undefined },
        });
        el._roles['start-pause'].click();
        assert.equal(manager.timers['0'].running, true);
        assert.ok(el.classList.contains('cook-timer--running'));
    });
});

describe('persistance', () => {
    it('sauvegarde et restaure l\u2019état au repos', () => {
        const first = setup({ duration: '300' });
        first.el._roles.plus.click(); // 360s
        first.manager.saveStates();
        const raw = first.store._data['recette:timers:1'];
        assert.ok(raw.includes('360'));

        const second = setup({ duration: '300', storage: { 'recette:timers:1': raw } });
        second.manager.restoreAll();
        assert.equal(second.manager.timers['0'].remainingSeconds, 360);
        assert.equal(second.el._roles.remaining.textContent, '06:00');
    });

    it('reprend un minuteur en cours via endTimestamp', () => {
        const future = Date.now() + 120000;
        const { manager } = setup({
            duration: '300',
            storage: {
                'recette:timers:1': JSON.stringify({
                    0: { remainingSeconds: 120, running: true, endTimestamp: future },
                }),
            },
        });
        manager.restoreAll();
        const t = manager.timers['0'];
        assert.equal(t.running, true);
        assert.ok(t.remainingSeconds > 100 && t.remainingSeconds <= 120);
    });

    it('resetAll vide le stockage', () => {
        const first = setup({ duration: '300' });
        first.manager.saveStates();
        first.manager.resetAll();
        assert.ok(!('recette:timers:1' in first.store._data));
    });
});

describe('callbacks spécifiques', () => {
    it('notifie push au démarrage et à l\u2019ajustement en cours', () => {
        const calls = [];
        const { el } = setup({
            duration: '120',
            managerOpts: {
                onStart: (t) => calls.push(['start', t.remainingSeconds]),
                onAdjust: (t) => { if (t.running) calls.push(['reschedule']); },
            },
        });
        el._roles['start-pause'].click();
        assert.deepEqual(calls, [['start', 120]]);
        el._roles.plus.click();
        assert.deepEqual(calls, [['start', 120], ['reschedule']]);
    });

    it('signale chaque fin une seule fois', () => {
        let done = 0;
        const { el, intervals } = setup({ duration: '1', managerOpts: { onDone: () => { done++; } } });
        el._roles['start-pause'].click();
        [...intervals.values()][0](); // tick → 0 : fin
        assert.equal(done, 1);
    });
});
