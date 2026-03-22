"""HTTP server for serving aircraft data.

Provides REST API compatible with tar1090 frontend format,
plus track history API.
"""

import json
import gzip
import logging
import time
from pathlib import Path

from aiohttp import web

from .decoder import AircraftStore
from .trackdb import TrackDB

logger = logging.getLogger("gaveron.server")


class GaveronServer:
    """HTTP server serving aircraft data, history, and track database."""

    def __init__(
        self,
        store: AircraftStore,
        trackdb: TrackDB,
        history_dir: str = "/run/gaveron",
        host: str = "0.0.0.0",
        port: int = 8080,
        receiver_lat: float = 0.0,
        receiver_lon: float = 0.0,
    ):
        self.store = store
        self.trackdb = trackdb
        self.history_dir = Path(history_dir)
        self.host = host
        self.port = port
        self.receiver_lat = receiver_lat
        self.receiver_lon = receiver_lon
        self.app = web.Application()
        self._setup_routes()
        self._start_time = time.time()

    def _setup_routes(self):
        # Core data API (tar1090-compatible)
        self.app.router.add_get("/data/aircraft.json", self.handle_aircraft)
        self.app.router.add_get("/data/receiver.json", self.handle_receiver)
        self.app.router.add_get("/data/stats.json", self.handle_stats)
        self.app.router.add_get(
            "/data/traces/{hex2}/{filename}", self.handle_trace_file
        )
        self.app.router.add_get("/chunks/chunks.json", self.handle_chunks_index)
        self.app.router.add_get("/chunks/{filename}", self.handle_chunk_file)
        self.app.router.add_get("/health", self.handle_health)

        # Track history API
        self.app.router.add_get("/api/tracks/{icao}", self.handle_track)
        self.app.router.add_get("/api/tracks", self.handle_all_tracks)
        self.app.router.add_get("/api/heatmap", self.handle_heatmap)
        self.app.router.add_get("/api/history", self.handle_history_list)
        self.app.router.add_get("/api/db-stats", self.handle_db_stats)

        # Web UI — tar1090 frontend
        tar1090_dir = Path(__file__).parent / "static" / "tar1090"
        if tar1090_dir.is_dir():
            self.app.router.add_get("/", self.handle_tar1090_index)
            self.app.router.add_static("/", tar1090_dir)
            self._tar1090_dir = tar1090_dir
        else:
            # Fallback to custom UI
            self.app.router.add_get("/", self.handle_index)
            self._tar1090_dir = None
        static_dir = Path(__file__).parent / "static"
        if static_dir.is_dir():
            self.app.router.add_static("/static/", static_dir)

        # CORS middleware
        self.app.middlewares.append(self._cors_middleware)

    @web.middleware
    async def _cors_middleware(self, request, handler):
        response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    async def handle_tar1090_index(self, request: web.Request) -> web.Response:
        """Serve tar1090 frontend."""
        index_path = self._tar1090_dir / "index.html"
        return web.FileResponse(index_path)

    async def handle_index(self, request: web.Request) -> web.Response:
        """Serve custom web UI."""
        index_path = Path(__file__).parent / "static" / "index.html"
        return web.FileResponse(index_path)

    async def handle_aircraft(self, request: web.Request) -> web.Response:
        """Serve current aircraft data in tar1090-compatible format."""
        data = self.store.to_json()
        return web.json_response(
            data,
            headers={"Cache-Control": "no-cache"},
        )

    async def handle_receiver(self, request: web.Request) -> web.Response:
        """Serve receiver metadata."""
        data = {
            "version": "gaveron 0.1.0",
            "refresh": 1000,
            "history": 120,
            "lat": self.receiver_lat,
            "lon": self.receiver_lon,
            "haveTraces": True,
        }
        return web.json_response(data)

    async def handle_stats(self, request: web.Request) -> web.Response:
        """Serve statistics."""
        now = time.time()
        data = {
            "latest": {
                "start": now - 60,
                "end": now,
                "messages": self.store.total_messages,
                "aircraft_with_pos": sum(
                    1 for ac in self.store.aircraft.values()
                    if ac.lat is not None
                ),
                "aircraft_total": len(self.store.aircraft),
            },
            "uptime": round(now - self._start_time),
        }
        return web.json_response(data)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "uptime": round(time.time() - self._start_time),
            "aircraft_count": len(self.store.aircraft),
            "messages": self.store.total_messages,
        })

    async def handle_chunks_index(self, request: web.Request) -> web.Response:
        """Serve chunks index."""
        path = self.history_dir / "chunks.json"
        if path.exists():
            data = json.loads(path.read_text())
            return web.json_response(data)
        return web.json_response({"chunks": []})

    async def handle_chunk_file(self, request: web.Request) -> web.Response:
        """Serve a compressed chunk file."""
        filename = request.match_info["filename"]
        if "/" in filename or ".." in filename:
            raise web.HTTPBadRequest(text="Invalid filename")

        path = self.history_dir / filename
        if not path.exists():
            raise web.HTTPNotFound()

        if filename.endswith(".gz"):
            data = path.read_bytes()
            return web.Response(
                body=data,
                content_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Cache-Control": "max-age=3600",
                },
            )
        else:
            return web.Response(
                text=path.read_text(),
                content_type="application/json",
            )

    async def handle_trace_file(self, request: web.Request) -> web.Response:
        """Serve trace data in tar1090-compatible format.

        tar1090 requests:
          data/traces/XX/trace_recent_HEXID.json
          data/traces/XX/trace_full_HEXID.json
        """
        filename = request.match_info["filename"]
        if ".." in filename or "/" in filename:
            raise web.HTTPBadRequest(text="Invalid filename")

        # Parse: trace_recent_a1b2c3.json or trace_full_a1b2c3.json
        if not filename.endswith(".json"):
            raise web.HTTPNotFound()
        name = filename[:-5]  # strip .json

        if name.startswith("trace_recent_"):
            icao = name[len("trace_recent_"):]
            hours = 4.0
        elif name.startswith("trace_full_"):
            icao = name[len("trace_full_"):]
            hours = 24.0
        else:
            raise web.HTTPNotFound()

        icao = icao.lower()
        positions = self.trackdb.get_track(icao, hours)

        if not positions:
            raise web.HTTPNotFound()

        # Build tar1090 trace format:
        # {"timestamp": base_ts, "trace": [[offset, lat, lon, alt, gs, track, flags, vr, extra], ...]}
        base_ts = positions[0]["ts"]
        trace = []
        for p in positions:
            extra = {}
            if p.get("flight"):
                extra["flight"] = p["flight"].strip()
            point = [
                round(p["ts"] - base_ts, 1),  # [0] offset from base
                round(p["lat"], 6),            # [1] lat
                round(p["lon"], 6),            # [2] lon
                p.get("alt_baro") or "ground", # [3] alt
                round(p["gs"], 1) if p.get("gs") else None,  # [4] gs
                round(p["track"], 1) if p.get("track") else None,  # [5] track
                0,                             # [6] flags
                p.get("vert_rate") or 0,       # [7] vertical rate
                extra if extra else None,      # [8] extra data
            ]
            trace.append(point)

        data = {
            "timestamp": round(base_ts, 1),
            "trace": trace,
        }
        return web.json_response(
            data,
            headers={"Cache-Control": "no-cache"},
        )

    # ---- Track history API ----

    async def handle_track(self, request: web.Request) -> web.Response:
        """Get track history for a specific aircraft."""
        icao = request.match_info["icao"].lower()
        hours = float(request.query.get("hours", "24"))
        hours = min(hours, 72)  # cap at retention
        track = self.trackdb.get_track(icao, hours)
        return web.json_response({
            "icao": icao,
            "count": len(track),
            "positions": track,
        })

    async def handle_all_tracks(self, request: web.Request) -> web.Response:
        """Get all recent tracks."""
        hours = float(request.query.get("hours", "1"))
        hours = min(hours, 24)
        min_pts = int(request.query.get("min_points", "2"))
        tracks = self.trackdb.get_all_tracks(hours, min_pts)
        return web.json_response({
            "count": len(tracks),
            "tracks": tracks,
        })

    async def handle_heatmap(self, request: web.Request) -> web.Response:
        """Get heatmap data."""
        hours = float(request.query.get("hours", "24"))
        hours = min(hours, 72)
        grid = float(request.query.get("grid", "0.05"))
        data = self.trackdb.get_heatmap(hours, grid)
        return web.json_response({
            "count": len(data),
            "grid_size": grid,
            "points": data,
        })

    async def handle_history_list(self, request: web.Request) -> web.Response:
        """Get list of recently seen aircraft."""
        hours = float(request.query.get("hours", "24"))
        hours = min(hours, 72)
        aircraft = self.trackdb.get_recent_aircraft(hours)
        return web.json_response({
            "count": len(aircraft),
            "aircraft": aircraft,
        })

    async def handle_db_stats(self, request: web.Request) -> web.Response:
        """Get database statistics."""
        stats = self.trackdb.get_stats()
        return web.json_response(stats)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info("HTTP server started on %s:%d", self.host, self.port)
        # Keep running
        while True:
            await __import__("asyncio").sleep(3600)
