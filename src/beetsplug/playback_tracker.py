import asyncio
import time
from math import inf
from os import path
from typing import Literal

from beets import config
from beets.dbcore import types
from beets.library import DateType
from beets.library import Item as BeetSong
from beets.library import Library, PathQuery
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand
from mpd.asyncio import MPDClient
from mpd.base import MPDError
from mpd_types import Track as MPDSong


music_dir: str = config["directory"].get(str)  # type: ignore


class Song:
    def __init__(self) -> None:
        self.mpd: MPDSong
        self.beet: BeetSong

    @classmethod
    async def now_playing(cls, client: MPDClient, lib: Library):
        self = Song()

        while (await client.status()).get("state") == "stop":
            async for _ in client.idle(["player"]):
                break

        # MPD song data
        self.mpd = await client.currentsong()

        # beets song data
        query = PathQuery("path", path.join(music_dir, self.mpd["file"]))
        self.beet = lib.items(query).get()

        return self


class NoElapsedError(Exception):
    pass


class MPDEvents:
    def __init__(
        self,
        client: MPDClient,
        song: MPDSong,
    ) -> None:
        self.client = client
        self.song = song

    async def play_from(self) -> float:
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("status") == "play"
                and self.song == await self.client.currentsong()
            ):
                try:
                    return float(status["elapsed"])
                except:
                    raise NoElapsedError()

        raise MPDError

    async def pause_at(self) -> float:
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if status.get("status") == "pause":
                try:
                    return float(status["elapsed"])
                except:
                    raise NoElapsedError()

        raise MPDError

    async def seek_to(self, expected_end: float) -> float:
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song == await self.client.currentsong()
                and 1 < abs(time.time() - expected_end)
            ):
                try:
                    return float(status["elapsed"])
                except:
                    raise NoElapsedError()

        raise MPDError

    async def replay(self, expected_end: float):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song == await self.client.currentsong()
                and abs(time.time() - expected_end) < 1
            ):
                return

        raise MPDError

    async def new_song(self):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if (
                status.get("state") == "play"
                and self.song != await self.client.currentsong()
            ):
                return

        raise MPDError

    async def stop(self):
        async for _ in self.client.idle(["player"]):
            status = await self.client.status()

            if status.get("state") == "stop":
                return

        raise MPDError


class Tracker(MPDEvents):
    def __init__(
        self,
        client: MPDClient,
        song: MPDSong,
    ) -> None:
        super().__init__(client, song)

        self.song: MPDSong
        self.play_threshold: float
        self.skip_threshold: float

        self.client: MPDClient
        self.task: asyncio.Task

        self.history: list[tuple[float, float]]
        self.play_from_pos: float
        self.play_from_time: float
        self.expected_end: float

    @classmethod
    async def track(
        cls,
        client: MPDClient,
        song: MPDSong,
        play_time: int = 240,
        play_percent: float = 0.5,
        skip_time: int = 20,
        skip_percent: float = 0,
    ):
        self = Tracker(client, song)

        self.play_threshold = min(
            play_time,
            float(self.song["duration"]) * play_percent,
        )
        self.skip_threshold = max(
            skip_time,
            float(self.song["duration"]) * skip_percent,
        )

        elapsed = float((await self.client.status()).get("elapsed", 0))
        self.history = [(0, elapsed)] if elapsed else []
        self.set_play_from(elapsed)

        self.task = asyncio.create_task(self.run())

        return self

    async def run(self):
        while True:
            status = await self.client.status()

            if status.get("state") == "play":
                pause = asyncio.create_task(self.pause_at())
                seek = asyncio.create_task(self.seek_to(self.expected_end))
                replay = asyncio.create_task(self.replay(self.expected_end))
                new_song = asyncio.create_task(self.new_song())
                stop = asyncio.create_task(self.stop())

                [done], pending = await asyncio.wait(
                    [pause, seek, replay, new_song, stop],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                if done == pause:
                    position = pause.result()
                    self.history.append((self.play_from_pos, position))

                elif done == seek:
                    self.history.append(
                        (
                            self.play_from_pos,
                            self.play_from_pos + time.time() - self.play_from_time,
                        )
                    )
                    position = seek.result()
                    self.set_play_from(position)

                elif done == replay:
                    self.history.append(
                        (self.play_from_pos, float(self.song["duration"]))
                    )
                    break

                elif done == new_song:
                    self.history.append(
                        (
                            self.play_from_pos,
                            self.play_from_pos + time.time() - self.play_from_time,
                        )
                    )
                    break

                elif done == stop:
                    self.history = []
                    break

            elif status.get("state") == "pause":
                play = asyncio.create_task(self.play_from())
                new_song = asyncio.create_task(self.new_song())
                stop = asyncio.create_task(self.stop())

                [done], pending = await asyncio.wait(
                    [play, new_song, stop],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                if done == play:
                    position = play.result()
                    self.set_play_from(position)

                elif done == new_song:
                    self.history.append(
                        (
                            self.play_from_pos,
                            self.play_from_pos + time.time() - self.play_from_time,
                        )
                    )
                    break

                elif done == stop:
                    self.history = []
                    break

            elif status.get("state") == "stop":
                break

        return

    def set_play_from(self, position: float):
        self.play_from_pos = position
        self.play_from_time = time.time()
        self.expected_end = time.time() + float(self.song["duration"]) - position

    def play_time(self) -> float:
        """Calculate how many seconds of the song were played, based on the playback ranges."""
        if not self.history:
            return 0

        self.history.sort(key=lambda x: x[0])

        total_play_time = 0
        current_start = self.history[0][0]
        current_end = self.history[0][1]

        for start, end in self.history[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                total_play_time += current_end - current_start
                current_start = start
                current_end = end

        total_play_time += current_end - current_start

        return total_play_time

    def status(self) -> Literal["played", "skipped", "neither"]:
        """Calculate the play and skip threshold times"""

        play_time = self.play_time()

        if play_time == 0:
            return "neither"
        elif self.play_threshold < play_time:
            return "played"
        elif play_time < self.skip_threshold:
            return "skipped"
        else:
            return "neither"


class Plugin(BeetsPlugin):
    item_types = {
        "play_count": types.INTEGER,
        "skip_count": types.INTEGER,
        "last_played": DateType(),
    }
    album_types = {"last_played": DateType()}

    def __init__(self, name=None):
        super().__init__(name)

        self.mpd_client = MPDClient()

    def set_played(self, item: BeetSong):
        # update song
        item["play_count"] = item.get("play_count", 0) + 1
        item["last_played"] = time.time()
        item.store()

        # update album
        album = item.get_album()
        if album:
            album["last_played"] = min(
                song.get("last_played", inf) for song in album.items()
            )
            album.store(inherit=False)

    def set_skipped(self, item: BeetSong):
        item["skip_count"] = item.get("skip_count", 0) + 1
        item.store()

    async def run(self, lib):
        self.mpd_client.disconnect()

        try:
            await self.mpd_client.connect("localhost", 6600)
            print("connected to MPD version,", self.mpd_client.mpd_version)
        except Exception as e:
            raise Exception(f"Connection failed: {e}")

        while True:
            song = await Song.now_playing(self.mpd_client, lib)
            tracker = await Tracker.track(self.mpd_client, song.mpd)
            playback_status = tracker.status()

            if playback_status == "played":
                self.set_played(song.beet)
            elif playback_status == "skipped":
                self.set_skipped(song.beet)

    def commands(self):
        def _func(lib, opts, args):
            asyncio.run(self.run(lib))

        cmd = Subcommand(
            "playbacktracker",
            help="Log play count, skip count, and last played from MPD",
            aliases=("pt",),
        )
        cmd.func = _func

        return [cmd]
