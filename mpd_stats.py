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


class Player:
    def __init__(
        self,
        play_time: int = 240,
        play_percent: int = 50,
        skip_time: int = 20,
        skip_percent: int = 0,
    ) -> None:
        # play and skip threshold settings
        self.play_time = play_time
        self.play_percent = play_percent
        self.skip_time = skip_time
        self.skip_percent = skip_percent

        # playback state
        self.elapsed = 0
        self.is_played = False

        self.track: Optional[Track] = None
        self.playback_task: Optional[asyncio.Task] = None

    def change_track(self, track: Track):
        previous_played = self.is_played
        previous_skipped = False

        if self.track:
            skip_threshold = max(
                self.skip_time,
                (float(self.track["duration"]) * self.skip_percent) // 100,
            )
            previous_skipped = self.elapsed < skip_threshold

        if previous_played:
            print("previous track was played")
        elif previous_skipped:
            print("previous track was skipped")
        else:
            print("previous track was neither played nor skipped")

        self.track = track
        self.elapsed = 0
        self.is_played = False

        return (previous_played, previous_skipped)

    def play(self, position: Optional[float] = None):
        async def coro(self):
            if self.track:
                play_threshold = min(
                    self.play_time,
                    (float(self.track["duration"]) * self.play_percent) // 100,
                )

                while self.elapsed < play_threshold:
                    print(self.elapsed)
                    await asyncio.sleep(1)
                    self.elapsed += 1

        def set_played(_):
            print("track considered played")
            self.is_played = True

        if position:
            self.elapsed = floor(position)

        self.playback_task = asyncio.create_task(coro(self))
        self.playback_task.add_done_callback(set_played)

    def pause(self):
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()

    def stop(self):
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()

        self.track = None
        self.playback_task = None

    def seek(self, time: float):
        self.elapsed = min(floor(self.elapsed), time)


class MPDWrapper:
    status: Status

    def __init__(self) -> None:
        self.client = MPDClient()
        self.client.disconnect()
        self.player = Player()

    async def connect(self):
        try:
            await self.client.connect("localhost", 6600)
        except Exception as e:
            print(f"Connection failed: {e}")

    async def handle_subsystem(self, subsystem):
        match subsystem:
            case "player":
                await self.handle_player()
            case _:
                self.status = await self.client.status()
                print(f"change in subsytem: {subsystem}")

    async def handle_player(self):
        track = await self.client.currentsong()
        status = await self.client.status()

        if status["state"] == "play":
            if self.status["state"] == "stop":
                print("starting playback")
            elif self.status["state"] == "play":
                if track == self.current_track:
                    if float(status["elapsed"]) < 1:
                        print("replaying track")
                    else:
                        print("seeking playing track")
                else:
                    print("change playing track")
                    self.current_track = track
            elif self.status["state"] == "pause":
                if track == self.current_track:
                    print("continuing playback")
                else:
                    print("start playing track")
                    self.current_track = track
        elif status["state"] == "pause":
            if self.status["state"] == "pause":
                print("seeking paused track")
            else:
                print("paused track")
        elif status["state"] == "stop":
            print("stopped player")
            self.current_track = {}

        self.status = status


async def main():
    player = MPDWrapper()
    await player.connect()

    track = await player.client.currentsong()
    if track:
        player.player.change_track(track)

    status = await player.client.status()
    if status:
        player.status = status

        if player.status["state"] == "play":
            player.player.play(float(player.status["elapsed"]))

    print("connected to MPD version,", player.client.mpd_version)

    async for subsystems in player.client.idle():
        for subsystem in subsystems:
            await player.handle_subsystem(subsystem)


if __name__ == "__main__":
    asyncio.run(main())
