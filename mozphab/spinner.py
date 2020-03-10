# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import signal
import sys
import threading
import time

from contextlib import contextmanager
from mozphab import environment

# py2 doesn't handle SIGINT with background threads; hook up our own handler
# to allow background threads to check sig_int.triggered and stop.


class SigIntHandler(object):
    def __init__(self):
        self.triggered = False

    # noinspection PyUnusedLocal
    def signal_handler(self, sig, frame):
        self.triggered = True
        raise KeyboardInterrupt()


sig_int = SigIntHandler()
signal.signal(signal.SIGINT, sig_int.signal_handler)


class Spinner(threading.Thread):
    def __init__(self, message):
        super().__init__()
        self.message = message
        self.daemon = True
        self.running = False

    def run(self):
        self.running = True

        if not environment.HAS_ANSI:
            sys.stdout.write("%s  " % self.message)

        spinner = ["-", "\\", "|", "/"]
        spin = 0
        try:
            while self.running:
                if environment.HAS_ANSI:
                    sys.stdout.write("%s %s\r" % (self.message, spinner[spin]))
                else:
                    sys.stdout.write(chr(8) + spinner[spin])
                sys.stdout.flush()
                spin = (spin + 1) % len(spinner)
                time.sleep(0.2)
        finally:
            if environment.HAS_ANSI:
                sys.stdout.write("\r\033[K")
            else:
                sys.stdout.write(chr(8) + " \n")
            sys.stdout.flush()


@contextmanager
def wait_message(message):
    if not environment.SHOW_SPINNER:
        yield
        return

    spinner = Spinner(message)
    spinner.start()
    try:
        yield
    finally:
        spinner.running = False
        spinner.join()
        if sig_int.triggered:
            print("Cancelled")
            sys.exit(3)
