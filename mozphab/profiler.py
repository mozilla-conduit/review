# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Record a timeline of each run and save it in the Firefox Profiler format.

Markers are recorded for the phases of each command, VCS commands, network
requests, file I/O, sleeps, user input, log messages and telemetry.
`moz-phab profile` opens the saved profiles in https://profiler.firefox.com,
similar to `mach resource-usage`.
"""

import itertools
import json
import os
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

from mozphab import environment


class Category(IntEnum):
    """Index in the `categories` list of the profile metadata."""

    OTHER = 0
    NETWORK = 1
    USER_INPUT = 2
    PHASE = 3
    LOGGING = 4
    VCS = 5
    # Only used by samples, for the time not spent waiting.
    PYTHON = 6


# The name and color of each category.
CATEGORY_DISPLAY = {
    Category.OTHER: ("Other", "grey"),
    Category.NETWORK: ("Network", "blue"),
    # Transparent in the timeline, as moz-phab is idle.
    Category.USER_INPUT: ("User input", "transparent"),
    # The marker chart sorts categories alphabetically, so this name puts
    # phases at the top.
    Category.PHASE: ("Activity", "purple"),
    Category.LOGGING: ("Logging", "grey"),
    Category.VCS: ("VCS", "yellow"),
    Category.PYTHON: ("Python", "green"),
}


class MarkerType(str, Enum):
    """The name of the marker schema of each marker."""

    PHASE = "Phase"
    NETWORK = "Network"
    COMMAND = "Command"
    USER_INPUT = "UserInput"
    SLEEP = "Sleep"
    FILE_IO = "FileIO"
    GLEAN = "Glean"
    LOG = "Log"


class MarkerPhase(IntEnum):
    INSTANT = 0
    INTERVAL = 1


# When waiting for several things at once, e.g. network requests in worker
# threads during a git command, samples show the first one of these.
WAITING_PRIORITY = [Category.USER_INPUT, Category.VCS, Category.NETWORK]

# The timeline draws each sample from halfway to the previous sample to halfway
# to the next one, so a sample this close to the end of each segment keeps the
# color from changing before the segment ends.
SEGMENT_END_SAMPLE_OFFSET = 0.001


@dataclass(slots=True)
class Marker:
    name: str
    category: Category
    start_time: float
    # `None` for instant markers.
    end_time: float | None
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Frame:
    name: str
    category: Category


@dataclass(slots=True)
class Interval:
    """What is being waited for, used to build samples."""

    start_time: float
    end_time: float
    category: Category
    frames: list[str]


@dataclass(slots=True)
class Segment:
    """A time range during which samples have the same stack."""

    start_time: float
    end_time: float
    stack: tuple[Frame, ...]


DEFAULT_TRACK_NAME = "moz-phab"


def profiles_dir() -> Path:
    return Path(environment.MOZBUILD_PATH) / "profiles"


def list_profiles() -> list[Path]:
    """Return the saved profiles, oldest first."""
    return sorted(profiles_dir().glob("profile-*.json"))


class Profiler:
    """Record the markers of a run, and save them as a profile."""

    def __init__(self):
        self.start()

    def start(self):
        """Start a new recording, dropping any previously recorded marker."""
        self.start_wall = time.time()
        self.start_time = time.perf_counter()
        self.markers: list[Marker] = []
        self.track_name = DEFAULT_TRACK_NAME
        self.network_ids = itertools.count(1)
        self.enabled = True

    def disable(self):
        """Stop recording markers, and don't save the profile of this run."""
        self.enabled = False
        self.markers.clear()

    def set_track_name(self, name: str):
        """Name the track after the command being run."""
        self.track_name = name

    def _now(self) -> float:
        return (time.perf_counter() - self.start_time) * 1000

    def _append(
        self,
        name: str,
        category: Category,
        start_time: float,
        end_time: float | None,
        data: dict[str, Any],
    ):
        """Add a marker to the recording.

        Markers of all threads are shown on a single track, so that they can be
        compared with the phases of the main thread; the marker chart puts
        overlapping markers on separate rows.
        """
        if not self.enabled:
            return

        thread = threading.current_thread()
        if thread is not threading.main_thread():
            data["thread"] = thread.name
        # `list.append` is atomic, so markers can be added from worker threads.
        self.markers.append(Marker(name, category, start_time, end_time, data))

    @contextmanager
    def marker(self, name: str, category: Category, marker_type: MarkerType, **data):
        """Record a marker spanning the body of the `with` statement.

        Yields the marker data dict, which can be updated before the marker ends.
        """
        data["type"] = marker_type
        start_time = self._now()
        try:
            yield data
        finally:
            self._append(name, category, start_time, self._now(), data)

    def add_instant_marker(
        self, name: str, category: Category, marker_type: MarkerType, **data
    ):
        """Record a marker without duration."""
        data["type"] = marker_type
        self._append(name, category, self._now(), None, data)

    def phase(self, name: str) -> AbstractContextManager[dict]:
        """Record a phase marker, the only kind shown in the timeline."""
        return self.marker("Phase", Category.PHASE, MarkerType.PHASE, phase=name)

    def add_startup_phase(self):
        """Record a phase from the start of the recording until now.

        The recording starts when this module is imported, so this phase covers
        loading the other modules of moz-phab.
        """
        self._append(
            "Phase",
            Category.PHASE,
            0,
            self._now(),
            {"type": MarkerType.PHASE, "phase": "Loading Python modules"},
        )

    @contextmanager
    def network_marker(self, uri: str, method: str = "GET"):
        """Record a network marker spanning the body of the `with` statement.

        Yields the marker data dict, where `responseStatus`, `count` (the size of
        the response), `contentType` and `httpVersion` can be set.
        """
        # The front-end merges a start and a stop marker with the same `id` into
        # a single request, displayed in the network track.
        # Gecko names these markers `Load <id>: <uri>`; the front-end only
        # expects a colon before the URI, so use the name to show the method.
        load_id = next(self.network_ids)
        name = f"{method} {load_id}: {uri}"
        data: dict[str, Any] = {
            "type": MarkerType.NETWORK,
            "URI": uri,
            "id": load_id,
            "pri": 0,
        }
        start_time = self._now()
        self._append(
            name,
            Category.NETWORK,
            start_time,
            start_time,
            {
                **data,
                "status": "STATUS_START",
                "startTime": start_time,
                "endTime": start_time,
            },
        )
        data["status"] = "STATUS_STOP"
        try:
            yield data
        except BaseException as e:
            data["status"] = "STATUS_CANCEL"
            data["requestStatus"] = type(e).__name__
            # `urllib.error.HTTPError` has the HTTP status code.
            if isinstance(code := getattr(e, "code", None), int):
                data["responseStatus"] = code
            raise
        finally:
            end_time = self._now()
            data.update(
                startTime=start_time,
                endTime=end_time,
                requestStart=start_time,
                responseStart=end_time,
                responseEnd=end_time,
            )
            self._append(name, Category.NETWORK, start_time, end_time, data)

    def file_io(
        self, operation: str, filename: str | os.PathLike
    ) -> AbstractContextManager[dict]:
        """Record a file I/O marker, `operation` being e.g. "read" or "write"."""
        return self.marker(
            "FileIO",
            Category.OTHER,
            MarkerType.FILE_IO,
            operation=operation,
            filename=str(filename),
        )

    def sleep_marker(self, reason: str) -> AbstractContextManager[dict]:
        """Record a marker for the time spent sleeping or waiting."""
        return self.marker("Sleep", Category.OTHER, MarkerType.SLEEP, reason=reason)

    def build_profile(self) -> dict[str, Any]:
        """Return the recorded markers as a Firefox Profiler processed profile."""
        duration = self._now()
        thread = _build_thread(self.track_name)
        strings = thread["stringArray"]
        string_indexes: dict[str, int] = {}
        markers = thread["markers"]
        for marker in sorted(self.markers, key=lambda marker: marker.start_time):
            name_index = string_indexes.setdefault(marker.name, len(strings))
            if name_index == len(strings):
                strings.append(marker.name)

            markers["data"].append(marker.data)
            markers["name"].append(name_index)
            markers["startTime"].append(marker.start_time)
            markers["endTime"].append(marker.end_time)
            markers["phase"].append(
                MarkerPhase.INSTANT if marker.end_time is None else MarkerPhase.INTERVAL
            )
            markers["category"].append(marker.category)
            markers["length"] += 1

        intervals = [
            interval
            for marker in self.markers
            if (interval := _waiting_interval(marker))
        ]
        _add_samples(thread, build_segments(intervals, self.track_name, duration))
        return {
            "meta": _build_meta(self.start_wall, duration),
            "libs": [],
            "threads": [thread],
            "counters": [],
        }

    def save(self, profiles_to_keep: int) -> Path | None:
        """Save the recorded profile and delete the oldest ones.

        All the profiles are kept when `profiles_to_keep` is 0.
        Returns the path of the profile, or `None` if profiling is disabled.
        """
        if not self.enabled:
            return None

        directory = profiles_dir()
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.start_wall))
        path = directory / f"profile-{timestamp}-{os.getpid()}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.build_profile(), f, separators=(",", ":"))

        if profiles_to_keep:
            for old_profile in list_profiles()[:-profiles_to_keep]:
                old_profile.unlink(missing_ok=True)

        return path


def _build_meta(start_wall: float, duration: float) -> dict[str, Any]:
    def schema(
        name: MarkerType,
        chart_label: str,
        fields: list[tuple[str, str, str]],
        label: str | None = None,
        timeline: bool = False,
    ):
        """Build a marker schema, `label` defaulting to `chart_label`."""
        fields = [*fields, ("thread", "Thread", "string")]
        return {
            "name": name,
            "tooltipLabel": label or chart_label,
            "tableLabel": label or chart_label,
            "chartLabel": chart_label,
            "display": [
                "marker-chart",
                "marker-table",
                *(["timeline-overview"] if timeline else []),
            ],
            "data": [
                {"key": key, "label": field_label, "format": field_format}
                for key, field_label, field_format in fields
            ],
        }

    return {
        "processType": 0,
        # The front-end would only keep the final digits and dots of a version
        # in `misc`, e.g. "20260929" for "0.1.dev842+g521dd9831.d20260929".
        "product": f"{environment.MOZPHAB_NAME} {environment.MOZPHAB_VERSION}",
        "stackwalk": 0,
        "version": 27,
        "preprocessedProfileVersion": 47,
        "symbolicationNotSupported": True,
        "interval": 1,
        "startTime": start_wall * 1000,
        "profilingStartTime": 0,
        "profilingEndTime": duration,
        "categories": [
            {"name": name, "color": color, "subcategories": ["Other"]}
            for name, color in (CATEGORY_DISPLAY[category] for category in Category)
        ],
        "markerSchema": [
            schema(
                MarkerType.PHASE,
                "{marker.data.phase}",
                [("phase", "Phase", "string")],
                timeline=True,
            ),
            {
                **schema(
                    MarkerType.LOG,
                    "{marker.data.message}",
                    [("message", "Message", "string")],
                ),
                "colorField": "color",
            },
            # The front-end only knows the FileIO schema from Gecko profiles.
            schema(
                MarkerType.FILE_IO,
                "{marker.data.operation} — {marker.data.filename}",
                [
                    ("operation", "Operation", "string"),
                    ("filename", "Filename", "file-path"),
                ],
            ),
            schema(
                MarkerType.GLEAN,
                "{marker.data.metric}",
                [
                    ("metric", "Metric", "string"),
                    ("operation", "Operation", "string"),
                    ("value", "Value", "string"),
                    ("telemetry", "Telemetry", "string"),
                ],
                label="{marker.data.metric}.{marker.data.operation}({marker.data.value})",
            ),
            schema(
                MarkerType.SLEEP,
                "{marker.data.reason}",
                [("reason", "Reason", "string")],
            ),
            schema(
                MarkerType.USER_INPUT,
                "{marker.data.question}",
                [("question", "Question", "string")],
            ),
            schema(
                MarkerType.COMMAND,
                "{marker.data.subcommand}",
                [
                    ("subcommand", "Subcommand", "string"),
                    ("command", "Command", "string"),
                    ("exitCode", "Exit code", "integer"),
                ],
                label="{marker.data.shortCommand}",
            ),
        ],
    }


def _build_thread(track_name: str) -> dict[str, Any]:
    return {
        "processType": "default",
        "processName": environment.MOZPHAB_NAME,
        "processStartupTime": 0,
        "processShutdownTime": None,
        "registerTime": 0,
        "unregisterTime": None,
        "pausedRanges": [],
        "showMarkersInTimeline": True,
        "name": track_name,
        "isMainThread": True,
        "pid": str(os.getpid()),
        "tid": threading.main_thread().native_id or 0,
        "samples": {
            "weightType": "tracing-ms",
            "weight": [],
            "stack": [],
            "time": [],
            "length": 0,
        },
        "markers": {
            "data": [],
            "name": [],
            "startTime": [],
            "endTime": [],
            "phase": [],
            "category": [],
            "length": 0,
        },
        "stackTable": {
            "frame": [],
            "prefix": [],
            "category": [],
            "subcategory": [],
            "length": 0,
        },
        "frameTable": {
            "address": [],
            "inlineDepth": [],
            "category": [],
            "subcategory": [],
            "func": [],
            "nativeSymbol": [],
            "innerWindowID": [],
            "implementation": [],
            "line": [],
            "column": [],
            "length": 0,
        },
        "funcTable": {
            "isJS": [],
            "relevantForJS": [],
            "name": [],
            "resource": [],
            "fileName": [],
            "lineNumber": [],
            "columnNumber": [],
            "length": 0,
        },
        "resourceTable": {"lib": [], "name": [], "host": [], "type": [], "length": 0},
        "nativeSymbols": {
            "libIndex": [],
            "address": [],
            "name": [],
            "functionSize": [],
            "length": 0,
        },
        "stringArray": [],
    }


def _waiting_interval(marker: Marker) -> Interval | None:
    """Return what a marker is waiting for, if anything."""
    if marker.end_time is None or marker.end_time <= marker.start_time:
        return None

    data = marker.data
    marker_type = data["type"]
    if marker_type == MarkerType.USER_INPUT:
        category, frames = Category.USER_INPUT, ["Waiting for user input"]
    elif marker_type == MarkerType.COMMAND:
        # Not all commands are VCS commands, e.g. when updating moz-phab.
        category, frames = marker.category, [marker.name, data["subcommand"]]
    # Start markers have no duration, so only the stop markers get here.
    elif marker_type == MarkerType.NETWORK:
        # E.g. the Conduit API URL, then the method name.
        base, _, last = data["URI"].split("?")[0].rpartition("/")
        category, frames = Category.NETWORK, [base + "/", last]
    elif marker_type == MarkerType.SLEEP:
        category, frames = Category.OTHER, ["Sleep", data["reason"]]
    else:
        return None
    return Interval(marker.start_time, marker.end_time, category, frames)


def build_segments(
    intervals: list[Interval], track_name: str, duration: float
) -> list[Segment]:
    """Split the time in segments during which moz-phab is waiting for the
    same thing, or not waiting and running Python code.
    """

    def waiting_rank(interval: Interval) -> tuple[int, float]:
        priority = len(WAITING_PRIORITY)
        if interval.category in WAITING_PRIORITY:
            priority = WAITING_PRIORITY.index(interval.category)
        # Prefer the most recent one.
        return priority, -interval.start_time

    # Merge the consecutive segments that have the same stack. Every interval
    # start and end is a boundary, so intervals running at the start of a
    # segment run until its end.
    segments: list[Segment] = []
    boundaries = sorted(
        {
            0,
            duration,
            *(
                boundary
                for interval in intervals
                for boundary in (interval.start_time, interval.end_time)
            ),
        }
    )
    by_start = sorted(intervals, key=lambda interval: interval.start_time, reverse=True)
    by_end = sorted(intervals, key=lambda interval: interval.end_time, reverse=True)
    active: list[Interval] = []
    for start, end in itertools.pairwise(boundaries):
        while by_start and by_start[-1].start_time <= start:
            active.append(by_start.pop())
        while by_end and by_end[-1].end_time <= start:
            active.remove(by_end.pop())

        stack = [Frame(track_name, Category.PYTHON)]
        if active:
            waiting = min(active, key=waiting_rank)
            stack += [Frame(name, waiting.category) for name in waiting.frames]
        if segments and segments[-1].stack == tuple(stack):
            segments[-1].end_time = end
        else:
            segments.append(Segment(start, end, tuple(stack)))
    return segments


def _add_samples(thread: dict[str, Any], segments: list[Segment]):
    """Add samples coloring the timeline by what moz-phab was waiting for.

    Each segment gets a sample at its start, weighted by its duration for the
    call tree, and a sample at its end for the timeline.
    """
    strings = thread["stringArray"]
    func_indexes: dict[str, int] = {}
    frame_indexes: dict[Frame, int] = {}
    stack_indexes: dict[tuple[int | None, int], int] = {}

    def add_row(table: dict[str, Any], **row):
        for key, value in row.items():
            table[key].append(value)
        table["length"] += 1
        return table["length"] - 1

    def get_stack(frames: tuple[Frame, ...]) -> int:
        stack_index = None
        for frame in frames:
            if frame.name not in func_indexes:
                strings.append(frame.name)
                func_indexes[frame.name] = add_row(
                    thread["funcTable"],
                    isJS=False,
                    relevantForJS=False,
                    name=len(strings) - 1,
                    resource=-1,
                    fileName=None,
                    lineNumber=None,
                    columnNumber=None,
                )
            if frame not in frame_indexes:
                frame_indexes[frame] = add_row(
                    thread["frameTable"],
                    address=-1,
                    inlineDepth=0,
                    category=frame.category,
                    subcategory=0,
                    func=func_indexes[frame.name],
                    nativeSymbol=None,
                    innerWindowID=0,
                    implementation=None,
                    line=None,
                    column=None,
                )
            key = (stack_index, frame_indexes[frame])
            if key not in stack_indexes:
                stack_indexes[key] = add_row(
                    thread["stackTable"],
                    frame=key[1],
                    prefix=stack_index,
                    category=frame.category,
                    subcategory=0,
                )
            stack_index = stack_indexes[key]
        assert stack_index is not None
        return stack_index

    samples = thread["samples"]
    for segment in segments:
        stack_index = get_stack(segment.stack)
        segment_duration = segment.end_time - segment.start_time
        add_row(
            samples, time=segment.start_time, stack=stack_index, weight=segment_duration
        )
        if segment_duration > 2 * SEGMENT_END_SAMPLE_OFFSET:
            add_row(
                samples,
                time=segment.end_time - SEGMENT_END_SAMPLE_OFFSET,
                stack=stack_index,
                weight=0,
            )


profiler = Profiler()
