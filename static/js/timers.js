/* timers.js — Moteur minuteurs partagé (page recette + modes cuisine).
 *
 * Un manager par page pilote tous les `.cook-timer` du scope :
 * compte à rebours, son, vibration, notification locale et persistance
 * localStorage (clé `recette:timers:<recipe_id>`, partagée entre les vues).
 *
 * Les comportements spécifiques passent par callbacks :
 * `onStart/onPause/onReset` (push serveur en mode classique),
 * `onTick/onDone` (chips du slideshow), `trackStarted` (pastilles slides).
 *
 * Les libellés traduits et l'ID recette sont fournis par chaque page
 * (les fichiers statiques ne passent pas par Jinja).
 */
window.RecetteTimers = (function () {
    'use strict';

    function pad(n) {
        return (n < 10 ? '0' : '') + n;
    }

    function formatTime(totalSeconds) {
        if (totalSeconds < 0) totalSeconds = 0;
        var h = Math.floor(totalSeconds / 3600);
        var m = Math.floor((totalSeconds % 3600) / 60);
        var s = totalSeconds % 60;
        if (h > 0) return h + ':' + pad(m) + ':' + pad(s);
        return pad(m) + ':' + pad(s);
    }

    function requestNotificationPermission() {
        // Certains navigateurs renvoient undefined au lieu d'une promesse :
        // sans garde, le .then() levait et le démarrage avortait.
        if (!('Notification' in window) || Notification.permission !== 'default') return;
        try {
            var req = Notification.requestPermission();
            if (req && typeof req.then === 'function') {
                req.then(function () {});
            }
        } catch (e) {}
    }

    function playTimerSound() {
        try {
            var audioContext = new (window.AudioContext || window.webkitAudioContext)();
            var oscillator = audioContext.createOscillator();
            var gainNode = audioContext.createGain();

            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);

            oscillator.frequency.value = 800;
            oscillator.type = 'sine';

            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);

            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.5);
        } catch (e) {
            console.warn('Could not play timer sound:', e);
        }
    }

    function vibrateTimer(pattern) {
        try {
            if ('vibrate' in navigator) {
                navigator.vibrate(pattern || [200, 100, 200, 100, 200]);
            }
        } catch (e) {
            console.warn('Could not vibrate:', e);
        }
    }

    function showTimerNotification(t, opts) {
        try {
            if (!('Notification' in window)) return;
            if (Notification.permission === 'granted') {
                var stepNum = t.index + 1;
                var payload;
                if (opts.detailedNotifications) {
                    payload = {
                        body: opts.labels.done + ' - ' + stepNum,
                        icon: opts.notificationIcon,
                        badge: opts.notificationBadge,
                        tag: 'timer-' + opts.recipeId + '-' + stepNum,
                        requireInteraction: true
                    };
                } else {
                    payload = { tag: 'timer-' + opts.recipeId };
                }
                new Notification(opts.labels.done, payload);
            } else if (Notification.permission !== 'denied' && opts.detailedNotifications) {
                requestNotificationPermission();
            }
        } catch (e) {
            console.warn('Notification error:', e);
        }
    }

    function createManager(opts) {
        var scope = opts.scope || document;
        var timers = {};
        var containers = [];

        function loadStates() {
            try {
                var raw = localStorage.getItem(opts.storageKey);
                if (!raw) return {};
                var data = JSON.parse(raw);
                return (data && typeof data === 'object') ? data : {};
            } catch (e) { return {}; }
        }

        function saveStates() {
            var states = {};
            containers.forEach(function (el) {
                var key = el.dataset.stepIndex;
                var t = timers[key];
                if (!t) return;
                var state = {
                    remainingSeconds: t.remainingSeconds,
                    running: t.running,
                    endTimestamp: t.running ? (Date.now() + t.remainingSeconds * 1000) : null
                };
                if (opts.trackStarted) {
                    state.started = t.started;
                    state.done = t.done;
                }
                states[key] = state;
            });
            try { localStorage.setItem(opts.storageKey, JSON.stringify(states)); } catch (e) {}
        }

        function notifyDone(t) {
            playTimerSound();
            vibrateTimer(opts.vibratePattern);
            showTimerNotification(t, opts);
            t.remainingEl.textContent = formatTime(0);
            t.el.classList.add('cook-timer--done');
            if (opts.trackStarted) t.done = true;
            if (opts.onDone) opts.onDone(t);
        }

        function startTimer(t) {
            if (t.remainingSeconds <= 0) return;
            requestNotificationPermission();
            t.running = true;
            t.doneNotified = false;
            if (opts.trackStarted) {
                t.started = true;
                t.done = false;
            }
            t.startPauseBtn.textContent = opts.labels.pause;
            t.el.classList.add('cook-timer--running');
            t.el.classList.remove('cook-timer--done');
            t.intervalId = setInterval(function () {
                t.remainingSeconds--;
                t.remainingEl.textContent = formatTime(t.remainingSeconds);
                if (opts.onTick) opts.onTick(t);
                if (t.remainingSeconds <= 0) {
                    clearInterval(t.intervalId);
                    t.intervalId = null;
                    t.running = false;
                    t.startPauseBtn.textContent = opts.labels.start;
                    t.el.classList.remove('cook-timer--running');
                    if (!t.doneNotified) {
                        t.doneNotified = true;
                        notifyDone(t);
                    }
                    saveStates();
                }
            }, 1000);
            saveStates();
            if (opts.onStart) opts.onStart(t);
        }

        function pauseTimer(t) {
            t.running = false;
            if (t.intervalId) {
                clearInterval(t.intervalId);
                t.intervalId = null;
            }
            t.startPauseBtn.textContent = opts.labels.resume;
            t.el.classList.remove('cook-timer--running');
            saveStates();
            if (opts.onPause) opts.onPause(t);
        }

        function resetTimer(t) {
            if (t.intervalId) {
                clearInterval(t.intervalId);
                t.intervalId = null;
            }
            t.running = false;
            if (opts.trackStarted) {
                t.started = false;
                t.done = false;
            }
            t.remainingSeconds = t.defaultDuration;
            t.doneNotified = false;
            t.remainingEl.textContent = formatTime(t.remainingSeconds);
            t.startPauseBtn.textContent = opts.labels.start;
            t.el.classList.remove('cook-timer--running', 'cook-timer--done');
            saveStates();
            if (opts.onReset) opts.onReset(t);
        }

        function adjustTimer(t, deltaSeconds) {
            t.remainingSeconds = Math.max(0, t.remainingSeconds + deltaSeconds);
            t.remainingEl.textContent = formatTime(t.remainingSeconds);
            if (t.remainingSeconds > 0) {
                t.el.classList.remove('cook-timer--done');
                if (!opts.trackStarted) t.doneNotified = false;
            }
            saveStates();
            if (opts.onTick) opts.onTick(t);
            if (opts.onAdjust) opts.onAdjust(t);
        }

        function restoreTimer(key, state) {
            var t = timers[key];
            if (!t || !state) return;
            if (state.endTimestamp && state.running) {
                var remaining = Math.round((state.endTimestamp - Date.now()) / 1000);
                if (remaining <= 0) {
                    t.remainingSeconds = 0;
                    t.remainingEl.textContent = formatTime(0);
                    t.el.classList.add('cook-timer--done');
                    if (opts.trackStarted) t.started = true;
                    t.doneNotified = false;
                    notifyDone(t);
                    t.doneNotified = true;
                } else {
                    t.remainingSeconds = remaining;
                    t.remainingEl.textContent = formatTime(remaining);
                    startTimer(t);
                }
            } else {
                t.remainingSeconds = state.remainingSeconds || t.defaultDuration;
                t.remainingEl.textContent = formatTime(t.remainingSeconds);
                if (opts.trackStarted) {
                    t.started = !!(state.started || t.remainingSeconds !== t.defaultDuration);
                    t.done = !!state.done && t.remainingSeconds <= 0;
                    if (t.done) t.el.classList.add('cook-timer--done');
                }
            }
        }

        function restoreAll() {
            var states = loadStates();
            Object.keys(states).forEach(function (key) {
                restoreTimer(key, states[key]);
            });
        }

        function resetAll() {
            Object.keys(timers).forEach(function (key) {
                resetTimer(timers[key]);
            });
            try { localStorage.removeItem(opts.storageKey); } catch (e) {}
        }

        function init() {
            containers = Array.prototype.slice.call(scope.querySelectorAll('.cook-timer'));
            containers.forEach(function (el) {
                var key = el.dataset.stepIndex;
                var defaultDuration = parseInt(el.dataset.defaultDuration, 10) || 0;
                var t = {
                    el: el,
                    key: key,
                    index: parseInt(key, 10),
                    remainingEl: el.querySelector('[data-role="remaining"]'),
                    startPauseBtn: el.querySelector('[data-role="start-pause"]'),
                    defaultDuration: defaultDuration,
                    remainingSeconds: defaultDuration,
                    running: false,
                    intervalId: null,
                    doneNotified: false,
                    started: false,
                    done: false
                };
                timers[key] = t;
                t.remainingEl.textContent = formatTime(t.remainingSeconds);

                t.startPauseBtn.addEventListener('click', function () {
                    if (t.running) {
                        pauseTimer(t);
                    } else {
                        startTimer(t);
                    }
                });

                var resetBtn = el.querySelector('[data-role="reset"]');
                if (resetBtn) {
                    resetBtn.addEventListener('click', function (e) {
                        e.stopPropagation();
                        e.preventDefault();
                        resetTimer(t);
                    });
                }

                var minusBtn = el.querySelector('[data-role="minus"]');
                if (minusBtn) {
                    minusBtn.addEventListener('click', function () {
                        adjustTimer(t, -60);
                    });
                }

                var plusBtn = el.querySelector('[data-role="plus"]');
                if (plusBtn) {
                    plusBtn.addEventListener('click', function () {
                        adjustTimer(t, 60);
                    });
                }
            });
        }

        return {
            timers: timers,
            formatTime: formatTime,
            init: init,
            restoreAll: restoreAll,
            resetAll: resetAll,
            saveStates: saveStates,
            startTimer: startTimer,
            pauseTimer: pauseTimer,
            resetTimer: resetTimer,
            adjustTimer: adjustTimer
        };
    }

    return {
        formatTime: formatTime,
        createManager: createManager
    };
})();
