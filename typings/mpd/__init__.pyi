from typing import Literal, TypedDict

class StatusBase(TypedDict):
    repeat: Literal["0", "1"]
    random: Literal["0", "1"]
    single: Literal["0", "1", "oneshot"]
    consume: Literal["0", "1", "oneshot"]
    partition: str
    playlist: str
    playlistlength: str
    mixrampdb: str
    state: Literal["play", "stop", "pause"]

class Status(StatusBase, total=False):
    song: str
    songid: str
    time: str
    elapsed: str
    bitrate: str
    duration: str
    audio: str
    nextsong: str
    nextsongid: str

# Using functional syntax because "last-modified" has a dash
TrackBase = TypedDict(
    "TrackBase",
    {
        "file": str,
        "last-modified": str,
        "format": str,
        "duration": str,
        "time": str,
        "pos": str,
        "id": str,
    },
)

class Track(TrackBase, total=False):
    artist: str
    albumartist: str
    artistsort: str
    title: str
    album: str
    track: str
    date: str
    originaldate: str
    genre: str
    disc: str
    label: str
    albumartistsort: str
    musicbrainz_workid: str
    musicbrainz_albumid: str
    musicbrainz_artistid: str
    musicbrainz_albumartistid: str
    musicbrainz_releasetrackid: str
    musicbrainz_trackid: str

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
