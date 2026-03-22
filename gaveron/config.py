"""Configuration management."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    """Gaveron server configuration."""

    # Feed source
    feed_type: str = "beast"  # beast, beast_listen, sbs, json_file
    feed_host: str = "127.0.0.1"
    feed_port: int = 30005  # 30005 for beast, 30003 for sbs
    json_file_path: str = "/run/readsb/aircraft.json"

    # HTTP server
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    # Receiver location (for distance calculations)
    receiver_lat: float = 0.0
    receiver_lon: float = 0.0

    # History
    history_dir: str = "/run/gaveron"
    history_interval: float = 8.0
    history_size: int = 450
    chunk_size: int = 20

    # Aircraft timeout (seconds)
    aircraft_timeout: float = 300.0

    # Track database retention (hours)
    track_retention_hours: float = 2400.0  # 100 days

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            feed_type=os.getenv("GAVERON_FEED_TYPE", "beast"),
            feed_host=os.getenv("GAVERON_FEED_HOST", "127.0.0.1"),
            feed_port=int(os.getenv("GAVERON_FEED_PORT", "30005")),
            json_file_path=os.getenv("GAVERON_JSON_PATH", "/run/readsb/aircraft.json"),
            http_host=os.getenv("GAVERON_HTTP_HOST", "0.0.0.0"),
            http_port=int(os.getenv("GAVERON_HTTP_PORT", "8080")),
            receiver_lat=float(os.getenv("GAVERON_LAT", "0.0")),
            receiver_lon=float(os.getenv("GAVERON_LON", "0.0")),
            history_dir=os.getenv("GAVERON_HISTORY_DIR", "/run/gaveron"),
            history_interval=float(os.getenv("GAVERON_HISTORY_INTERVAL", "8.0")),
            history_size=int(os.getenv("GAVERON_HISTORY_SIZE", "450")),
            chunk_size=int(os.getenv("GAVERON_CHUNK_SIZE", "20")),
            aircraft_timeout=float(os.getenv("GAVERON_AIRCRAFT_TIMEOUT", "300")),
            track_retention_hours=float(os.getenv("GAVERON_TRACK_RETENTION_HOURS", "2400")),
            log_level=os.getenv("GAVERON_LOG_LEVEL", "INFO"),
        )

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load configuration from a YAML/env file."""
        import yaml

        p = Path(path)
        if not p.exists():
            return cls.from_env()

        with open(p) as f:
            data = yaml.safe_load(f) or {}

        return cls(
            feed_type=data.get("feed_type", "beast"),
            feed_host=data.get("feed_host", "127.0.0.1"),
            feed_port=data.get("feed_port", 30005),
            json_file_path=data.get("json_file_path", "/run/readsb/aircraft.json"),
            http_host=data.get("http_host", "0.0.0.0"),
            http_port=data.get("http_port", 8080),
            receiver_lat=data.get("receiver_lat", 0.0),
            receiver_lon=data.get("receiver_lon", 0.0),
            history_dir=data.get("history_dir", "/run/gaveron"),
            history_interval=data.get("history_interval", 8.0),
            history_size=data.get("history_size", 450),
            chunk_size=data.get("chunk_size", 20),
            aircraft_timeout=data.get("aircraft_timeout", 300.0),
            track_retention_hours=data.get("track_retention_hours", 2400.0),
            log_level=data.get("log_level", "INFO"),
        )
