"""Aircraft movement simulator for demo/testing.

Generates realistic moving aircraft with tracks.
"""

import json
import math
import random
import time
from pathlib import Path


# Simulated aircraft definitions
AIRCRAFT = [
    {"hex": "a1b2c3", "flight": "AFL1234 ", "alt": 35000, "gs": 480, "track": 270, "lat": 56.2, "lon": 40.5, "squawk": "2134", "category": "A3"},
    {"hex": "a2b3c4", "flight": "SU2045  ", "alt": 28000, "gs": 420, "track": 200, "lat": 56.8, "lon": 38.0, "squawk": "4512", "category": "A3"},
    {"hex": "a3c4d5", "flight": "UTN432  ", "alt": 12500, "gs": 310, "track": 45, "lat": 54.5, "lon": 36.0, "squawk": "1234", "category": "A2"},
    {"hex": "d4e5f6", "flight": "TKS789  ", "alt": 41000, "gs": 520, "track": 135, "lat": 57.5, "lon": 35.0, "squawk": "6712", "category": "A5"},
    {"hex": "b5c6d7", "flight": "SVR2201 ", "alt": 5500, "gs": 220, "track": 90, "lat": 55.4, "lon": 36.5, "squawk": "3456", "category": "A1"},
    {"hex": "c6d7e8", "flight": "AFL505  ", "alt": 22000, "gs": 390, "track": 315, "lat": 55.0, "lon": 39.5, "squawk": "5543", "category": "A3"},
    {"hex": "e7f8a9", "flight": "POT4412 ", "alt": 8000, "gs": 250, "track": 180, "lat": 56.0, "lon": 37.0, "squawk": "0421", "category": "A2"},
    {"hex": "112233", "flight": "AZV567  ", "alt": 38000, "gs": 510, "track": 60, "lat": 55.2, "lon": 36.5, "squawk": "2345", "category": "A3"},
    {"hex": "445566", "flight": "NWS3301 ", "alt": 15000, "gs": 340, "track": 350, "lat": 55.0, "lon": 37.0, "squawk": "1200", "category": "A3"},
    {"hex": "aabbcc", "flight": "DAL42   ", "alt": 33000, "gs": 470, "track": 250, "lat": 56.5, "lon": 39.0, "squawk": "3421", "category": "A5"},
    # Climbing aircraft
    {"hex": "cc1122", "flight": "AFL777  ", "alt": 3000, "gs": 250, "track": 280, "lat": 55.95, "lon": 37.5, "squawk": "6001", "category": "A3",
     "climb": True, "alt_target": 32000, "vr": 2500},
    # Descending aircraft
    {"hex": "dd3344", "flight": "SU1500  ", "alt": 25000, "gs": 350, "track": 120, "lat": 56.3, "lon": 36.0, "squawk": "5200", "category": "A3",
     "climb": False, "alt_target": 2000, "vr": -1800},
]


class AircraftSim:
    def __init__(self, defn):
        self.hex = defn["hex"]
        self.flight = defn["flight"]
        self.alt = float(defn["alt"])
        self.gs = float(defn["gs"])
        self.track = float(defn["track"])
        self.lat = float(defn["lat"])
        self.lon = float(defn["lon"])
        self.squawk = defn["squawk"]
        self.category = defn["category"]
        self.messages = random.randint(100, 5000)
        self.rssi = random.uniform(-30, -5)

        # Vertical rate
        self.vr = defn.get("vr", 0)
        self.alt_target = defn.get("alt_target", self.alt)
        self.climbing = defn.get("climb", False)

        # Slight random variations
        self.track_drift = random.uniform(-0.3, 0.3)  # degrees per update
        self.gs_drift = random.uniform(-0.5, 0.5)

    def update(self, dt: float):
        """Move aircraft by dt seconds."""
        # Speed in degrees per second (rough approximation)
        speed_lat = (self.gs / 3600) * (1.0 / 60) * dt  # nm to degrees lat
        speed_lon = speed_lat / max(math.cos(math.radians(self.lat)), 0.01)

        # Move
        self.lat += speed_lat * math.cos(math.radians(self.track))
        self.lon += speed_lon * math.sin(math.radians(self.track))

        # Track drift (gentle turns)
        self.track += self.track_drift + random.uniform(-0.1, 0.1)
        self.track = self.track % 360

        # Speed variation
        self.gs += self.gs_drift + random.uniform(-0.2, 0.2)
        self.gs = max(100, min(600, self.gs))

        # Altitude changes
        if self.vr != 0:
            self.alt += self.vr * (dt / 60)  # vr is ft/min
            if self.vr > 0 and self.alt >= self.alt_target:
                self.alt = self.alt_target
                self.vr = 0
            elif self.vr < 0 and self.alt <= self.alt_target:
                self.alt = self.alt_target
                self.vr = 0

        # Wrap around if too far
        if self.lat > 60:
            self.lat = 53
        elif self.lat < 53:
            self.lat = 60
        if self.lon > 43:
            self.lon = 34
        elif self.lon < 34:
            self.lon = 43

        self.messages += random.randint(1, 5)
        self.rssi += random.uniform(-0.5, 0.5)
        self.rssi = max(-35, min(-3, self.rssi))

    def to_dict(self) -> dict:
        return {
            "hex": self.hex,
            "type": "adsb_icao",
            "flight": self.flight,
            "alt_baro": int(self.alt),
            "gs": round(self.gs, 1),
            "track": round(self.track, 1),
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "vert_rate": int(self.vr),
            "squawk": self.squawk,
            "category": self.category,
            "messages": self.messages,
            "seen": round(random.uniform(0.1, 1.5), 1),
            "seen_pos": round(random.uniform(0.2, 2.0), 1),
            "rssi": round(self.rssi, 1),
        }


def run_simulator(output_path: str, interval: float = 1.0):
    """Run simulator, writing aircraft.json every interval seconds."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    aircraft = [AircraftSim(d) for d in AIRCRAFT]
    total_messages = random.randint(10000, 50000)

    print(f"Simulator started: {len(aircraft)} aircraft, writing to {path}")
    print(f"Update interval: {interval}s")

    try:
        while True:
            # Update all aircraft
            for ac in aircraft:
                ac.update(interval)

            total_messages += sum(random.randint(1, 5) for _ in aircraft)

            # Write JSON
            data = {
                "now": round(time.time(), 1),
                "messages": total_messages,
                "aircraft": [ac.to_dict() for ac in aircraft],
            }
            path.write_text(json.dumps(data, separators=(",", ":")))
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nSimulator stopped.")


if __name__ == "__main__":
    import sys
    output = sys.argv[1] if len(sys.argv) > 1 else "/tmp/gaveron-sim/aircraft.json"
    run_simulator(output, interval=1.0)
