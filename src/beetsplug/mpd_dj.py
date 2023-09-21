"""A Beets plugin to auto-add songs to the MPD queue."""

import asyncio
import itertools
import logging
import optparse  # pylint: disable=deprecated-module
import os

import beets
from beets import library, plugins, ui
from beets.dbcore import query
from mpd.asyncio import MPDClient

mpd_config = beets.config["mpd"]
music_dir = beets.config["directory"].get(str)


class MPDDjPlugin(plugins.BeetsPlugin):
    """The mpd_dj plugin.

    Start the plugin by calling `beet dj`.
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
        cmd.parser.add_album_option()
        cmd.func = _func

        return [cmd]

    async def run(self, lib: library.Library, opts: optparse.Values, args: list[str]):
        """Main plugin function. Connect to MPD, upcoming items, and add accordingly."""

        mpd_queue = await MPDQueue.initialize(lib, self._log)

        while True:
            item_paths = await mpd_queue.upcoming_items()
            upcoming_items = self.count_items(lib, opts, item_paths)

            deficit = opts.items - len(upcoming_items)

            if deficit <= 0:
                continue

            to_queue = self.get_items(lib, opts, args, deficit)
            for uri in to_queue:
                mpd_queue.add(uri)

    def count_items(
        self, lib: library.Library, opts: optparse.Values, item_paths: list[str]
    ) -> set[int]:
        """From a list of paths to items, return a set of the unique items in the list."""

        items = set()

        for path in item_paths:
            path_query = library.PathQuery("path", path)
            item = lib.items(path_query).get()

            if opts.album:
                items.add(item.get_album().id)
            else:
                items.add(item.id)

        return items

    def get_items(
        self, lib: library.Library, opts: optparse.Values, args: list[str], num: int
    ) -> list[str]:
        """Get the specified number of items from the library, as paths."""

        if opts.album:
            items = lib.albums(ui.decargs(args), sort=RandomSort(num))
            item_paths = [item.item_dir().decode("utf-8") for item in items]
        else:
            items = lib.items(ui.decargs(args), sort=RandomSort(num))
            item_paths = [item.destination().decode("utf-8") for item in items]

        return [
            os.path.relpath(path, start=os.path.expanduser(music_dir))
            for path in item_paths
        ]


class MPDQueue(MPDClient):
    """Wrapper for the MPD client."""

    def __init__(self, lib: library.Library, log: logging.Logger, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.lib = lib
        self.log = log

    @classmethod
    async def initialize(cls, lib: library.Library, log: logging.Logger):
        """Main initializer for the queue."""

        self = MPDQueue(lib, log)

        self.disconnect()

        try:
            await self.connect(mpd_config["host"].get(), mpd_config["port"].get())
        except Exception as exc:
            raise ui.UserError(f"Connection failed: {exc}") from exc

        return self

    async def upcoming_items(self) -> list[str]:
        """Return a list of paths to the items upcoming in the queue."""
        async for _ in self.idle(["playlist", "player"]):
            # Turn off random mode, as we need to know what songs are upcoming
            self.random(0)
            status = await self.status()

            # Don't do anything if playlist is empty (allows user to completely clear queue).
            if int(status["playlistlength"]) == 0:
                continue

            upcoming_items = itertools.islice(
                await self.playlist(),
                int(status.get("song", 0)),
                int(status["playlistlength"]),
            )

            return [
                os.path.join(music_dir, item.replace("file: ", ""))
                for item in upcoming_items
            ]

    def command_list_end(self):
        pass

    def command_list_ok_begin(self):
        pass


class RandomSort(query.Sort):
    """Subclass of Sort, to return random items from the query."""

    def __init__(self, count=None) -> None:
        super().__init__()

        self.count = count if count else 1

    def order_clause(self):
        return f"RANDOM() LIMIT {self.count}"
