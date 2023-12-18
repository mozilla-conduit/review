# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Any


class SimpleCache:
    """Simple key/value store with all lowercase keys."""

    def __init__(self):
        self._cache = {}

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._cache

    def get(self, key: str) -> Any:
        return self._cache.get(key.lower())

    def set(self, key: str, value: Any):
        self._cache[key.lower()] = value

    def delete(self, key: str):
        if key in self:
            del self._cache[key.lower()]

    def reset(self):
        self._cache = {}


cache = SimpleCache()
