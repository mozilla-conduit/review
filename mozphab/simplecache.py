# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


class SimpleCache:
    """Simple key/value store with all lowercase keys."""

    def __init__(self):
        self._cache = dict()

    def __contains__(self, key):
        return key.lower() in self._cache

    def get(self, key):
        return self._cache.get(key.lower())

    def set(self, key, value):
        self._cache[key.lower()] = value

    def delete(self, key):
        if key in self:
            del self._cache[key.lower()]

    def reset(self):
        self._cache = dict()


cache = SimpleCache()
