"""Track database — stores aircraft position history in SQLite."""

import calendar
import logging
import sqlite3
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("gaveron.trackdb")


class TrackDB:
    """SQLite-based aircraft track storage."""

    def __init__(self, db_path: str = "gaveron_tracks.db", retention_hours: float = 72.0):
        self.db_path = db_path
        self.retention_seconds = retention_hours * 3600
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    icao TEXT NOT NULL,
                    flight TEXT,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    alt_baro INTEGER,
                    alt_geom INTEGER,
                    gs REAL,
                    track REAL,
                    vert_rate INTEGER,
                    squawk TEXT,
                    category TEXT,
                    rssi REAL
                );

                CREATE INDEX IF NOT EXISTS idx_positions_icao_ts
                    ON positions (icao, ts);
                CREATE INDEX IF NOT EXISTS idx_positions_ts
                    ON positions (ts);

                CREATE TABLE IF NOT EXISTS aircraft_info (
                    icao TEXT PRIMARY KEY,
                    flight TEXT,
                    category TEXT,
                    squawk TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    total_positions INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_aircraft_last_seen
                    ON aircraft_info (last_seen);
            """)
        logger.info("Track database initialized: %s (retention: %.0fh)",
                     self.db_path, self.retention_seconds / 3600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
        conn.row_factory = sqlite3.Row
        return conn

    def store_positions(self, aircraft_list: list[dict]):
        """Store current aircraft positions (batch insert).

        Uses per-aircraft timestamp based on seen_pos (seconds since last
        position from ADS-B source) for accurate timing.
        """
        now = time.time()
        rows = []
        info_updates = []

        for ac in aircraft_list:
            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                continue

            # Use ADS-B source timestamp: now - seen_pos
            seen_pos = ac.get("seen_pos", 0) or 0
            ts = now - seen_pos

            icao = ac["hex"]
            flight = ac.get("flight", "").strip() or None
            rows.append((
                ts, icao, flight, lat, lon,
                ac.get("alt_baro"), ac.get("alt_geom"),
                ac.get("gs"), ac.get("track"),
                ac.get("vert_rate"), ac.get("squawk"),
                ac.get("category"), ac.get("rssi"),
            ))
            info_updates.append((
                icao, flight, ac.get("category"),
                ac.get("squawk"), ts,
            ))

        if not rows:
            return

        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """INSERT INTO positions
                       (ts, icao, flight, lat, lon, alt_baro, alt_geom,
                        gs, track, vert_rate, squawk, category, rssi)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                for icao, flight, category, squawk, ts in info_updates:
                    conn.execute(
                        """INSERT INTO aircraft_info (icao, flight, category, squawk, first_seen, last_seen, total_positions)
                           VALUES (?, ?, ?, ?, ?, ?, 1)
                           ON CONFLICT(icao) DO UPDATE SET
                             flight = COALESCE(?, flight),
                             category = COALESCE(?, category),
                             squawk = COALESCE(?, squawk),
                             last_seen = ?,
                             total_positions = total_positions + 1""",
                        (icao, flight, category, squawk, ts, ts,
                         flight, category, squawk, ts),
                    )

    def get_track(self, icao: str, hours: float = 24.0) -> list[dict]:
        """Get position history for a specific aircraft."""
        since = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ts, lat, lon, alt_baro, alt_geom, gs, track,
                          vert_rate, flight, squawk
                   FROM positions
                   WHERE icao = ? AND ts >= ?
                   ORDER BY ts ASC""",
                (icao, since),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_tracks(self, hours: float = 1.0, min_points: int = 2) -> dict:
        """Get all aircraft tracks for the last N hours."""
        since = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT icao, ts, lat, lon, alt_baro, gs, track, flight
                   FROM positions
                   WHERE ts >= ?
                   ORDER BY icao, ts ASC""",
                (since,),
            ).fetchall()

        tracks = {}
        for r in rows:
            icao = r["icao"]
            if icao not in tracks:
                tracks[icao] = {"icao": icao, "flight": r["flight"], "positions": []}
            tracks[icao]["positions"].append({
                "ts": r["ts"], "lat": r["lat"], "lon": r["lon"],
                "alt_baro": r["alt_baro"], "gs": r["gs"], "track": r["track"],
            })
            if r["flight"]:
                tracks[icao]["flight"] = r["flight"]

        # Filter by min_points
        return {k: v for k, v in tracks.items() if len(v["positions"]) >= min_points}

    def get_heatmap(self, hours: float = 24.0, grid_size: float = 0.05) -> list[dict]:
        """Get position density heatmap data."""
        since = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT
                     ROUND(lat / ?, 0) * ? as grid_lat,
                     ROUND(lon / ?, 0) * ? as grid_lon,
                     COUNT(*) as count
                   FROM positions
                   WHERE ts >= ?
                   GROUP BY grid_lat, grid_lon
                   HAVING count >= 2
                   ORDER BY count DESC""",
                (grid_size, grid_size, grid_size, grid_size, since),
            ).fetchall()
        return [{"lat": r["grid_lat"], "lon": r["grid_lon"], "count": r["count"]} for r in rows]

    def get_recent_aircraft(self, hours: float = 24.0) -> list[dict]:
        """Get list of recently seen aircraft with summary info."""
        since = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ai.icao, ai.flight, ai.category, ai.squawk,
                          ai.first_seen, ai.last_seen, ai.total_positions,
                          (SELECT COUNT(*) FROM positions p
                           WHERE p.icao = ai.icao AND p.ts >= ?) as recent_positions
                   FROM aircraft_info ai
                   WHERE ai.last_seen >= ?
                   ORDER BY ai.last_seen DESC""",
                (since, since),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Get database statistics."""
        with self._connect() as conn:
            total_pos = conn.execute("SELECT COUNT(*) as c FROM positions").fetchone()["c"]
            total_ac = conn.execute("SELECT COUNT(*) as c FROM aircraft_info").fetchone()["c"]
            oldest = conn.execute("SELECT MIN(ts) as t FROM positions").fetchone()["t"]
            newest = conn.execute("SELECT MAX(ts) as t FROM positions").fetchone()["t"]
            db_size = Path(self.db_path).stat().st_size if Path(self.db_path).exists() else 0

        return {
            "total_positions": total_pos,
            "total_aircraft": total_ac,
            "oldest_record": oldest,
            "newest_record": newest,
            "db_size_mb": round(db_size / 1048576, 2),
            "retention_hours": round(self.retention_seconds / 3600),
        }

    def get_track_by_date(self, icao: str, date_str: str) -> list[dict]:
        """Get track for an aircraft on a specific date (YYYY-MM-DD, UTC)."""
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ts_start = dt.timestamp()
        ts_end = ts_start + 86400
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ts, lat, lon, alt_baro, alt_geom, gs, track,
                          vert_rate, flight, squawk
                   FROM positions
                   WHERE icao = ? AND ts >= ? AND ts < ?
                   ORDER BY ts ASC""",
                (icao, ts_start, ts_end),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_aircraft_by_date(self, date_str: str) -> list[dict]:
        """Get all aircraft seen on a specific date (YYYY-MM-DD, UTC)."""
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ts_start = dt.timestamp()
        ts_end = ts_start + 86400
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT icao,
                          MAX(flight) as flight,
                          MAX(category) as category,
                          MAX(squawk) as squawk,
                          MIN(ts) as first_seen,
                          MAX(ts) as last_seen,
                          COUNT(*) as positions
                   FROM positions
                   WHERE ts >= ? AND ts < ?
                   GROUP BY icao
                   ORDER BY last_seen DESC""",
                (ts_start, ts_end),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_track_by_range(self, icao: str, date_from: str, date_to: str) -> list[dict]:
        """Get track for an aircraft across a date range (YYYY-MM-DD, UTC)."""
        ts_start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        ts_end = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 86400
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ts, lat, lon, alt_baro, alt_geom, gs, track,
                          vert_rate, flight, squawk
                   FROM positions
                   WHERE icao = ? AND ts >= ? AND ts < ?
                   ORDER BY ts ASC""",
                (icao, ts_start, ts_end),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_aircraft_by_range(self, date_from: str, date_to: str) -> list[dict]:
        """Get all aircraft seen in a date range (YYYY-MM-DD, UTC)."""
        ts_start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        ts_end = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 86400
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT icao,
                          MAX(flight) as flight,
                          MAX(category) as category,
                          MAX(squawk) as squawk,
                          MIN(ts) as first_seen,
                          MAX(ts) as last_seen,
                          COUNT(*) as positions
                   FROM positions
                   WHERE ts >= ? AND ts < ?
                   GROUP BY icao
                   ORDER BY last_seen DESC""",
                (ts_start, ts_end),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_available_dates(self) -> list[str]:
        """Get list of dates that have track data (YYYY-MM-DD, UTC)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT DATE(ts, 'unixepoch') as d
                   FROM positions
                   ORDER BY d DESC""",
            ).fetchall()
        return [r["d"] for r in rows]

    def cleanup(self):
        """Remove data older than retention period."""
        cutoff = time.time() - self.retention_seconds
        with self._lock:
            with self._connect() as conn:
                deleted = conn.execute(
                    "DELETE FROM positions WHERE ts < ?", (cutoff,)
                ).rowcount
                if deleted > 0:
                    conn.execute("DELETE FROM aircraft_info WHERE last_seen < ?", (cutoff,))
                    logger.info("Cleanup: removed %d old positions", deleted)
