#!/usr/bin/env python3

"""
A minimal system tray application to stream The Indie Beat Radio
"""

import asyncio
import signal
import traceback
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GLib', '2.0')
gi.require_version('Gst', '1.0')
gi.require_version('Notify', '0.7')
gi.require_version('AyatanaAppIndicator3', '0.1')

from gi.repository import Gtk, Gdk, GLib, Gst
from gi.repository import AyatanaAppIndicator3 as AppIndicator

import aiohttp

# Constants
APP_NAME = 'TIBR Simple Player'
USER_AGENT = f'{APP_NAME}/1.0'
API_BASE_URL = 'https://azura.theindiebeat.fm/api'
METADATA_UPDATE_INTERVAL = 30  # seconds


class NetworkRetryManager:
    async def retry_async_call(self, coro, error_message="Operation failed"):
        """Retry with exponential backoff"""
        max_attempts = 3
        base_delay = 5

        for attempt in range(1, max_attempts + 1):
            try:
                return await coro
            except Exception as e:
                if attempt == max_attempts:
                    print(f"{error_message}: {e}")
                    raise

                delay = base_delay * (2 ** (attempt - 1))
                print(
                    f"{error_message}. Retrying in {delay} seconds (Attempt {attempt}/{max_attempts})"
                )
                await asyncio.sleep(delay)

        # This should never be reached
        return None

class Channel:
    def __init__(self, **kwargs):
        self.name = kwargs.get('name', 'Unknown Channel')
        self.shortcode = kwargs.get('shortcode', '')
        self.listen_url = kwargs.get('listen_url', '')

class AsyncIOLoop:
    """Manage an async event loop in a separate thread"""
    def __init__(self):
        self._loop = None
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        """Start the async event loop in a separate thread"""
        def run_loop():
            try:
                # Create new event loop
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)

                # Run until stopped
                while not self._stop_event.is_set():
                    self._loop.run_until_complete(asyncio.sleep(0.1))
            except Exception as e:
                print(f"Async loop error: {e}")
            finally:
                self._loop.close()

        # Start the thread
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the async event loop"""
        if self._stop_event:
            self._stop_event.set()
        if self._thread:
            self._thread.join()

    def run_coroutine(self, coro):
        """Run a coroutine in the async thread"""
        if not self._loop:
            return None

        # Use run_coroutine_threadsafe to run in the separate thread
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=10)
        except Exception as e:
            print(f"Coroutine execution error: {e}")
            return None

class AzuraCastAPI:
    def __init__(self, network_retry_manager=None):
        self._session = None
        self.network_retry = network_retry_manager or NetworkRetryManager()

    async def get_session(self):
        """Create a new aiohttp session if not exists"""
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={'User-Agent': USER_AGENT}
            )
        return self._session

    async def get_channels(self):
        """Fetch available channels with retry mechanism"""

        async def fetch_channels():
            session = await self.get_session()
            async with session.get(f"{API_BASE_URL}/stations") as response:
                if response.status == 200:
                    data = await response.json()
                    return [Channel(**station) for station in data]
                raise RuntimeError(f"HTTP {response.status}")

        try:
            return await self.network_retry.retry_async_call(
                fetch_channels(), "Error fetching channels"
            )
        except Exception:
            return []

    async def get_now_playing(self, station_shortcode):
        """Fetch now playing information for a station with retry mechanism"""

        async def fetch_now_playing():
            session = await self.get_session()
            async with session.get(
                f"{API_BASE_URL}/nowplaying/{station_shortcode}"
            ) as response:
                if response.status == 200:
                    return await response.json()
                raise RuntimeError(f"HTTP {response.status}")

        try:
            return await self.network_retry.retry_async_call(
                fetch_now_playing(), f"Error fetching now playing for {station_shortcode}"
            )
        except Exception:
            return None

    async def close(self):
        """Close the aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()


class RadioPlayer:
    def __init__(self, api, async_loop):
        # Initialize GStreamer
        Gst.init(None)

        self.api = api
        self.async_loop = async_loop

        self.playbin = Gst.ElementFactory.make("playbin", "tibr")
        self.sink = Gst.ElementFactory.make("pulsesink", "sink")

        if not self.playbin or not self.sink:
            print("Could not create GStreamer elements")
            raise RuntimeError("GStreamer initialization failed")

        self.sink.set_property("client-name", APP_NAME)
        self.playbin.set_property("audio-sink", self.sink)

        initial_volume = 0.5
        self.playbin.set_property("volume", initial_volume)

        # Set up bus message handling
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        # State tracking
        self.current_channel = None
        self.current_metadata = None
        self.is_playing = False
        self.metadata_update_source = None

    def play_channel(self, channel):
        """Play a specific channel"""
        # Stop any current playback
        self.stop()

        # Set the channel
        self.current_channel = channel

        # Set the URI for playback
        self.playbin.set_property('uri', channel.listen_url)

        # Start playback
        self.playbin.set_state(Gst.State.PLAYING)
        self.is_playing = True

        print(f"Playing channel: {channel.name}")

        # Start metadata updates
        self._start_metadata_updates()

    def _start_metadata_updates(self):
        """Start periodic metadata updates"""
        # Stop any existing updates
        self._stop_metadata_updates()

        def update_metadata():
            """Periodic metadata update"""
            if self.current_channel and self.is_playing:
                try:
                    # Use async loop to fetch metadata
                    metadata = self.async_loop.run_coroutine(
                        self.api.get_now_playing(self.current_channel.shortcode)
                    )

                    if metadata and 'now_playing' in metadata:
                        # Update metadata in main thread
                        GLib.idle_add(self._handle_metadata, metadata)
                except Exception as e:
                    print(f"Metadata update error: {e}")

            return self.is_playing  # Continue updates while playing

        # Use GLib timeout for periodic updates
        self.metadata_update_source = GLib.timeout_add_seconds(
            METADATA_UPDATE_INTERVAL, update_metadata
        )

        # Initial update
        update_metadata()

    def _handle_metadata(self, metadata):
        """Handle metadata update in main thread"""
        self.current_metadata = metadata

        # Call UI update method if set
        if hasattr(self, 'on_metadata_update'):
            self.on_metadata_update(metadata)

        return False

    def stop(self):
        """Stop current playback"""
        # Stop metadata updates
        self._stop_metadata_updates()

        # Stop playback
        if self.is_playing:
            self.playbin.set_state(Gst.State.NULL)
            self.is_playing = False
            self.current_channel = None
            self.current_metadata = None
            print("Playback stopped")

    def _stop_metadata_updates(self):
        """Stop metadata update timer"""
        if self.metadata_update_source:
            GLib.source_remove(self.metadata_update_source)
            self.metadata_update_source = None

    def _on_bus_message(self, bus, message):
        """Handle GStreamer messages"""
        """TODO: handle additional message types"""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Playback error: {err}, Debug: {debug}")
            self.stop()
        elif t == Gst.MessageType.EOS:
            print("End of stream")
            self.stop()


class TrayIcon:
    def __init__(self, player, api, async_loop):
        self.player = player
        self.api = api
        self.async_loop = async_loop

        # Create indicator
        self.indicator = AppIndicator.Indicator.new(
            'tibr-simple',
            'audio-x-generic-symbolic',
            AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        # Create menu
        self.menu = Gtk.Menu()

        # Loading item
        self.loading_item = Gtk.MenuItem(label="Loading channels...")
        self.loading_item.set_sensitive(False)
        self.menu.append(self.loading_item)

        # Track info item (with submenu)
        self.track_info_menu = Gtk.Menu()
        self.track_info_item = Gtk.MenuItem(label="No track playing")
        self.track_info_item.set_submenu(self.track_info_menu)

        # Artist link item
        self.artist_link_item = Gtk.MenuItem(label="View on Bandwagon")
        self.artist_link_item.connect('activate', self.open_artist_link)
        self.artist_link_item.set_visible(False)
        self.track_info_menu.append(self.artist_link_item)

        # Stop item
        self.stop_item = Gtk.MenuItem(label="Stop Playback")
        self.stop_item.connect('activate', self.stop_playback)
        self.stop_item.set_visible(False)
        self.track_info_menu.append(self.stop_item)

        # Add track info to main menu
        self.menu.append(self.track_info_item)

        # Separator before external links
        external_links_separator = Gtk.SeparatorMenuItem()
        self.menu.append(external_links_separator)

        # The Indie Beat link
        tibr_link_item = Gtk.MenuItem(label="Go to The Indie Beat")
        tibr_link_item.connect('activate', self.open_tibr_link)
        self.menu.append(tibr_link_item)

        # Bandwagon link
        bwf_link_item = Gtk.MenuItem(label="Go to Bandwagon")
        bwf_link_item.connect('activate', self.open_bandwagon_link)
        self.menu.append(bwf_link_item)

        # Separator
        separator = Gtk.SeparatorMenuItem()
        self.menu.append(separator)

        # Quit item
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect('activate', self.quit)
        self.menu.append(quit_item)

        # Set up metadata update callback on player
        player.on_metadata_update = self.update_track_info

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        # Trigger channel loading
        GLib.idle_add(self.load_channels)

    def load_channels(self):
        """Load channels"""
        try:
            # Fetch channels using async loop
            channels = self.async_loop.run_coroutine(self.api.get_channels())

            # Update menu in GTK main thread
            if channels:
                GLib.idle_add(self.update_menu_with_channels, channels)
            else:
                GLib.idle_add(self.show_channel_error)
        except Exception as e:
            print(f"Channel loading error: {e}")
            GLib.idle_add(self.show_channel_error)

        return False  # Ensure this is only run once

    def update_menu_with_channels(self, channels):
        """Update menu with channel names"""
        # Remove loading item
        self.menu.remove(self.loading_item)

        # Find the index where we want to insert channels
        channel_insert_index = 0

        for channel in channels:
            item = Gtk.MenuItem(label=channel.name)
            item.connect("activate", self.on_channel_selected, channel)
            # Insert at the beginning of the menu
            self.menu.insert(item, channel_insert_index)
            channel_insert_index += 1

        self.menu.show_all()
        return False

    def on_channel_selected(self, menu_item, channel):
        """Handle channel selection"""
        print(f"Selected channel: {channel.name}")

        try:
            self.player.play_channel(channel)

            # Show stop item when playing
            self.stop_item.set_visible(True)
        except Exception as e:
            print(f"Error playing channel: {e}")

        return True

    def stop_playback(self, *args):
        """Stop current playback"""
        # Stop player
        self.player.stop()

        # Hide stop item
        self.stop_item.set_visible(False)

        # Reset track info
        self.track_info_item.set_label("No track playing")
        self.artist_link_item.set_visible(False)

    def update_track_info(self, metadata):
        """Update track information in the menu"""
        # Ensure this runs in the main GTK thread
        GLib.idle_add(self._update_track_info_in_gtk, metadata)

    def _update_track_info_in_gtk(self, metadata):
        """Update track info in the GTK main thread"""
        # Ensure metadata and now_playing exist
        if not metadata or 'now_playing' not in metadata:
            self.track_info_item.set_label("No track playing")
            self.artist_link_item.set_visible(False)
            return False

        # Extract track info
        track = metadata['now_playing']['song']
        track_text = f"{track.get('artist', 'Unknown Artist')} - {track.get('title', 'Unknown Track')}"

        # Update track info
        self.track_info_item.set_label(track_text)

        # Check for external link
        ext_links = track.get('custom_fields', {}).get('ext_links')
        if ext_links:
            self.current_artist_link = ext_links
            self.artist_link_item.set_visible(True)
        else:
            self.current_artist_link = None
            self.artist_link_item.set_visible(False)

        return False

    def open_artist_link(self, *args):
        """Open artist's Bandwagon link"""
        try:
            # Check if current_artist_link exists and is not None
            link = getattr(self, 'current_artist_link', None)
            if link:
                Gtk.show_uri_on_window(
                    None,
                    link,
                    Gdk.CURRENT_TIME
                )
            else:
                print("No external link available")
        except Exception as e:
            print(f"Error opening link: {e}")

    def open_tibr_link(self, *args):
        """Open The Indie Beat website"""
        try:
            Gtk.show_uri_on_window(None, "https://theindiebeat.fm/", Gdk.CURRENT_TIME)
        except Exception as e:
            print(f"Error opening TIBR link: {e}")

    def open_bandwagon_link(self, *args):
        """Open Bandwagon website"""
        try:
            Gtk.show_uri_on_window(None, "https://bandwagon.fm/", Gdk.CURRENT_TIME)
        except Exception as e:
            print(f"Error opening Bandwagon link: {e}")

    def show_channel_error(self):
        """Show channel load error in the menu"""
        self.loading_item.set_label("Error loading channels")
        return False

    def quit(self, *args):
        """Quit the application"""
        # Stop playback
        if self.player:
            self.player.stop()

        Gtk.main_quit()


def main():
    # Handle Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Initialize GStreamer
    Gst.init(None)

    # Create async IO loop
    async_loop = AsyncIOLoop()
    async_loop.start()

    # Initialize network retry manager
    network_retry_manager = NetworkRetryManager()

    # Initialize API
    api = AzuraCastAPI(network_retry_manager)

    # Initialize player
    player = RadioPlayer(api, async_loop)

    try:
        # Create tray icon
        tray = TrayIcon(player, api, async_loop)

        # Start GTK main loop
        Gtk.main()

    except Exception as e:
        print(f"TIBR error: {e}")
        traceback.print_exc()

    finally:
        # Cleanup
        try:
            # Stop playback
            player.stop()

            # Close API session
            async_loop.run_coroutine(api.close())
            async_loop.stop()

        except Exception as cleanup_err:
            print(f"TIBR error: {cleanup_err}")

if __name__ == "__main__":
    main()
