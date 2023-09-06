import asyncio
import time
from typing import Literal, TypedDict

from beets.dbcore import types
from beets.library import DateType, Library
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand
from mpd.asyncio import MPDClient
from mpd_types import Track


class PlayFrom(TypedDict):
    location: float
    time: float
    end_at: float


PlaybackStatus = Literal["played", "skipped", "neither"]


class PlaybackTracker:
    def __init__(
        self,
        play_time: int = 240,
        play_percent: float = 0.5,
        skip_time: int = 20,
        skip_percent: float = 0,
    ):
        self.mpd = MPDClient()

        self.play_time = play_time
        self.play_percent = play_percent
        self.skip_time = skip_time
        self.skip_percent = skip_percent
        self.play_threshold: float
        self.skip_threshold: float

        self.song: Track
        self.play_from: PlayFrom
        self.playback_history: list[tuple[float, float]]

    async def run(self, lib: Library):
        self.mpd.disconnect()

        try:
            await self.mpd.connect("localhost", 6600)
            print("connected to MPD version,", self.mpd.mpd_version)

            self.task = asyncio.create_task(self.track(lib))

        except Exception as e:
            raise Exception(f"Connection failed: {e}")

    async def track(self, lib: Library):
        while True:
            await self.set_song()
            await self.track_playback()

            print(self.playback_history)
            print(f"played {self.get_play_time()} seconds of track")
            print("\n")

    async def set_song(self):
        status = await self.mpd.status()

        # if player is in "stop" state, wait until otherwise
        while status.get("state") == "stop":
            async for _ in self.mpd.idle(["player"]):
                status = await self.mpd.status()
                break

        self.song = await self.mpd.currentsong()
        print(
            f"song: {self.song.get('artist', 'unknown')} - {self.song.get('title', 'unknown')}"
        )

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
        status = await self.mpd.status()

        # If tracker is starting mid-song, assume we've already played to that point.
        elapsed = float(status.get("elapsed", 0))
        self.playback_history = [(0, elapsed)] if elapsed else []

        # If song is already playing, initialize play_from with values
        if status.get("state") == "play":
            print(f"- start playing from {elapsed}")
            self.play_from = {
                "location": elapsed,
                "time": time.time(),
                "end_at": time.time() + float(self.song["duration"]) - elapsed,
            }
        else:
            print(f"- start queued at {elapsed}")

        while True:
            if status.get("state") == "play":
                pause_task = asyncio.create_task(self.pause())
                seek_task = asyncio.create_task(self.seek())
                replay_task = asyncio.create_task(self.replay())
                new_song_task = asyncio.create_task(self.new_song())
                stop_task = asyncio.create_task(self.stop())

                [done], pending = await asyncio.wait(
                    [
                        pause_task,
                        seek_task,
                        replay_task,
                        new_song_task,
                        stop_task,
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                if done in [replay_task, new_song_task, stop_task]:
                    break

            elif status.get("state") == "pause":
                resume_task = asyncio.create_task(self.resume())
                new_song_task = asyncio.create_task(self.new_song())
                stop_task = asyncio.create_task(self.stop())

                [done], pending = await asyncio.wait(
                    [resume_task, new_song_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                if done in [new_song_task, stop_task]:
                    break

            elif status.get("state") == "stop":
                break

            status = await self.mpd.status()

        return

    # State set to play for song already being tracked
    async def resume(self):
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if (
                status.get("state") == "play"
                and self.song == await self.mpd.currentsong()
            ):
                try:
                    self.play_from = {
                        "location": float(status["elapsed"]),
                        "time": time.time(),
                        "end_at": time.time()
                        + float(self.song["duration"])
                        - float(status["elapsed"]),
                    }
                except KeyError:
                    continue

                print(f"- resume from {self.play_from['location']}")
                return

    # State set to pause
    async def pause(self):
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if status.get("state") == "pause":
                try:
                    self.playback_history.append(
                        (self.play_from["location"], float(status["elapsed"]))
                    )
                except KeyError:
                    continue

                print(f"- pause at {self.playback_history[-1][1]}")
                return

    # Same song, but elapsed time sufficiently different
    async def seek(self):
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if (
                status.get("state") == "play"
                and self.song == await self.mpd.currentsong()
                and 1 <= abs(time.time() - self.play_from["end_at"])
            ):
                self.playback_history.append(
                    (
                        self.play_from["location"],
                        self.play_from["location"]
                        + time.time()
                        - self.play_from["time"],
                    )
                )

                try:
                    self.play_from = {
                        "location": float(status["elapsed"]),
                        "time": time.time(),
                        "end_at": time.time()
                        + float(self.song["duration"])
                        - float(status["elapsed"]),
                    }
                except KeyError:
                    continue

                print(
                    f"- seeked from {self.playback_history[-1][1]} to {self.play_from['location']}"
                )
                return

    # Something happened around the time we expected the song to end,
    # but the song is the same. Must be a replay.
    async def replay(self):
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if (
                status.get("state") == "play"
                and self.song == await self.mpd.currentsong()
                and abs(time.time() - self.play_from["end_at"]) < 1
            ):
                self.playback_history.append(
                    (self.play_from["location"], float(self.song["duration"]))
                )

                print(f"- replay")
                print(f"  previous song played to {self.playback_history[-1][1]}")
                return

    # Song's changed
    async def new_song(self):
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if (
                status.get("state") == "play"
                and self.song != await self.mpd.currentsong()
            ):
                self.playback_history.append(
                    (
                        self.play_from["location"],
                        self.play_from["location"]
                        + time.time()
                        - self.play_from["time"],
                    )
                )

                print(f"- new song")
                print(f"  previous song played to {self.playback_history[-1][1]}")
                return

    # A stopped song is neither played or skipped
    async def stop(self):
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if status.get("state") == "stop":
                self.playback_history = []

                print("- stop")
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

        if play_time == 0:
            return "neither"
        elif self.play_threshold < play_time:
            return "played"
        elif play_time < self.skip_threshold:
            return "skipped"
        else:
            return "neither"


class PlaybackTrackerPlugin(BeetsPlugin):
    item_types = {
        "play_count": types.INTEGER,
        "skip_count": types.INTEGER,
        "last_played": DateType,
    }
    album_types = {"last_played": DateType}

    def __init__(self, name=None):
        super().__init__(name)
        self.tracker = PlaybackTracker()

    def commands(self):
        def _func(lib, opts, args):
            with asyncio.Runner() as runner:
                runner.run(self.tracker.run(lib))
                runner.get_loop().run_forever()

        cmd = Subcommand(
            "playbacktracker",
            help="Log play count, skip count, and last played from MPD",
            aliases=("pt",),
        )
        cmd.func = _func

        return [cmd]
