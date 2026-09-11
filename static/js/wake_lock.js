/* wake_lock.js — Maintien d'écran allumé partagé (modes cuisine + épicerie).
 *
 * Un appel par page : `window.RecetteWakeLock.init({...})` verrouille
 * l'écran, suit la visibilité et notifie via les callbacks.
 * `statusEl` est optionnel : sans lui (slides), le mode est silencieux.
 */
window.RecetteWakeLock = (function () {
    'use strict';

    function init(options) {
        options = options || {};
        var statusEl = options.statusEl || null;
        var sentinel = null;

        function showStatus(msg) {
            if (!statusEl) return;
            statusEl.textContent = msg;
            statusEl.hidden = false;
        }

        function hideStatus() {
            if (!statusEl) return;
            statusEl.hidden = true;
            statusEl.textContent = '';
        }

        async function request() {
            if (!('wakeLock' in navigator)) {
                showStatus(options.unsupportedMsg);
                return;
            }
            try {
                sentinel = await navigator.wakeLock.request('screen');
                sentinel.addEventListener('release', function () {
                    if (document.visibilityState !== 'visible') {
                        showStatus(options.releasedMsg);
                    }
                });
                hideStatus();
            } catch (e) {
                showStatus(options.unsupportedMsg);
            }
        }

        async function release() {
            if (sentinel) {
                try { await sentinel.release(); } catch (e) {}
                sentinel = null;
            }
        }

        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') {
                request();
                if (options.onVisible) options.onVisible();
            } else {
                release();
                if (options.onHidden) options.onHidden();
            }
        });

        window.addEventListener('pagehide', function () {
            release();
            if (options.onHidden) options.onHidden();
        });

        request();
        return { request: request, release: release };
    }

    return { init: init };
})();
