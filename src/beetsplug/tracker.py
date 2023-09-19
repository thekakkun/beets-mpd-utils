"""A Beets plugin to track MPD playback status."""

import asyncio
import os
import time

import beets
from beets import library, plugins, ui
from beets.dbcore import types
from mpd import MPDError
from mpd.asyncio import MPDClient

mpd_config = beets.config["mpd"]
music_dir = beets.config["directory"].get(str)
time_format = beets.config["time_format"].get(str)


class MPDTrackerPlugin(plugins.BeetsPlugin):
    """The mpd_tracker plugin.

    Start the plugin by calling `beet tracker`.
    """

    item_types = {
        "play_count": types.INTEGER,
        "skip_count": types.INTEGER,
        "last_played": library.DateType(),
    }
    album_types = {"last_played": library.DateType()}

    def __init__(self, name=None):
        super().__init__(name)

        self.config.add(
            {
                "play_time": 240,
                "play_percent": 0.5,
                "skip_time": 20,
                "skip_percent": 0,
            }
        )

        mpd_config.add(
            {
                "host": os.environ.get("MPD_HOST", "localhost"),
                "port": int(os.environ.get("MPD_PORT", 6600)),
                "password": "",
            }
        )
        mpd_config["password"].redact = True

    async def run(self, lib):
        """Main plugin function. Connect to MPD, then start tracking songs."""

        mpd_tracker = MPDTracker(self.config, self._log)
        await mpd_tracker.initialize()

        while True:
            song = await mpd_tracker.now_playing()
            playback_status = await mpd_tracker.track(song)

            if playback_status == "played":
                self.set_played(lib, song)
            elif playback_status == "skipped":
                self.set_skipped(lib, song)

    def set_played(self, item: library.Item):
        """Increment the `play_count` flexible attribute for the item, and set `last_played`

        In addition, if all songs in the album have been played at some point,
        set the album's `last_played` flexible attribute as the oldest `last_played`
        attribuite of the songs in the album.
        """

        # set song metadata
        item["play_count"] = item.get("play_count", 0) + 1
        item["last_played"] = time.time()
        item.store()
        self._log.info(
            "{} played {} times at {}",
            item,
            item["play_count"],
            time.strftime(time_format, time.localtime(item["last_played"])),
        )

        # set album metadata
        album = item.get_album()
        if album:
            songs_last_played_at = [song.get("last_played") for song in album.items()]

            if all(songs_last_played_at):
                album["last_played"] = min(songs_last_played_at)
                album.store(inherit=False)
                self._log.info(
                    "{} last played at {}",
                    album,
                    time.strftime(time_format, time.localtime(album["last_played"])),
                )

    def set_skipped(self, item: library.Item):
        """Increment the `skip_count` flexible attribute for the item."""

        item["skip_count"] = item.get("skip_count", 0) + 1
        self._log.info("{} skipped", item)
        item.store()

    def commands(self):
        def _func(lib, _opts, _args):
            asyncio.run(self.run(lib))

        cmd = ui.Subcommand(
            "tracker",
            help="Log play count, skip count, and last played from MPD",
        )
        cmd.func = _func

        return [cmd]


class PlaybackHistory:
    """Store playback history as a list of play/pause positions."""

    def __init__(self) -> None:
        self.history = []
        self.play_from_pos = 0
        self.play_from_time = 0
        self.expected_end = 0

    def play_from(
        self,
        position: float,
        duration: float,
    ):
        """Record history of playback start. Recalculate expected end time."""

        self.play_from_pos = position
        self.play_from_time = time.time()
        self.expected_end = time.time() + duration - position

    def play_to(self, position: float):
        """Add play range to history."""

        self.history.append((self.play_from_pos, position))

    def play_to_now(self):
        """Add play range to history. Extrapolate range end from current time."""

        self.play_to(self.play_from_pos + time.time() - self.play_from_time)

    def clear(self):
        """Clear history."""

        self.history = []
        self.play_from_pos = 0
        self.play_from_time = 0
        self.expected_end = 0

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


class MPDTracker(MPDClient):
    """Tracks the playback of songs on MPD, and updates beets metadata accordingly.

    Default thresholds:
    - Play: more than 50% of track played or 240 seconds
    - Skip: less than 0% of track played or 20 seconds
    """

    def __init__(self, config, log, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.log = log
        self.config = config

        self.song_done = asyncio.Event()

    async def initialize(self):
        """Connect to MPD"""

        self.disconnect()

        try:
            await self.connect(mpd_config["host"].get(), mpd_config["port"].get())
        except Exception as exc:
            raise ui.UserError(f"Connection failed: {exc}") from exc

    async def now_playing(self):
        """Wait for a song to be loaded in MPD, then get the Beets item."""

        while (await self.status()).get("state") == "stop":
            self.log.debug("Player stopped. Waiting for song.")
            async for _ in self.idle(["player"]):
                break

        return await self.currentsong()

    async def track(self, song):
        task = asyncio.create_task(self._task(song))
        playback_history = await task
        # return self.playback_status()

    async def _task(self, song) -> PlaybackHistory:
        self.song_done.clear()
        playback_history = PlaybackHistory()

        elapsed = (await self.status()).get("elapsed")
        if elapsed:
            playback_history.play_to(float(elapsed))
            playback_history.play_from(float(elapsed), float(song["duration"]))

        async for _ in self.idle(["player"]):
            status = await self.status()

            if status.get("state") == "play":
                self.handle_play_state(playback_history, song)
            elif status.get("state") == "pause":
                pass
            elif status.get("state") == "stop":
                playback_history.clear()
                self.song_done.set()

            if self.song_done.is_set():
                return playback_history

    async def handle_play_state(self, playback_history: PlaybackHistory, song):
        """Handle events that may occor when state=='play'."""

        await asyncio.gather(
            self.pause_event(playback_history),
            self.seek_event(playback_history, song),
            self.replay_event(playback_history, song),
            self.new_song_event(playback_history, song),
            self.stop_event(playback_history),
            self.playlist_end_event(playback_history, song),
        )

    async def handle_pause_state(self, playback_history: PlaybackHistory, song):
        """Handle events that may occor when state=='pause'."""

        await asyncio.gather(
            self.play_event(playback_history, song),
            self.new_song_event(playback_history, song),
            self.stop_event(playback_history),
        )

    async def play_event(self, playback_history: PlaybackHistory, song):
        """Start playback."""

        status = await self.status()

        if status.get("state") == "play" and song == await self.currentsong():
            try:
                position = float(status["elapsed"])
            except Exception as exc:
                raise NoElapsedError() from exc

            playback_history.play_from(position, float(song["duration"]))

    async def pause_event(self, playback_history: PlaybackHistory):
        """Playback paused."""

        status = await self.status()

        if status.get("state") == "pause":
            try:
                position = float(status["elapsed"])
            except Exception as exc:
                raise NoElapsedError() from exc

            playback_history.play_to(position)

    async def seek_event(self, playback_history: PlaybackHistory, song):
        """Player seeked.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `replay` events.
        """

        status = await self.status()

        if (
            status.get("state") == "play"
            and song == await self.currentsong()
            and 1 < abs(time.time() - playback_history.expected_end)
        ):
            try:
                position = float(status["elapsed"])
            except Exception as exc:
                raise NoElapsedError() from exc

            playback_history.play_to_now()
            playback_history.play_from(position, float(song["duration"]))

    async def replay_event(self, playback_history: PlaybackHistory, song):
        """Song replayed.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `seek_to` events.
        """

        status = await self.status()

        if (
            status.get("state") == "play"
            and song == await self.currentsong()
            and abs(time.time() - playback_history.expected_end) < 1
        ):
            playback_history.play_to(float(song["duration"]))
            self.song_done.set()

    async def new_song_event(self, playback_history: PlaybackHistory, song):
        """New song queued in player."""

        status = await self.status()

        if status.get("state") == "play" and song != await self.currentsong():
            playback_history.play_to_now()
            self.song_done.set()

    async def stop_event(self, playback_history: PlaybackHistory):
        """Player stopped.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `playlist_end` events.
        """

        status = await self.status()

        if status.get("state") == "stop" and 1 < abs(
            time.time() - playback_history.expected_end
        ):
            playback_history.clear()
            self.song_done.set()

    async def playlist_end_event(self, playback_history: PlaybackHistory, song):
        """Reached end of playlist.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `stop` events.
        """

        status = await self.status()

        if (
            status.get("state") == "stop"
            and abs(time.time() - playback_history.expected_end) < 1
        ):
            playback_history.play_to(float(song["duration"]))
            self.song_done.set()

    def command_list_end(self):
        pass

    def command_list_ok_begin(self):
        pass


class NoElapsedError(MPDError):
    """MPD has no playback position data."""
