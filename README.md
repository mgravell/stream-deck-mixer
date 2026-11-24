# Stream Deck audio mixer

A Stream Deck MK.2 as a software audio mixer: colored multi-key fader columns with
volume up/down/mute keys, live stereo VU meters with peak-hold, and a media-transport key.

| Platform | Implementation |
|---|---|
| [Linux](linux/README.md) | PipeWire virtual channels + a [StreamController](https://github.com/StreamController/StreamController) plugin (Python) |

![The Apps page: per-application faders](docs/deck-apps.png)

## License

[MIT](LICENSE).
