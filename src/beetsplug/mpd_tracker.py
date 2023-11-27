"""A Beets plugin to track MPD playback status."""

import asyncio
import logging
import os
import time
import typing

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

    def commands(self):
        def _func(lib, _opts, _args):
            asyncio.run(self.run(lib))

        cmd = ui.Subcommand(
            "tracker",
            help="Log play count, skip count, and last played from MPD",
        )
        cmd.func = _func

        return [cmd]

    async def run(self, lib: library.Library):
        """Main plugin function. Connect to MPD, then start tracking songs."""

        mpd_tracker = await MPDTracker.initialize(self.config, self._log)

        while True:
            (song, playback_status) = await mpd_tracker.run()

            if playback_status == "played":
                self.set_played(lib, song)
            elif playback_status == "skipped":
                self.set_skipped(lib, song)

    def set_played(self, lib: library.Library, song: dict):
        """Increment the `play_count` flexible attribute for the item, and set `last_played`

        In addition, if all songs in the album have been played at some point,
        set the album's `last_played` flexible attribute as the oldest `last_played`
        attribuite of the songs in the album.
        """

        query = library.PathQuery("path", os.path.join(music_dir, song["file"]))
        item = lib.items(query).get()

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

    def set_skipped(self, lib: library.Library, song: dict):
        """Increment the `skip_count` flexible attribute for the item."""

        query = library.PathQuery("path", os.path.join(music_dir, song["file"]))
        item = lib.items(query).get()

        item["skip_count"] = item.get("skip_count", 0) + 1
        self._log.info("{} skipped", item)
        item.store()


class PlaybackHistory:
    """Store playback history as a list of play/pause positions."""

    def __init__(self, log: logging.Logger, duration: float) -> None:
        self.log = log

        self.duration = duration
        self.history = []
        self.play_from_pos = 0
        self.play_from_time = 0
        self.expected_end = 0

    def play_from(
        self,
        position: float,
    ):
        """Record history of playback start. Recalculate expected end time."""

        self.log.debug("playing from {}", position)

        self.play_from_pos = position
        self.play_from_time = time.time()
        self.expected_end = time.time() + self.duration - position

    def play_to(self, position: float):
        """Add play range to history."""

        self.log.debug("played to {}", position)

        self.history.append((self.play_from_pos, position))

    def play_to_now(self):
        """Add play range to history. Extrapolate range end from current time."""

        self.play_to(self.play_from_pos + time.time() - self.play_from_time)

    def play_to_end(self):
        """Assume played to end of song."""
        self.play_to(self.duration)

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

    song: dict
    playback_history: PlaybackHistory

    def __init__(
        self, config: beets.IncludeLazyConfig, log: logging.Logger, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.log = log
        self.config = config

    @classmethod
    async def initialize(
        cls, config: beets.IncludeLazyConfig, log: logging.Logger
    ) -> typing.Self:
        """Main initializer for the tracker."""

        self = MPDTracker(config, log)

        self.disconnect()

        try:
            await self.connect(mpd_config["host"].get(), mpd_config["port"].get())
        except Exception as exc:
            raise ui.UserError(f"Connection failed: {exc}") from exc

        return self

    async def run(self) -> tuple[dict, typing.Literal["played", "skipped", "neither"]]:
        """Main initializer for the tracker.

        Connects to MPD, tracks the currently playing song,
        then returns song info and playback state.
        """

        # Load a song
        while (await self.status()).get("state") == "stop":
            self.log.debug("Player stopped. Waiting for song.")
            async for _ in self.idle(["player"]):
                break

        self.song = await self.currentsong()
        self.playback_history = PlaybackHistory(self.log, float(self.song["duration"]))

        self.log.debug(f"Start tracking: {self.song['artist']} - {self.song['title']}")

        # Start tracking
        task = asyncio.create_task(self._task())
        await task

        return (self.song, self.playback_status())

    async def _task(self) -> PlaybackHistory:
        elapsed = (await self.status()).get("elapsed")
        if elapsed:
            self.playback_history.play_to(float(elapsed))
            self.playback_history.play_from(float(elapsed))

        status = await self.status()

        async for _ in self.idle(["player"]):
            state = status.get("state")

            status = await self.status()
            song = await self.currentsong()

            if state == "play":
                song_done = self.handle_play_state(status, song)

            elif state == "pause":
                song_done = self.handle_pause_state(status, song)

            elif state == "stop":
                self.playback_history.clear()
                song_done = True

            if song_done:
                break

    def handle_play_state(self, status: dict, song: dict) -> bool:
        """Handle events that may occur when state=='play'."""

        song_done = False

        if self.is_pause(status, song):
            try:
                self.playback_history.play_to(float(status["elapsed"]))
            except Exception as exc:
                raise NoElapsedError() from exc

        elif self.is_seek(status, song):
            self.playback_history.play_to_now()
            try:
                self.playback_history.play_from(float(status["elapsed"]))
            except Exception as exc:
                raise NoElapsedError() from exc

        elif self.is_replay(status, song):
            self.playback_history.play_to_end()
            song_done = True

        elif self.is_new_song(status, song):
            self.playback_history.play_to_now()
            song_done = True

        elif self.is_stop(status):
            self.playback_history.clear()
            song_done = True

        elif self.is_playlist_end(status):
            self.playback_history.play_to_end()
            song_done = True

        return song_done

    def handle_pause_state(self, status: dict, song: dict) -> bool:
        """Handle events that may occor when state=='pause'."""

        song_done = False

        if self.is_play(status, song):
            try:
                self.playback_history.play_from(float(status["elapsed"]))
            except Exception as exc:
                raise NoElapsedError() from exc

        elif self.is_new_song(status, song):
            self.playback_history.play_to_now()
            song_done = True

        elif self.is_stop(status):
            self.playback_history.clear()
            song_done = True

        return song_done

    def is_play(self, status: dict, song: dict) -> bool:
        """Start playback."""

        return status.get("state") == "play" and self.song == song

    def is_pause(self, status: dict, song: dict):
        """Playback paused."""

        return status.get("state") == "pause" and self.song == song

    def is_seek(self, status: dict, song: dict) -> bool:
        """Player seeked.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `replay` events.
        """

        return (
            status.get("state") == "play"
            and self.song == song
            and 1 < abs(time.time() - self.playback_history.expected_end)
        )

    def is_replay(self, status: dict, song: dict) -> bool:
        """Song replayed.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `seek_to` events.
        """

        return (
            status.get("state") == "play"
            and self.song == song
            and abs(time.time() - self.playback_history.expected_end) < 1
        )

    def is_new_song(self, status: dict, song: dict) -> bool:
        """New song queued in player."""

        return (
            status.get("state") == "play" or status.get("state") == "pause"
        ) and self.song != song

    def is_stop(self, status: dict) -> bool:
        """Player stopped.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `playlist_end` events.
        """

        return status.get("state") == "stop" and 1 < abs(
            time.time() - self.playback_history.expected_end
        )

    def is_playlist_end(self, status: dict) -> bool:
        """Reached end of playlist.

        This requires the time we expect the song to naturally end, so that it can be
        differentiated from `stop` events.
        """

        return (
            status.get("state") == "stop"
            and abs(time.time() - self.playback_history.expected_end) < 1
        )

    def playback_status(self) -> typing.Literal["played", "skipped", "neither"]:
        """Calculate the play and skip threshold times, return playback state for song."""

        play_time = self.playback_history.play_time()

        if play_time == 0:
            return "neither"

        play_threshold = min(
            self.config["play_time"].get(int),
            float(self.song["duration"]) * self.config["play_percent"].get(float),
        )
        skip_threshold = max(
            self.config["skip_time"].get(int),
            float(self.song["duration"]) * self.config["skip_percent"].get(float),
        )

        if play_threshold < play_time:
            return "played"
        if play_time < skip_threshold:
            return "skipped"
        return "neither"

    def command_list_end(self):
        pass

    def command_list_ok_begin(self):
        pass


class NoElapsedError(MPDError):
    """MPD has no playback position data."""
