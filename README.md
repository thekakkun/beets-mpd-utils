# beets-mpd-utils

[beets](https://beets.io/) plugins for managing music metadata and playback using [Music Player Daemon](https://www.musicpd.org/).

## Installation

Install the plugin

```
pip install git+https://github.com/thekakkun/music_utilities.git
```

Enable the plugin by adding it the `plugins` option in your beets config.

```
plugins: mpd_tracker
```

## Provided Plugins

### MPD Tracker

The `mpd_tracker` plugin tracks song plays and skips on MPD and records them in the following flexible attributes:

- Song
  - `play_count`: The number of times the song has been played. Defined as playing more than 50% or 240 seconds of the song, by default.
  - `last_played`: When the `play_count` was last updated.
  - `skip_count`: The number of times the song has beed skipped. Defined as changing the song before 20 seconds of the song has been played, by default.
- Album
  - `last_played`: Only written once every song in the album has been played. Defined as the oldest `last_played` value for the songs in the album.

#### Configuration

To configure, make a `mpd_tracker` section in your beets config file. Songs will be considered played/skipped if either of the thresholds are met.

The available options are:

- **play_time**: The amount of seconds played after which the song will be considered "played". Default: `240`.
- **play_percent**: The percentage of the song that needs to be played before being considered "played". Expects a value between `0` and `1`, default: `0.5`.
- **skip_time**: The amount of seconds played before which the song will be considered "skipped". Default: `20`.
- **skip_percent**: The percentage of the song that needs to be played before which the song will be considered "skipped". Expects a value between `0` and `1`, default: `0.0`.

## MPD DJ

Auto-add songs into the MPD queue. Under construction.
