# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import signal
import sys
import threading
from contextlib import contextmanager

from mozphab import environment

WAIT_INTERVAL = 0.2


def clear_terminal_line():
    if environment.HAS_ANSI:
        sys.stdout.write("\r\033[K")  # move to start of line, erase to end of line
        sys.stdout.flush()


def signal_sigint(self, *args):
    print("\nCancelled")
    raise KeyboardInterrupt()


signal.signal(signal.SIGINT, signal_sigint)


class Spinner(threading.Thread):
    def __init__(self, message: str):
        super().__init__()
        self.message = message
        self.daemon = True
        self._stop_event = threading.Event()

    def signal_stop(self):
        self._stop_event.set()

    def run(self):
        if not environment.HAS_ANSI:
            sys.stdout.write("%s  " % self.message)

        spinner = ["-", "\\", "|", "/"]
        spin = 0
        try:
            while not self._stop_event.wait(WAIT_INTERVAL):
                if environment.HAS_ANSI:
                    sys.stdout.write("%s %s\r" % (self.message, spinner[spin]))
                else:
                    sys.stdout.write(chr(8) + spinner[spin])
                sys.stdout.flush()
                spin = (spin + 1) % len(spinner)
        finally:
            if environment.HAS_ANSI:
                clear_terminal_line()
            else:
                sys.stdout.write(chr(8) + " \n")


@contextmanager
def wait_message(message: str):
    if not environment.SHOW_SPINNER:
        yield
        return

    spinner = Spinner(message)
    spinner.start()
    try:
        yield
    finally:
        spinner.signal_stop()
        spinner.join()
