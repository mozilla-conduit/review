# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import quote

from mozphab.config import config
from mozphab.exceptions import Error
from mozphab.logger import logger
from mozphab.profiler import list_profiles, profiler, profiles_dir


def get_profiler_origin() -> str:
    """Return the origin of the Firefox Profiler to open the profiles in.

    The `PROFILER_ORIGIN` environment variable, e.g. http://localhost:4242 for
    a local Firefox Profiler, can be shared with other tools, so it overrides
    the config file.
    """
    return os.environ.get("PROFILER_ORIGIN", config.profiler_origin).rstrip("/")


class ProfileServer(HTTPServer):
    """Serve a single profile to the Firefox Profiler, then stop."""

    def __init__(self, path: Path, origin: str):
        super().__init__(("127.0.0.1", 0), ProfileRequestHandler)
        self.profile_path = path
        self.origin = origin
        self.served = False


class ProfileRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        assert isinstance(server, ProfileServer)
        if self.path != "/" + quote(server.profile_path.name):
            self.send_error(404)
            return

        data = server.profile_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", server.origin)
        self.end_headers()
        self.wfile.write(data)
        server.served = True

    def log_message(self, format, *args):
        logger.debug(format, *args)


def print_profiles():
    profiles = list_profiles()
    if not profiles:
        logger.info("No profile found in %s", profiles_dir())
        return

    for path in reversed(profiles):
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        logger.info("%s  %s", modified.strftime("%Y-%m-%d %H:%M:%S"), path)


def open_profile(args: argparse.Namespace):
    # Don't let this run replace one of the profiles it lists or opens.
    profiler.disable()

    if args.list:
        print_profiles()
        return

    if args.path:
        path = Path(args.path).resolve()
        if not path.is_file():
            raise Error(f"Profile not found: {path}")
    else:
        profiles = list_profiles()
        if not profiles:
            raise Error(f"No profile found in {profiles_dir()}")
        path = profiles[-1]

    origin = get_profiler_origin()
    server = ProfileServer(path, origin)
    port = server.server_address[1]
    profile_url = f"http://127.0.0.1:{port}/{quote(path.name)}"
    profiler_url = f"{origin}/from-url/{quote(profile_url, safe='')}/marker-chart/"

    logger.info("Serving %s at %s", path, profile_url)
    # The browser may not be able to reach this server, e.g. over SSH, so log
    # the URL even when opening it.
    if config.open_browser and webbrowser.open_new_tab(profiler_url):
        logger.info("Opened %s", profiler_url)
    else:
        logger.info("Open %s in your browser to view the profile.", profiler_url)

    try:
        while not server.served:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def add_parser(parser):
    profile_parser = parser.add_parser(
        "profile",
        help="Open the profile of a previous run in the Firefox Profiler.",
        description=(
            "Open the profile of a previous run in the Firefox Profiler. Set "
            "profiler.origin in the config file or the PROFILER_ORIGIN "
            "environment variable to use another instance of the profiler, "
            "e.g. http://localhost:4242."
        ),
    )
    profile_parser.add_argument(
        "path",
        nargs="?",
        help="Profile to open. Defaults to the most recent one.",
    )
    profile_parser.add_argument(
        "--list",
        action="store_true",
        help="List the saved profiles, most recent first.",
    )
    profile_parser.set_defaults(
        func=open_profile, needs_repo=False, needs_user_data=False
    )
