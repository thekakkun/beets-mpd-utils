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

        self.task = asyncio.create_task(self.track())

    async def track(self):
        while True:
            await self.set_song()
            await self.track_playback()

            print(self.playback_history)
            print(self.get_playback_status())

    async def set_song(self):
        status = await self.client.status()

        # if player is in "stop" state, wait until otherwise
        while status.get("state") == "stop":
            async for _ in self.client.idle(["player"]):
                status = await self.client.status()
                break

        self.song = await self.client.currentsong()
        print(f"playing: {self.song['artist']} - {self.song['title']}")

        # Set the play and skip threshold times
        try:
            self.play_threshold = min(
                self.play_time,
                float(self.song["duration"]) * self.play_percent,
            )
            self.skip_threshold = max(
                self.skip_time,
                float(self.song["duration"]) * self.skip_percent,
            )
        except KeyError:
            self.play_threshold = self.play_time
            self.skip_threshold = self.skip_time

    async def track_playback(self):
        status = await self.client.status()

        elapsed = float(status.get("elapsed", 0))
        history: list[tuple[float, float]] = [(0, elapsed)] if elapsed else []

        while True:
            if status.get("state") == "play":
                play_from = float(status["elapsed"])
                play_at = time.time()
                expected_end = play_at + float(status["duration"]) - play_from

                tasks = [
                    pause_task,
                    seek_task,
                    replay_task,
                    new_song_task,
                    stop_task,
                ] = self.tasks_for_play(expected_end)
                [done], pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

                history.append((play_from, play_from + time.time() - play_at))
                if done in [replay_task, new_song_task, stop_task]:
                    break

            elif status.get("state") == "pause":
                tasks = [resume_task, new_song_task, stop_task] = self.tasks_for_pause()
                [done], pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if done in [new_song_task, stop_task]:
                    break

            elif status.get("state") == "stop":
                break

            status = await self.client.status()

        self.playback_history = history

    def tasks_for_play(self, expected_end: float) -> list[asyncio.Task]:
        pause_task = asyncio.create_task(self.pause())
        seek_task = asyncio.create_task(self.seek(expected_end))
        replay_task = asyncio.create_task(self.replay(expected_end))
        new_song_task = asyncio.create_task(self.new_song())
        stop_task = asyncio.create_task(self.stop())

        return [pause_task, seek_task, replay_task, new_song_task, stop_task]

    def tasks_for_pause(self) -> list[asyncio.Task]:
        resume_task = asyncio.create_task(self.resume())
        new_song_task = asyncio.create_task(self.new_song())
        stop_task = asyncio.create_task(self.stop())

        return [resume_task, new_song_task, stop_task]

    async def resume(self):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song == await self.client.currentsong()
            ):
                print("resume")
                return

    async def pause(self):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if status.get("state") == "pause":
                print("pause")
                return

    async def seek(self, expected_end: float):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song == await self.client.currentsong()
                and 1 <= abs(time.time() - expected_end)
            ):
                print("seek")
                return

    async def replay(self, expected_end: float):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song == await self.client.currentsong()
                and abs(time.time() - expected_end) < 1
            ):
                print("replay")
                return

    async def new_song(self):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song != await self.client.currentsong()
            ):
                print("new song")
                return

    # FIX: Do we care about playback status here?
    async def stop(self):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if status.get("state") == "stop":
                print("stop")
                return

    def get_play_time(self) -> float:
        if not self.playback_history:
            return 0

        self.playback_history.sort(key=lambda x: x[0])

        total_play_time = 0
        current_start = self.playback_history[0][0]
        current_end = self.playback_history[0][1]

        for start, end in self.playback_history[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                total_play_time += current_end - current_start
                current_start = start
                current_end = end

        total_play_time += current_end - current_start

        return total_play_time

    def get_playback_status(self) -> PlaybackStatus:
        play_time = self.get_play_time()

        if self.play_threshold < play_time:
            return "played"
        elif play_time < self.skip_threshold:
            return "skipped"
        else:
            return "neither"


class MPDWrapper:
    def __init__(self) -> None:
        self.client = MPDClient()
        self.client.disconnect()

    async def connect(self):
        try:
            await self.client.connect("localhost", 6600)
            print("connected to MPD version,", self.client.mpd_version)

            self.tracker = PlaybackTracker(self.client)

        except Exception as e:
            raise Exception(f"Connection failed: {e}")


async def main():
    player = MPDWrapper()
    await player.connect()


if __name__ == "__main__":
    with asyncio.Runner() as runner:
        runner.run(main())
        runner.get_loop().run_forever()
