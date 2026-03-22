"""HTTP server for serving aircraft data.

Provides REST API compatible with tar1090 frontend format.
"""

import json
import gzip
import logging
import time
from pathlib import Path

from aiohttp import web

from .decoder import AircraftStore

logger = logging.getLogger("gaveron.server")


class GaveronServer:
    """HTTP server serving aircraft data and history."""

    def __init__(
        self,
        store: AircraftStore,
        history_dir: str = "/run/gaveron",
        host: str = "0.0.0.0",
        port: int = 8080,
        receiver_lat: float = 0.0,
        receiver_lon: float = 0.0,
    ):
        self.store = store
        self.history_dir = Path(history_dir)
        self.host = host
        self.port = port
        self.receiver_lat = receiver_lat
        self.receiver_lon = receiver_lon
        self.app = web.Application()
        self._setup_routes()
        self._start_time = time.time()

    def _setup_routes(self):
        self.app.router.add_get("/data/aircraft.json", self.handle_aircraft)
        self.app.router.add_get("/data/receiver.json", self.handle_receiver)
        self.app.router.add_get("/data/stats.json", self.handle_stats)
        self.app.router.add_get("/chunks/chunks.json", self.handle_chunks_index)
        self.app.router.add_get("/chunks/{filename}", self.handle_chunk_file)
        self.app.router.add_get("/health", self.handle_health)
        # CORS middleware
        self.app.middlewares.append(self._cors_middleware)

    @web.middleware
    async def _cors_middleware(self, request, handler):
        response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

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
        # Prevent path traversal
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

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info("HTTP server started on %s:%d", self.host, self.port)
        # Keep running
        while True:
            await __import__("asyncio").sleep(3600)
