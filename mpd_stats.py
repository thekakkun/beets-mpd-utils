import asyncio
from math import floor
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


class Player:
    def __init__(
        self,
        play_time: int = 240,
        play_percent: int = 50,
        skip_time: int = 20,
        skip_percent: int = 0,
    ) -> None:
        # play and skip threshold settings
        self.play_time: int = play_time
        self.play_percent: int = play_percent
        self.skip_time: int = skip_time
        self.skip_percent: int = skip_percent

        # playback state
        self.elapsed: int = 0
        self.is_played: bool = False

        self.track: Optional[Track] = None
        self.playback_task: Optional[asyncio.Task] = None

    def set_track(self, track: Track, elapsed: Optional[float] = None):
        print(
            f"change track from {self.track['title'] if self.track else 'None'} to {track['title']}"
        )

        previous_played = self.is_played
        previous_skipped = False

        if self.track:
            skip_threshold = max(
                self.skip_time,
                (float(self.track["duration"]) * self.skip_percent) // 100,
            )
            previous_skipped = not previous_played and self.elapsed < skip_threshold

            if previous_played:
                print("previous track was played")
            elif previous_skipped:
                print("previous track was skipped")
            else:
                print("previous track was neither played nor skipped")

        self.elapsed = floor(elapsed) if elapsed else 0
        self.is_played = False
        self.track = track
        if self.playback_task:
            print("cancelling player coroutine")
            self.playback_task.cancel()

        return (previous_played, previous_skipped)

    def play(self):
        async def coro(self):
            print("starting player coroutine")
            if self.track:
                play_threshold = min(
                    self.play_time,
                    (float(self.track["duration"]) * self.play_percent) // 100,
                )

                while True:
                    if play_threshold < self.elapsed:
                        print("set track played")
                        self.is_played = True
                        return

                    await asyncio.sleep(1)
                    self.elapsed += 1
                    print(f"elapsed: {self.elapsed}")

        print("play track")

        self.playback_task = asyncio.create_task(coro(self))

    def pause(self):
        print("pause track")
        if self.playback_task and not self.playback_task.done():
            print("cancelling player coroutine")
            self.playback_task.cancel()

    def stop(self):
        print("stop player")
        if self.playback_task and not self.playback_task.done():
            print("cancelling player coroutine")
            self.playback_task.cancel()

        self.track = None
        self.playback_task = None

    def seek(self, time: float):
        print("seeking track")
        self.elapsed = min(self.elapsed, int(time))

    def replay(self):
        print("replay track")
        if self.track:
            self.set_track(self.track)
            self.play()


class MPDWrapper:
    status: Status

    def __init__(self) -> None:
        self.client = MPDClient()
        self.client.disconnect()
        self.player = Player()

    async def connect(self):
        try:
            await self.client.connect("localhost", 6600)
            print("connected to MPD version,", self.client.mpd_version)

            track = await self.client.currentsong()
            self.status = await self.client.status()

            if track:
                self.player.set_track(track, float(self.status.get("elapsed", 0)))
            if self.status["state"] == "play":
                self.player.play()

        except Exception as e:
            print(f"Connection failed: {e}")

    async def handle_subsystem(self, subsystem):
        print(f"\n== change in subsytem: {subsystem} ==")
        match subsystem:
            case "player":
                await self.handle_player()
            case _:
                pass

        self.status = await self.client.status()

    async def handle_player(self):
        prev_track = self.player.track
        track = await self.client.currentsong()

        prev_status = self.status
        status = await self.client.status()

        # track changed
        if prev_track != track:
            self.player.set_track(track)
            self.player.play()
            prev_status["state"] = "play"

        # status changed
        if prev_status["state"] != status["state"]:
            match status["state"]:
                case "play":
                    self.player.play()
                case "pause":
                    self.player.pause()
                case "stop":
                    self.player.stop()

        # track and status did not change, must be seek or replay
        if prev_track == track and prev_status["state"] == status["state"]:
            elapsed = float(status["elapsed"])

            if elapsed < 1:
                self.player.replay()
                self.player.play()
            elif 1 < abs(float(prev_status["elapsed"]) - elapsed):
                self.player.seek(float(status["elapsed"]))


async def main():
    player = MPDWrapper()
    await player.connect()

    async for subsystems in player.client.idle():
        for subsystem in subsystems:
            await player.handle_subsystem(subsystem)


if __name__ == "__main__":
    asyncio.run(main())
