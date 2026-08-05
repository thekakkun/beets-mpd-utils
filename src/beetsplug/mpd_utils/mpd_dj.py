"""A Beets plugin to auto-add songs to the MPD queue."""

import asyncio
import itertools
import os
from collections.abc import Iterator
from optparse import Values

import beets
from beets.dbcore import Results
from beets.dbcore.query import PathQuery
from beets.dbcore.sort import Sort
from beets.exceptions import UserError
from beets.library import Album, Item, Library
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, decargs
from confuse import Subview
from mpd.asyncio import MPDClient

from .utils import debounce


class MPDDjPlugin(BeetsPlugin):
    """The mpd_dj plugin.

    Start the plugin by calling `beet dj`.
    """

    def __init__(self, name=None):
        super().__init__(name)

        self.mpd_config = self.init_mpd_config()
        self.music_dir = beets.config["directory"].get(str)

    def init_mpd_config(self) -> Subview:
        mpd_config = beets.config["mpd"]

        mpd_config["password"].redact = True
        mpd_config.add(
            {
                "host": os.environ.get("MPD_HOST", "localhost"),
                "port": int(os.environ.get("MPD_PORT", "6600")),
                "password": "",
            }
        )

        return mpd_config

    async def mpd_client(self) -> MPDClient:
        client = MPDClient()

        await client.disconnect()

        try:
            await client.connect(
                self.mpd_config["host"].get(),
                self.mpd_config["port"].get(),
            )
        except Exception as exc:
            raise UserError(f"MPD connection failed: {exc}") from exc

        await client.password(self.mpd_config["password"].get())

        try:
            await client.status()
        except Exception as exc:
            raise UserError(f"MPD status error: {exc}") from exc

        return client

    def commands(self):
        cmd = Subcommand("dj", help="Auto-add songs to the MPD queue")
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
        cmd.func = lambda lib, opts, args: asyncio.run(self.run(lib, opts, args))

        return [cmd]

    async def run(
        self,
        lib: Library,
        opts: Values,
        args: list[str],
    ):
        """Main plugin function.
        Connect to MPD, upcoming items, and add accordingly.
        """

        client = await self.mpd_client()

        async for _ in debounce(await client.idle(["playlist", "player"]), 0.5):
            upcoming_items = await self.get_upcoming_items(lib, opts, client)
            if upcoming_items is None:
                continue

            deficit = opts.items - len(upcoming_items)
            if deficit <= 0:
                continue

            to_queue = self.get_items_to_add(lib, opts, args, deficit)
            for uri in to_queue:
                await client.add(uri)

    async def get_upcoming_items(
        self,
        lib: Library,
        opts: Values,
        client: MPDClient,
    ) -> set[int] | None:
        # Turn off random mode, as we need to know what songs are upcoming
        await client.random(0)

        status = await client.status()

        # Don't do anything if playlist is empty
        # (allows user to completely clear queue).
        if status["playlistlength"] == "0":
            return None

        item_paths = itertools.islice(
            await client.playlist(),
            int(status.get("song", 0)),
        )

        items = set()

        for path in item_paths:
            full_path = os.path.join(self.music_dir, path.replace("file: ", ""))

            path_query = PathQuery("path", full_path.encode())

            if item := lib.items(path_query).get():
                if opts.album and (album := item.get_album()):
                    items.add(album.get("id", 0))
                elif item_id := item.id:
                    items.add(item_id)

        return items

    def get_items_to_add(
        self,
        lib: Library,
        opts: Values,
        args: list[str],
        num: int,
    ) -> Iterator[str]:
        """Get the specified number of items from the library, as paths."""
        items: Results[Item] | Results[Album]

        if opts.album:
            items = lib.albums(decargs(args), sort=RandomSort(num))
            item_paths = (item.item_dir().decode() for item in items)
        else:
            items = lib.items(decargs(args), sort=RandomSort(num))
            item_paths = (item.destination().decode() for item in items)

        for path in item_paths:
            yield os.path.relpath(path, start=os.path.expanduser(self.music_dir))


class RandomSort(Sort):
    """Subclass of Sort, to return random items from the query."""

    def __init__(self, count=None) -> None:
        super().__init__()

        self.count = count if count else 1

    def order_clause(self):
        return f"RANDOM() LIMIT {self.count}"
