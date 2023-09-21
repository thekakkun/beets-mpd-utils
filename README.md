# beets-mpd-utils

Some [beets](https://beets.io/) plugins to interface with [Music Player Daemon](https://www.musicpd.org/).

- [MPD Tracker](#mpd-tracker): Track song plays/skips on MPD.
- [MPD DJ](#mpd-dj): Auto-add songs/albums to your MPD queue.

## Installation

Install the plugin.

**Note**: `mpd_tracker` requires a newer version of beets than is available on PyPI. Therefore, make sure it is installed from git.

```bash
pip install git+https://github.com/beetbox/beets.git beets-mpd-utils
```

Enable the plugin by adding it the `plugins` option in your beets config.

```yaml
plugins: mpd_tracker, mpd_dj
```

## Provided Plugins

### MPD Tracker

The `mpd_tracker` plugin tracks song plays and skips on MPD and records them in the following flexible attributes:

- Song
  - `play_count`: The number of times the song has been played.
  - `last_played`: When the `play_count` was last updated.
  - `skip_count`: The number of times the song has beed skipped.
- Album
  - `last_played`: Only written once every song in the album has been played. Defined as the oldest `last_played` value for the songs in the album.

#### Usage

Once enabled, start the tracker by typing:

```bash
beet tracker
```

#### Configuration

To configure, make a `mpd_tracker` section in your beets config file. Songs will be considered played/skipped if either of the time/percentage thresholds are met.

The available options are:

- **play_time**: The amount of seconds played after which the song will be considered "played". Default: `240`.
- **play_percent**: The percentage of the song that needs to be played before being considered "played". Expects a value between `0` and `1`, default: `0.5`.
- **skip_time**: The amount of seconds played before which the song will be considered "skipped". Default: `20`.
- **skip_percent**: The percentage of the song that needs to be played before which the song will be considered "skipped". Expects a value between `0` and `1`, default: `0.0`.

### MPD DJ

The `mpd_dj` plugin randomly adds items to the MPD queue. Note that activating this plugin will turn off random mode in MPD, as it needs to know what songs are upcoming in the queue.

#### Usage

Once enabled, start the tracker by typing:

```bash
beet dj
```

By default, the plugin will work to maintain 20 upcoming songs, selected randomly from the library. These defaults can be changed using command-line options.

- `--number=ITEMS`, `-n ITEMS`: The plugin will maintain the specified number of items in the upcoming queue.
- `--album`, `-a`: The plugin will queue albums instead of songs.

In addition, you can enter a [query](https://beets.readthedocs.io/en/stable/reference/query.html) to specify what will be added to the queue.

```bash
# maintain 5 albums in the queue, pulling randomly from albums released in 2022
beet dj -n 5 --album year:2022
```
