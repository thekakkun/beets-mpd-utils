from beets.plugins import BeetsPlugin
from beets.ui import Subcommand
from mpd import MPDClient
import sys


class MusicUtil(BeetsPlugin):
    def commands(self):
        cmd = Subcommand("mpd_status", help="get mpd status")

        def func(lib, opts, args):
            print(sys.path)
            client = MPDClient()
            client.connect(host="localhost", port=6600)
            status = client.status()
            print(status)

        cmd.func = func

        return [cmd]
