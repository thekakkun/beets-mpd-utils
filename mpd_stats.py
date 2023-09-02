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

EndReason = Literal["pause", "seek", "replay", "stop", "new song"]
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
        client: MPDClient,
        play_time: int = 240,
        play_percent: float = 0.5,
        skip_time: int = 20,
        skip_percent: float = 0,
    ):
        self.client = client

        self.play_time = play_time
        self.play_percent = play_percent
        self.skip_time = skip_time
        self.skip_percent = skip_percent

        self.track_song_task: Optional[asyncio.Task] = None
        self.play_history: list[tuple[float, float]] = []

    async def set_new_song(self):
        status = await self.client.status()

        if status.get("state") == "stop":
            async for _ in self.client.idle(["player"]):
                status = await self.client.status()
                if status.get("state") == "play":
                    break

        song = await self.client.currentsong()
        print(f"\nNew song: {song['artist']} - {song['title']}")
        elapsed = float((await self.client.status()).get("elapsed", 0))

        if elapsed:
            # new song is already being played
            self.play_history = [(0, elapsed)]

        if status.get("state") == "play":
            # start playback tracker from elapsed time
            self.track_song_task = asyncio.create_task(self.track_song(song, elapsed))
        else:
            # start tracker, awaiting play
            self.track_song_task = asyncio.create_task(self.track_song(song))

    async def track_song(
        self, song: Track, playing_from: Optional[float] = None
    ) -> PlaybackStatus:
        while True:
            start_from: dict[str, float] = {
                "elapsed": await self.play_start()
                if playing_from == None
                else playing_from,
                "time": time.time(),
            }

            try:
                expected_end = (
                    start_from["time"] + float(song["duration"]) - start_from["elapsed"]
                )
            except KeyError:
                raise Exception("song has no duration")

            playing_from = None

            end_reason = await self.play_end(song, expected_end)
            end_time = start_from["elapsed"] + time.time() - start_from["time"]

            if end_reason == "pause":
                print("pause song")
                self.play_history.append((start_from["elapsed"], end_time))

            elif end_reason == "seek":
                print("seek song")
                self.play_history.append((start_from["elapsed"], end_time))

                try:
                    playing_from = float((await self.client.status())["elapsed"])
                except KeyError:
                    raise Exception("elapsed time not found")

            elif end_reason == "replay":
                print("replay song")
                self.play_history.append(
                    (
                        start_from["elapsed"],
                        start_from["elapsed"] + time.time() - start_from["time"],
                    )
                )
                break

            elif end_reason == "stop":
                print("stop song")
                self.play_history.append((start_from["elapsed"], end_time))
                break

            elif end_reason == "new song":
                self.play_history.append(
                    (
                        start_from["elapsed"],
                        start_from["elapsed"] + time.time() - start_from["time"],
                    )
                )
                break

        playback_status = self.get_playback_status(song)
        print(playback_status)

        await self.set_new_song()

        return playback_status

    def get_play_time(self) -> float:
        if not self.play_history:
            return 0

        self.play_history.sort(key=lambda x: x[0])

        total_play_time = 0
        current_start = self.play_history[0][0]
        current_end = self.play_history[0][1]

        for start, end in self.play_history[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                total_play_time += current_end - current_start
                current_start = start
                current_end = end

        total_play_time += current_end - current_start

        return total_play_time

    def get_playback_status(self, song: Track) -> PlaybackStatus:
        try:
            play_threshold = min(
                self.play_time,
                float(song["duration"]) * self.play_percent,
            )
            skip_threshold = max(
                self.skip_time,
                float(song["duration"]) * self.skip_percent,
            )
        except KeyError:
            play_threshold = self.play_time
            skip_threshold = self.skip_time

        play_time = self.get_play_time()

        if play_threshold < play_time:
            return "played"
        elif play_time < skip_threshold:
            return "skipped"
        else:
            return "neither"

    async def play_start(self) -> float:
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if status.get("state") == "play":
                try:
                    print(f"play from {float(status['elapsed'])}")
                    return float(status["elapsed"])

                except KeyError:
                    raise Exception("elapsed not found in status")

        return 0

    async def play_end(self, song: Track, expected_end: float) -> Optional[EndReason]:
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if status.get("state") == "pause":
                return "pause"

            elif status.get("state") == "play":
                current_song = await self.client.currentsong()
                if song == current_song:
                    # we're close to when the song expected to end.
                    # Event probably not due to user input (aka seek).
                    if abs(time.time() - expected_end) < 1:
                        return "replay"
                    else:
                        return "seek"

                else:
                    return "new song"

            elif status.get("state") == "stop":
                return "stop"


class MPDWrapper:
    def __init__(self) -> None:
        self.client = MPDClient()
        self.client.disconnect()

    async def connect(self):
        try:
            await self.client.connect("localhost", 6600)
            print("connected to MPD version,", self.client.mpd_version)

            self.tracker = PlaybackTracker(self.client)
            await self.tracker.set_new_song()

        except Exception as e:
            raise Exception(f"Connection failed: {e}")


async def main():
    player = MPDWrapper()
    await player.connect()


if __name__ == "__main__":
    with asyncio.Runner() as runner:
        runner.run(main())
        runner.get_loop().run_forever()
