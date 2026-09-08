"""Whitelist for vulture dead-code detection.

Symbols listed here are intentionally kept even though vulture
(which only scans `src/`) cannot see their usage:

- `reset_*` helpers: used by the test suite (`conftest.py`, `test_poller.py`,
  `test_tagger.py`) to isolate tests.
- `touch_shopping_list`, `get_all_push_subscriptions`: small public API
  helpers exercised by tests; kept as stable API surface.
"""

from recipes.features.push.services import get_all_push_subscriptions
from recipes.features.shopping.services import touch_shopping_list
from recipes.shared.poller import reset_dropbox_client
from recipes.shared.tagger import reset_client

assert get_all_push_subscriptions
assert touch_shopping_list
assert reset_dropbox_client
assert reset_client
