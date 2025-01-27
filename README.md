# The Indie Beat - Simple Desktop Client

A small system tray application for streaming [The Indie Beat Radio](https://theindiebeat.fm).

I wrote this because I wanted something that did not depend on a full desktop environment, and that could easily be controlled from the system tray in a mimalistic desktop such as Sway or i3 (which I use on my [MNT Pocket Reform](https://shop.mntre.com/products/mnt-pocket-reform)).

For a slightly more feature-rich experience, check out my [GNOME Shell extension for The Indie Beat](https://extensions.gnome.org/extension/7822/the-indie-beat-fediverse-radio/).

## Features

- Stream The Indie Beat Radio channels
- Minimal system tray interface
- Simple playback control

## Dependencies

### System Packages

#### Debian/Ubuntu

(specifically, Debian unstable on MNT Pocket Reform; I assume the packages may be the same on other Debian derivatives)

```bash
sudo apt install \
    python3-gi \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav
```

#### Fedora

```bash
sudo dnf install \
    python3-gobject \
    gtk3 \
    libayatana-appindicator-gtk3 \
    gstreamer1-plugins-good \
    gstreamer1-plugins-bad-free \
    gstreamer1-libav
```

### Python Dependencies

```bash
pip install -r requirements.txt
```

## Installation

1. Clone the repository
2. Install system and Python dependencies
3. Run the application:

```bash
python3 ./tibr-player.py
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT

## Credits

Powered by [The Indie Beat Radio](https://theindiebeat.fm) and [Bandwagon](https://bandwagon.fm/). Built with Python, GTK, and GStreamer.
