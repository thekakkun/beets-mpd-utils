import asyncio
from math import floor
import time
from typing import Literal, Optional, TypedDict

from mpd.asyncio import MPDClient

Status = TypedDict(
    "Status",
    {
        "repeat": Literal["0", "1"],
        "random": Literal["0", "1"],
        "single": Literal["0", "1", "oneshot"],
        "consume": Literal["0", "1", "oneshot"],
        "partition": str,
        "playlist": str,
        "playlistlength": str,
        "mixrampdb": str,
        "state": Literal["play", "stop", "pause"],
        "song": str,
        "songid": str,
        "time": str,
        "elapsed": str,
        "bitrate": str,
        "duration": str,
        "audio": str,
        "nextsong": str,
        "nextsongid": str,
    },
    total=False,
)

Track = TypedDict(
    "Track",
    {
        "file": str,
        "last-modified": str,
        "format": str,
        "artist": str,
        "albumartist": str,
        "artistsort": str,
        "title": str,
        "album": str,
        "track": str,
        "date": str,
        "originaldate": str,
        "genre": str,
        "disc": str,
        "label": str,
        "albumartistsort": str,
        "musicbrainz_workid": str,
        "musicbrainz_albumid": str,
        "musicbrainz_artistid": str,
        "musicbrainz_albumartistid": str,
        "musicbrainz_releasetrackid": str,
        "musicbrainz_trackid": str,
        "time": str,
        "duration": str,
        "pos": str,
        "id": str,
    },
    total=False,
)

Subsystems = Literal[
    "database",
    "update",
    "stored_playlist",
    "playlist",
    "player",
    "mixer",
    "output",
    "options",
    "partition",
    "sticker",
    "subscription",
    "message",
    "neigbor",
    "mount",
]

PlaybackStatus = Literal["played", "skipped", "neither"]


def changes(dict_1: dict, dict_2: dict) -> bool:
    dict_1_keys = set(dict_1.keys())
    dict_2_keys = set(dict_2.keys())

    deleted_keys = dict_1_keys - dict_2_keys
    for k in deleted_keys:
        print(f"{k}: {dict_1[k]} -> None")

    common_keys = dict_1_keys & dict_2_keys
    for k in common_keys:
        if dict_1[k] != dict_2[k]:
            print(f"{k}: {dict_1[k]} -> {dict_2[k]}")

    added_keys = dict_2_keys - dict_2_keys
    for k in added_keys:
        print(f"{k}: None -> {dict_2[k]}")

    return dict_1 == dict_2


class PlaybackTracker:
    def __init__(
        self,
        track: Track,
        elapsed: Optional[float] = None,
        play_time: int = 240,
        play_percent: float = 0.5,
        skip_time: int = 20,
        skip_percent: float = 0,
    ) -> None:
        # play and skip threshold settings
        try:
            self.play_threshold = min(
                play_time,
                float(track["duration"]) * play_percent,
            )
            self.skip_threshold = max(
                skip_time,
                float(track["duration"]) * skip_percent,
            )
        except KeyError:
            self.play_threshold = play_time
            self.skip_threshold = skip_time

        # tracker stuff
        self.task = asyncio.create_task(self.tracker())
        self.play_event = asyncio.Event()
        self.pause_event = asyncio.Event()
        self.start_time = time.time()
        self.elapsed: float = elapsed or 0

        # playback status
        self.is_played: bool = False

        print(f"{self.task.get_name()}: initialize tracker from {elapsed or 0}")

    async def tracker(self):
        while True:
            await self.play_event.wait()
            print(f"{self.task.get_name()}: start tracker at {self.elapsed}")
            self.start_time = time.time()

            await self.pause_event.wait()
            self.elapsed += time.time() - self.start_time
            print(f"{self.task.get_name()}: pause tracker at {self.elapsed}")

            self.is_played = self.is_played or self.play_threshold < self.elapsed

            self.play_event.clear()
            self.pause_event.clear()

    def get_status(self) -> PlaybackStatus:
        if self.is_played:
            return "played"
        elif self.get_elapsed() < self.skip_threshold:
            return "skipped"
        else:
            return "neither"

    def get_elapsed(self) -> float:
        return self.elapsed + (time.time() - self.start_time)

    def start(self):
        if self.play_event.is_set():
            return

        self.play_event.set()

    def pause(self):
        if not self.play_event.is_set():
            return

        self.pause_event.set()

    def rewind(self, position: float):
        print(f"{self.task.get_name()}: rewind tracker to {position}")
        self.elapsed = position

    def reset(self):
        print(f"{self.task.get_name()}: replay")
        self.start_time = time.time()
        self.elapsed = 0
        self.is_played = False


class MPDWrapper:
    def __init__(self) -> None:
        self.client = MPDClient()
        self.client.disconnect()

        self.status: Status = {}
        self.track: Optional[Track] = None

        self.playback_tracker: PlaybackTracker

    async def connect(self):
        try:
            await self.client.connect("localhost", 6600)
            print("connected to MPD version,", self.client.mpd_version)

            track = await self.client.currentsong()
            self.status = await self.client.status()
            if track:
                self.track = track
                self.playback_tracker = PlaybackTracker(
                    self.track, float(self.status.get("elapsed", 0))
                )
            else:
                self.track = None

            if self.status.get("state") == "play":
                self.playback_tracker.start()

        except Exception as e:
            print(f"Connection failed: {e}")

    def set_track(self, track: Optional[Track]):
        prev_track_status = self.playback_tracker.get_status()
        print(f"previous track: {prev_track_status}")

        if track:
            self.playback_tracker = PlaybackTracker(track)

        self.track = track

    async def handle_subsystem(self, subsystem):
        print(f"\n== change in subsytem: {subsystem} ==")
        match subsystem:
            case "player":
                await self.handle_player()
            case _:
                pass

        self.status = await self.client.status()

    async def handle_player(self):
        track = await self.client.currentsong()
        status = await self.client.status()

        if self.track != track:
            self.set_track(track)
        elif self.status.get("state") == status.get("state"):
            try:
                elapsed = float(status["elapsed"])

                if elapsed < 1 or self.status["songid"] == self.status["nextsongid"]:
                    print("reset tracker")
                    self.playback_tracker.reset()
                elif elapsed < self.playback_tracker.get_elapsed():
                    print("rewind tracker")
                    self.playback_tracker.rewind(elapsed)

            except KeyError as err:
                print(f"key not found: {err}")

        match status.get("state"):
            case "play":
                self.playback_tracker.start()
            case "pause":
                self.playback_tracker.pause()
            case "stop":
                self.set_track(None)


async def main():
    player = MPDWrapper()
    await player.connect()

    async for subsystems in player.client.idle():
        for subsystem in subsystems:
            await player.handle_subsystem(subsystem)


if __name__ == "__main__":
    asyncio.run(main())
