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


class PlaybackTracker:
    """Tracks the playback of songs on MPD, and updates beets metadata accordingly.

    Default thresholds:
    - Play: more than 50% of track played or 240 seconds
    - Skip: less than 0% of track played or 20 seconds
    """

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

        self.song: Track
        self.play_from: PlayFrom
        self.playback_history: list[tuple[float, float]]

    async def run(self, lib: Library):
        """Connect to MPD, start tracking playback."""
        self.mpd.disconnect()

        try:
            await self.mpd.connect("localhost", 6600)
            print("connected to MPD version,", self.mpd.mpd_version)

            self.task = asyncio.create_task(self.track(lib))

        except Exception as e:
            raise Exception(f"Connection failed: {e}")

    async def track(self, lib: Library):
        """Main tracking loop.

        Wait for song to be queued, track it's playback until a new song is set,
        and update beets metadata.
        """
        while True:
            await self.set_song()
            await self.track_playback()

            print(self.playback_history)
            print(f"played {self.get_play_time()} seconds of track")
            print("\n")

    async def set_song(self):
        """Wait for song to be set in MPD, then calculate play and skip thresholds for the song."""

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

    async def track_playback(self):
        """Track MPD status, and store the playback history for song."""
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
        """Fires when player resumes the current song.

        Assumes to only be awaited when the player is in the pause state.
        """
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

    async def pause(self):
        """Fires when player pauses the current song.

        Assumed to only be awaited when the player is in the play state.
        """
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
        """Fires when user seeks through the song.

        Ignored if event occurs around when we expect the song to end, in order to
        differentiate from replay events (which are also same song, different location).
        """
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

    async def replay(self):
        """Fires when the song is replayed.

        We define a replay as the player state changing around the time we expect
        then song to end, but the song has remained the same.
        """
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

    async def new_song(self):
        """Fires when there's a new song

        By the time this event occurs, it is too late to find out what location the previous
        song was played to. Therefore, we calculate it from the current time and when playback
        previously started.
        """
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

    async def stop(self):
        """Fires when the player has been stopped.

        This deletes any history information about the track that was playing, so
        the track will never be considered to be skipped or played.
        """
        async for _ in self.mpd.idle(["player"]):
            status = await self.mpd.status()

            if status.get("state") == "stop":
                self.playback_history = []

                print("- stop")
                return

    def get_play_time(self) -> float:
        """Calculate how many seconds of the song were played, based on the playback ranges."""
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

    def playback_status(self) -> Literal["played", "skipped", "neither"]:
        """Get the playback status of the current song, based on current playback history."""

        # Calculate the play and skip threshold times
        play_time = self.get_play_time()

        if play_time == 0:
            return "neither"

        try:
            play_threshold = min(
                self.play_time,
                float(self.song["duration"]) * self.play_percent,
            )
            skip_threshold = max(
                self.skip_time,
                float(self.song["duration"]) * self.skip_percent,
            )
        except KeyError:
            play_threshold = self.play_time
            skip_threshold = self.skip_time

        if play_threshold < play_time:
            return "played"
        elif play_time < skip_threshold:
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
