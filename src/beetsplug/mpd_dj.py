"""A Beets plugin to auto-add songs to the MPD queue."""

import asyncio
import itertools
import os
from logging import Logger

import beets
from beets import library, plugins, ui
from beets.dbcore import query
from mpd.asyncio import MPDClient

mpd_config = beets.config["mpd"]
music_dir = beets.config["directory"].get(str)


class MPDDjPlugin(plugins.BeetsPlugin):
    """The mpd_dj plugin.

    Start by calling `beet dj`.
    """

    def __init__(self, name=None):
        super().__init__(name)

        mpd_config.add(
            {
                "host": os.environ.get("MPD_HOST", "localhost"),
                "port": int(os.environ.get("MPD_PORT", 6600)),
                "password": "",
            }
        )
        mpd_config["password"].redact = True

    async def run(self, lib, opts, args):
        mpd_queue = MPDQueue(lib, self._log)
        await mpd_queue.initialize()

        async for _ in mpd_queue.idle(["playlist", "player"]):
            items = await mpd_queue.upcoming_items(opts.album)
            deficit = opts.items - len(items)

            if deficit <= 0:
                continue

            if opts.album:
                items = lib.albums(ui.decargs(args), sort=RandomSort(deficit))
                item_paths = [item.item_dir().decode("utf-8") for item in items]
            else:
                items = lib.items(ui.decargs(args), sort=RandomSort(deficit))
                item_paths = [item.destination().decode("utf-8") for item in items]

            item_uris = [
                os.path.relpath(path, start=os.path.expanduser(music_dir))
                for path in item_paths
            ]

            for uri in item_uris:
                mpd_queue.add(uri)

    def commands(self):
        def _func(lib, opts, args):
            asyncio.run(self.run(lib, opts, args))

        cmd = ui.Subcommand("dj", help="Auto-add songs to the MPD queue")
        cmd.parser.add_option(
            "-n",
            "--number",
            action="store",
            type="int",
            dest="items",
            default=20,
            help="number of items to maintain in the queue",
        )
        cmd.parser.add_option(
            "-a",
            "--album",
            action="store_true",
            dest="album",
            default=False,
            help="Auto-queue albums instead of songs",
        )

        cmd.func = _func

        return [cmd]


class MPDQueue(MPDClient):
    def __init__(self, lib: library.Library, log: Logger, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.lib = lib
        self.log = log

    async def initialize(self):
        """Connect to MPD"""

        self.disconnect()

        try:
            await self.connect(mpd_config["host"].get(), mpd_config["port"].get())
        except Exception as exc:
            raise ui.UserError(f"Connection failed: {exc}") from exc

    async def upcoming_items(self, album: bool):
        # Turn off random mode, as we need to know what songs are upcoming
        self.random(0)
        status = await self.status()
        items = set()

        upcoming_items = itertools.islice(
            await self.playlist(),
            int(status.get("song", 0)) + 1,
            int(status["playlistlength"]),
        )

        for song_path in upcoming_items:
            abs_path = os.path.join(music_dir, song_path.replace("file: ", ""))
            path_query = library.PathQuery("path", abs_path)
            song = self.lib.items(path_query).get()

            if album:
                items.add(song.get_album().id)
            else:
                items.add(song.id)

        return items


class RandomSort(query.Sort):
    def __init__(self, count=None) -> None:
        super().__init__()

        self.count = count if count else 1

    def order_clause(self):
        return f"RANDOM() LIMIT {self.count}"
