"""Network feed handlers for Beast and SBS protocols."""

import asyncio
import logging
from typing import Optional

from .decoder import (
    AircraftStore,
    beast_extract_frames,
    decode_mode_s_long,
    decode_mode_s_short,
    decode_sbs_message,
)

logger = logging.getLogger("gaveron.feed")


class SBSFeed:
    """Connects to SBS/BaseStation feed (port 30003).

    SBS format is line-based CSV text.
    """

    def __init__(self, store: AircraftStore, host: str = "127.0.0.1", port: int = 30003):
        self.store = store
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False

    async def connect(self):
        while self._running:
            try:
                logger.info("Connecting to SBS feed at %s:%d", self.host, self.port)
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port
                )
                logger.info("Connected to SBS feed")
                await self._read_loop()
            except (ConnectionRefusedError, OSError) as e:
                logger.warning("SBS connection failed: %s. Retrying in 5s...", e)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SBS feed error: %s. Retrying in 5s...", e)
                await asyncio.sleep(5)

    async def _read_loop(self):
        assert self._reader is not None
        while self._running:
            line = await self._reader.readline()
            if not line:
                logger.warning("SBS feed disconnected")
                return
            try:
                text = line.decode("ascii", errors="ignore")
                decode_sbs_message(text, self.store)
            except Exception as e:
                logger.debug("SBS decode error: %s", e)

    async def start(self):
        self._running = True
        await self.connect()

    def stop(self):
        self._running = False
        if self._writer:
            self._writer.close()


class BeastFeed:
    """Connects to Beast binary feed (port 30005).

    Beast is a binary protocol used by dump1090/readsb.
    """

    def __init__(self, store: AircraftStore, host: str = "127.0.0.1", port: int = 30005):
        self.store = store
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False

    async def connect(self):
        while self._running:
            try:
                logger.info("Connecting to Beast feed at %s:%d", self.host, self.port)
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port
                )
                logger.info("Connected to Beast feed")
                await self._read_loop()
            except (ConnectionRefusedError, OSError) as e:
                logger.warning("Beast connection failed: %s. Retrying in 5s...", e)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Beast feed error: %s. Retrying in 5s...", e)
                await asyncio.sleep(5)

    async def _read_loop(self):
        assert self._reader is not None
        buffer = bytearray()
        while self._running:
            data = await self._reader.read(4096)
            if not data:
                logger.warning("Beast feed disconnected")
                return
            buffer.extend(data)
            frames = beast_extract_frames(buffer)
            for msg_type, payload in frames:
                try:
                    if msg_type == ord('2'):
                        decode_mode_s_short(payload, self.store)
                    elif msg_type == ord('3'):
                        decode_mode_s_long(payload, self.store)
                except Exception as e:
                    logger.debug("Beast decode error: %s", e)

    async def start(self):
        self._running = True
        await self.connect()

    def stop(self):
        self._running = False
        if self._writer:
            self._writer.close()


class BeastListener:
    """Listens for incoming Beast binary connections.

    Used when the receiver connects outbound to the server
    (e.g., receiver behind NAT pushing data over the internet).
    readsb --net-connector <server_ip>,<port>,beast_out
    """

    def __init__(self, store: AircraftStore, host: str = "0.0.0.0", port: int = 30005):
        self.store = store
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._running = False
        self._clients: set[asyncio.Task] = set()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        logger.info("Beast client connected: %s", peer)
        buffer = bytearray()
        try:
            while self._running:
                data = await reader.read(4096)
                if not data:
                    break
                buffer.extend(data)
                frames = beast_extract_frames(buffer)
                for msg_type, payload in frames:
                    try:
                        if msg_type == ord('2'):
                            decode_mode_s_short(payload, self.store)
                        elif msg_type == ord('3'):
                            decode_mode_s_long(payload, self.store)
                    except Exception as e:
                        logger.debug("Beast decode error: %s", e)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.warning("Beast client error: %s", e)
        finally:
            logger.info("Beast client disconnected: %s", peer)
            writer.close()

    async def start(self):
        self._running = True
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = self._server.sockets[0].getsockname()
        logger.info("Beast listener started on %s:%d — waiting for receivers", addr[0], addr[1])
        async with self._server:
            await self._server.serve_forever()

    def stop(self):
        self._running = False
        if self._server:
            self._server.close()


class JSONFileFeed:
    """Reads aircraft.json from a file (e.g., from readsb/dump1090).

    This is the simplest integration: just poll the JSON file that
    readsb already writes.
    """

    def __init__(self, store: AircraftStore, path: str = "/run/readsb/aircraft.json",
                 interval: float = 1.0):
        self.store = store
        self.path = path
        self.interval = interval
        self._running = False

    async def start(self):
        import json
        from pathlib import Path

        self._running = True
        logger.info("Polling aircraft.json from %s every %.1fs", self.path, self.interval)

        while self._running:
            try:
                p = Path(self.path)
                if p.exists():
                    data = json.loads(p.read_text())
                    self._update_from_json(data)
            except Exception as e:
                logger.debug("JSON file read error: %s", e)
            await asyncio.sleep(self.interval)

    def _update_from_json(self, data: dict):
        """Update store from aircraft.json format."""
        import time

        now = time.time()
        for entry in data.get("aircraft", []):
            icao = entry.get("hex", "").strip().lower()
            if not icao:
                continue
            ac = self.store.get_or_create(icao)
            ac._last_message_time = now - entry.get("seen", 0)

            if "flight" in entry:
                ac.flight = entry["flight"]
            if "alt_baro" in entry:
                ac.alt_baro = entry["alt_baro"] if entry["alt_baro"] != "ground" else 0
            if "alt_geom" in entry:
                ac.alt_geom = entry["alt_geom"]
            if "gs" in entry:
                ac.gs = entry["gs"]
            if "track" in entry:
                ac.track = entry["track"]
            if "lat" in entry:
                ac.lat = entry["lat"]
            if "lon" in entry:
                ac.lon = entry["lon"]
            if "vert_rate" in entry:
                ac.vert_rate = entry["vert_rate"]
            if "squawk" in entry:
                ac.squawk = entry["squawk"]
            if "category" in entry:
                ac.category = entry["category"]
            if "type" in entry:
                ac.type = entry["type"]
            if "messages" in entry:
                ac.messages = entry["messages"]
            if "rssi" in entry:
                ac.rssi = entry["rssi"]
            if "seen_pos" in entry:
                ac._last_position_time = now - entry["seen_pos"]

        if "messages" in data:
            self.store.total_messages = data["messages"]

    def stop(self):
        self._running = False
