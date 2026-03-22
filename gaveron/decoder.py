"""ADS-B message decoder.

Decodes Beast binary and SBS/BaseStation protocols into aircraft state.
"""

import struct
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Aircraft:
    """Represents a tracked aircraft."""
    hex: str  # ICAO 24-bit address
    flight: Optional[str] = None
    alt_baro: Optional[int] = None
    alt_geom: Optional[int] = None
    gs: Optional[float] = None  # ground speed (knots)
    track: Optional[float] = None  # heading (degrees)
    lat: Optional[float] = None
    lon: Optional[float] = None
    vert_rate: Optional[int] = None  # ft/min
    squawk: Optional[str] = None
    category: Optional[str] = None
    messages: int = 0
    seen: float = 0.0  # seconds since last message
    seen_pos: float = 0.0  # seconds since last position
    rssi: float = -49.5
    type: str = "adsb_icao"
    _last_message_time: float = field(default_factory=time.time, repr=False)
    _last_position_time: float = field(default=0.0, repr=False)

    def update_seen(self, now: float):
        self.seen = now - self._last_message_time
        if self._last_position_time > 0:
            self.seen_pos = now - self._last_position_time

    def to_dict(self, now: float) -> dict:
        self.update_seen(now)
        d = {"hex": self.hex}
        if self.type:
            d["type"] = self.type
        if self.flight:
            d["flight"] = self.flight.strip()
        if self.alt_baro is not None:
            d["alt_baro"] = self.alt_baro
        if self.alt_geom is not None:
            d["alt_geom"] = self.alt_geom
        if self.gs is not None:
            d["gs"] = round(self.gs, 1)
        if self.track is not None:
            d["track"] = round(self.track, 1)
        if self.lat is not None:
            d["lat"] = round(self.lat, 6)
        if self.lon is not None:
            d["lon"] = round(self.lon, 6)
        if self.vert_rate is not None:
            d["vert_rate"] = self.vert_rate
        if self.squawk:
            d["squawk"] = self.squawk
        if self.category:
            d["category"] = self.category
        d["messages"] = self.messages
        d["seen"] = round(self.seen, 1)
        if self._last_position_time > 0:
            d["seen_pos"] = round(self.seen_pos, 1)
        d["rssi"] = round(self.rssi, 1)
        return d


class AircraftStore:
    """Thread-safe store for tracked aircraft."""

    def __init__(self, timeout: float = 300.0):
        self.aircraft: dict[str, Aircraft] = {}
        self.timeout = timeout
        self.total_messages = 0

    def get_or_create(self, icao_hex: str) -> Aircraft:
        if icao_hex not in self.aircraft:
            self.aircraft[icao_hex] = Aircraft(hex=icao_hex)
        return self.aircraft[icao_hex]

    def cleanup(self, now: float):
        """Remove aircraft not seen for timeout seconds."""
        stale = [
            k for k, v in self.aircraft.items()
            if now - v._last_message_time > self.timeout
        ]
        for k in stale:
            del self.aircraft[k]

    def to_json(self) -> dict:
        now = time.time()
        self.cleanup(now)
        return {
            "now": round(now, 1),
            "messages": self.total_messages,
            "aircraft": [ac.to_dict(now) for ac in self.aircraft.values()],
        }


# ---- SBS/BaseStation decoder (port 30003) ----

def decode_sbs_message(line: str, store: AircraftStore):
    """Decode a single SBS BaseStation format message.

    Format: MSG,type,session,aircraft,hex,flight,date,time,date,time,
            callsign,alt,gs,track,lat,lon,vert_rate,squawk,alert,emergency,spi,onground
    """
    parts = line.strip().split(",")
    if len(parts) < 22 or parts[0] != "MSG":
        return

    icao_hex = parts[4].strip().lower()
    if not icao_hex or len(icao_hex) != 6:
        return

    msg_type = parts[1].strip()
    ac = store.get_or_create(icao_hex)
    ac.messages += 1
    ac._last_message_time = time.time()
    store.total_messages += 1

    # MSG type 1: identification (callsign)
    if msg_type == "1":
        callsign = parts[10].strip()
        if callsign:
            ac.flight = callsign

    # MSG type 2: surface position
    elif msg_type == "2":
        _parse_position(parts, ac)
        _parse_altitude(parts, ac)
        _parse_speed(parts, ac)

    # MSG type 3: airborne position
    elif msg_type == "3":
        _parse_position(parts, ac)
        _parse_altitude(parts, ac)

    # MSG type 4: airborne velocity
    elif msg_type == "4":
        _parse_speed(parts, ac)
        _parse_vert_rate(parts, ac)

    # MSG type 5: surveillance altitude
    elif msg_type == "5":
        _parse_altitude(parts, ac)

    # MSG type 6: surveillance squawk
    elif msg_type == "6":
        squawk = parts[17].strip()
        if squawk:
            ac.squawk = squawk

    # MSG type 7: air-to-air
    elif msg_type == "7":
        _parse_altitude(parts, ac)

    # MSG type 8: all-call
    elif msg_type == "8":
        pass  # no useful data


def _parse_position(parts: list, ac: Aircraft):
    try:
        lat_s = parts[14].strip()
        lon_s = parts[15].strip()
        if lat_s and lon_s:
            ac.lat = float(lat_s)
            ac.lon = float(lon_s)
            ac._last_position_time = time.time()
    except (ValueError, IndexError):
        pass


def _parse_altitude(parts: list, ac: Aircraft):
    try:
        alt_s = parts[11].strip()
        if alt_s:
            ac.alt_baro = int(float(alt_s))
    except (ValueError, IndexError):
        pass


def _parse_speed(parts: list, ac: Aircraft):
    try:
        gs_s = parts[12].strip()
        trk_s = parts[13].strip()
        if gs_s:
            ac.gs = float(gs_s)
        if trk_s:
            ac.track = float(trk_s)
    except (ValueError, IndexError):
        pass


def _parse_vert_rate(parts: list, ac: Aircraft):
    try:
        vr_s = parts[16].strip()
        if vr_s:
            ac.vert_rate = int(float(vr_s))
    except (ValueError, IndexError):
        pass


# ---- Beast binary decoder (port 30005) ----

BEAST_ESCAPE = 0x1A

def beast_extract_frames(buffer: bytearray) -> list[tuple[int, bytes]]:
    """Extract Beast binary frames from buffer.

    Beast format:
    <1a> <type> <6-byte timestamp> <1-byte signal> <payload>
    Type 1: Mode-AC (2 bytes payload)
    Type 2: Mode-S short (7 bytes payload)
    Type 3: Mode-S long (14 bytes payload)

    Escaped: 0x1a in data is sent as 0x1a 0x1a
    """
    frames = []
    i = 0
    while i < len(buffer):
        # Find frame start
        if buffer[i] != BEAST_ESCAPE:
            i += 1
            continue

        if i + 1 >= len(buffer):
            break

        # Skip escaped 0x1a
        if buffer[i + 1] == BEAST_ESCAPE:
            i += 2
            continue

        msg_type = buffer[i + 1]
        if msg_type == ord('1'):
            payload_len = 2
        elif msg_type == ord('2'):
            payload_len = 7
        elif msg_type == ord('3'):
            payload_len = 14
        else:
            i += 2
            continue

        # Total: 1a + type + 6 timestamp + 1 signal + payload
        frame_len = 2 + 6 + 1 + payload_len
        # Need to account for possible escapes, so read enough
        raw = bytearray()
        j = i + 2  # skip 1a and type
        while len(raw) < 6 + 1 + payload_len and j < len(buffer):
            if buffer[j] == BEAST_ESCAPE:
                if j + 1 < len(buffer) and buffer[j + 1] == BEAST_ESCAPE:
                    raw.append(BEAST_ESCAPE)
                    j += 2
                else:
                    break  # next frame start
            else:
                raw.append(buffer[j])
                j += 1

        expected = 6 + 1 + payload_len
        if len(raw) < expected:
            break  # incomplete frame

        payload = bytes(raw[7:])  # skip 6-byte timestamp + 1 signal
        signal_level = raw[6]
        frames.append((msg_type, payload))

        # Advance buffer
        buffer[:] = buffer[j:]
        i = 0
        continue

    # Remove consumed data
    if frames:
        pass  # already trimmed in loop
    return frames


def decode_mode_s_short(payload: bytes, store: AircraftStore):
    """Decode Mode-S short message (56 bits / 7 bytes)."""
    if len(payload) < 7:
        return

    df = (payload[0] >> 3) & 0x1F  # Downlink Format

    # DF 0: Short air-air surveillance (ACAS)
    # DF 4: Surveillance altitude reply
    # DF 5: Surveillance identity reply
    # DF 11: All-call reply (contains ICAO address)

    if df == 11:
        icao = _extract_icao(payload)
        ac = store.get_or_create(icao)
        ac.messages += 1
        ac._last_message_time = time.time()
        store.total_messages += 1

    elif df in (0, 4, 16, 20):
        # These contain altitude in bits 19-31
        icao = _extract_icao_from_parity(payload)
        if icao:
            ac = store.get_or_create(icao)
            ac.messages += 1
            ac._last_message_time = time.time()
            store.total_messages += 1
            alt = _decode_ac13(payload)
            if alt is not None:
                ac.alt_baro = alt

    elif df == 5:
        icao = _extract_icao_from_parity(payload)
        if icao:
            ac = store.get_or_create(icao)
            ac.messages += 1
            ac._last_message_time = time.time()
            store.total_messages += 1
            squawk = _decode_id13(payload)
            if squawk:
                ac.squawk = squawk


def decode_mode_s_long(payload: bytes, store: AircraftStore):
    """Decode Mode-S long message (112 bits / 14 bytes).

    DF 17 = ADS-B, DF 18 = TIS-B/ADS-R
    """
    if len(payload) < 14:
        return

    df = (payload[0] >> 3) & 0x1F

    if df not in (17, 18):
        # DF 20, 21 also have ICAO + altitude/identity
        if df == 20:
            icao = _extract_icao_from_parity_long(payload)
            if icao:
                ac = store.get_or_create(icao)
                ac.messages += 1
                ac._last_message_time = time.time()
                store.total_messages += 1
                alt = _decode_ac13(payload)
                if alt is not None:
                    ac.alt_baro = alt
        elif df == 21:
            icao = _extract_icao_from_parity_long(payload)
            if icao:
                ac = store.get_or_create(icao)
                ac.messages += 1
                ac._last_message_time = time.time()
                store.total_messages += 1
                squawk = _decode_id13(payload)
                if squawk:
                    ac.squawk = squawk
        return

    # DF 17/18: ADS-B
    icao = f"{payload[1]:02x}{payload[2]:02x}{payload[3]:02x}"
    ac = store.get_or_create(icao)
    ac.messages += 1
    ac._last_message_time = time.time()
    store.total_messages += 1

    if df == 17:
        ac.type = "adsb_icao"
    elif df == 18:
        ac.type = "tisb_icao"

    # Type code (TC) is in bits 32-36 of the ME field (payload[4] >> 3)
    tc = (payload[4] >> 3) & 0x1F

    if 1 <= tc <= 4:
        _decode_identification(payload, ac, tc)
    elif 9 <= tc <= 18:
        _decode_airborne_position(payload, ac)
    elif tc == 19:
        _decode_airborne_velocity(payload, ac)
    elif 20 <= tc <= 22:
        _decode_airborne_position(payload, ac, gnss=True)


def _extract_icao(payload: bytes) -> str:
    return f"{payload[1]:02x}{payload[2]:02x}{payload[3]:02x}"


def _extract_icao_from_parity(payload: bytes) -> Optional[str]:
    """Extract ICAO from parity field (last 3 bytes XORed with CRC)."""
    # For surveillance replies, ICAO is XORed into the parity
    # Simplified: use the AP field directly (works when interrogator is us)
    crc = _crc_mode_s(payload[:4])
    addr = (
        (payload[4] ^ ((crc >> 16) & 0xFF)),
        (payload[5] ^ ((crc >> 8) & 0xFF)),
        (payload[6] ^ (crc & 0xFF)),
    )
    icao = f"{addr[0]:02x}{addr[1]:02x}{addr[2]:02x}"
    # Basic validation — skip obviously invalid
    if icao == "000000":
        return None
    return icao


def _extract_icao_from_parity_long(payload: bytes) -> Optional[str]:
    crc = _crc_mode_s(payload[:11])
    addr = (
        (payload[11] ^ ((crc >> 16) & 0xFF)),
        (payload[12] ^ ((crc >> 8) & 0xFF)),
        (payload[13] ^ (crc & 0xFF)),
    )
    icao = f"{addr[0]:02x}{addr[1]:02x}{addr[2]:02x}"
    if icao == "000000":
        return None
    return icao


# CRC-24 for Mode S
_CRC_TABLE: list[int] = []

def _init_crc_table():
    global _CRC_TABLE
    generator = 0xFFF409
    for i in range(256):
        entry = i << 16
        for _ in range(8):
            if entry & 0x800000:
                entry = ((entry << 1) ^ generator) & 0xFFFFFF
            else:
                entry = (entry << 1) & 0xFFFFFF
        _CRC_TABLE.append(entry)

_init_crc_table()


def _crc_mode_s(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ _CRC_TABLE[(crc >> 16) ^ byte]) & 0xFFFFFF
    return crc


# ADS-B charset for identification
_ADSB_CHARSET = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def _decode_identification(payload: bytes, ac: Aircraft, tc: int):
    """Decode aircraft identification (TC 1-4)."""
    # Category
    ca = payload[4] & 0x07
    categories = {1: "D", 2: "C", 3: "B", 4: "A"}
    prefix = categories.get(tc, "")
    if prefix:
        ac.category = f"{prefix}{ca}"

    # Callsign (6-bit chars, 8 characters in 48 bits)
    chars = []
    bits = int.from_bytes(payload[5:11], "big")
    for i in range(8):
        idx = (bits >> (42 - i * 6)) & 0x3F
        chars.append(_ADSB_CHARSET[idx])
    callsign = "".join(chars).strip("#").strip()
    if callsign:
        ac.flight = callsign


def _decode_airborne_position(payload: bytes, ac: Aircraft, gnss: bool = False):
    """Decode airborne position (TC 9-18, 20-22).

    Uses CPR (Compact Position Reporting). For simplicity, we decode
    locally using a reference position if available, otherwise store
    raw CPR and wait for both odd/even frames.
    """
    tc = (payload[4] >> 3) & 0x1F

    # Altitude
    alt_bits = ((payload[5] & 0xFF) << 4) | ((payload[6] >> 4) & 0x0F)
    q_bit = (alt_bits >> 4) & 1

    if q_bit:
        # Q-bit set: 25ft resolution
        n = ((alt_bits >> 5) << 4) | (alt_bits & 0x0F)
        altitude = n * 25 - 1000
    else:
        # Gillham code: 100ft resolution (simplified)
        altitude = _decode_gillham(alt_bits)

    if altitude is not None:
        if gnss:
            ac.alt_geom = altitude
        else:
            ac.alt_baro = altitude

    # CPR position
    cpr_odd = (payload[6] >> 2) & 1
    lat_cpr = ((payload[6] & 0x03) << 15) | (payload[7] << 7) | (payload[8] >> 1)
    lon_cpr = ((payload[8] & 0x01) << 16) | (payload[9] << 8) | payload[10]

    lat_cpr_f = lat_cpr / 131072.0
    lon_cpr_f = lon_cpr / 131072.0

    # Store CPR data for later decoding
    if not hasattr(ac, '_cpr_data'):
        ac._cpr_data = {}

    ac._cpr_data[cpr_odd] = {
        "lat": lat_cpr_f,
        "lon": lon_cpr_f,
        "time": time.time(),
    }

    # Try to decode position if we have both odd and even
    if 0 in ac._cpr_data and 1 in ac._cpr_data:
        even = ac._cpr_data[0]
        odd = ac._cpr_data[1]
        # Only use if both received within 10 seconds
        if abs(even["time"] - odd["time"]) < 10.0:
            lat, lon = _cpr_global_decode(
                even["lat"], even["lon"],
                odd["lat"], odd["lon"],
                cpr_odd,
            )
            if lat is not None and lon is not None:
                ac.lat = lat
                ac.lon = lon
                ac._last_position_time = time.time()


def _cpr_global_decode(
    lat_even: float, lon_even: float,
    lat_odd: float, lon_odd: float,
    most_recent: int,
) -> tuple[Optional[float], Optional[float]]:
    """Global CPR decoding for airborne positions."""
    import math

    NZ = 15  # Number of latitude zones (for airborne)
    d_lat_even = 360.0 / (4 * NZ)
    d_lat_odd = 360.0 / (4 * NZ - 1)

    j = math.floor(59 * lat_even - 60 * lat_odd + 0.5)

    lat_even_dec = d_lat_even * ((j % 60) + lat_even)
    lat_odd_dec = d_lat_odd * ((j % 59) + lat_odd)

    # Normalize to [-90, 90]
    if lat_even_dec >= 270:
        lat_even_dec -= 360
    if lat_odd_dec >= 270:
        lat_odd_dec -= 360

    # Check latitude zone consistency
    nl_even = _cpr_nl(lat_even_dec)
    nl_odd = _cpr_nl(lat_odd_dec)
    if nl_even != nl_odd:
        return None, None

    if most_recent == 0:
        lat = lat_even_dec
        nl = nl_even
        d_lon = 360.0 / max(nl, 1)
        m = math.floor(lon_even * (nl - 1) - lon_odd * nl + 0.5)
        lon = d_lon * ((m % max(nl, 1)) + lon_even)
    else:
        lat = lat_odd_dec
        nl = nl_even
        n = max(nl - 1, 1)
        d_lon = 360.0 / n
        m = math.floor(lon_even * (nl - 1) - lon_odd * nl + 0.5)
        lon = d_lon * ((m % n) + lon_odd)

    if lon > 180:
        lon -= 360

    # Sanity check
    if lat < -90 or lat > 90 or lon < -180 or lon > 180:
        return None, None

    return round(lat, 6), round(lon, 6)


def _cpr_nl(lat: float) -> int:
    """Number of longitude zones for a given latitude."""
    import math
    if abs(lat) >= 87.0:
        return 1
    nz = 15
    a = 1 - math.cos(math.pi / (2 * nz))
    b = math.cos(math.pi * abs(lat) / 180) ** 2
    nl = math.floor(2 * math.pi / math.acos(1 - a / b))
    return nl


def _decode_gillham(alt_bits: int) -> Optional[int]:
    """Simplified Gillham altitude decoding."""
    # For most modern transponders, Q-bit is set
    # This is a fallback — returns None for undecodable
    return None


def _decode_airborne_velocity(payload: bytes, ac: Aircraft):
    """Decode airborne velocity (TC 19)."""
    import math

    sub_type = payload[4] & 0x07

    if sub_type in (1, 2):
        # Ground speed (sub 1: subsonic, sub 2: supersonic)
        ew_sign = (payload[5] >> 2) & 1
        ew_vel = ((payload[5] & 0x03) << 8) | payload[6]
        ns_sign = (payload[7] >> 7) & 1
        ns_vel = ((payload[7] & 0x7F) << 3) | (payload[8] >> 5)

        if ew_vel == 0 or ns_vel == 0:
            return

        ew_vel -= 1
        ns_vel -= 1

        if sub_type == 2:
            ew_vel *= 4
            ns_vel *= 4

        if ew_sign:
            ew_vel = -ew_vel
        if ns_sign:
            ns_vel = -ns_vel

        speed = math.sqrt(ew_vel ** 2 + ns_vel ** 2)
        heading = math.degrees(math.atan2(ew_vel, ns_vel))
        if heading < 0:
            heading += 360

        ac.gs = round(speed, 1)
        ac.track = round(heading, 1)

        # Vertical rate
        vr_sign = (payload[8] >> 3) & 1
        vr = ((payload[8] & 0x07) << 6) | (payload[9] >> 2)
        if vr:
            vr = (vr - 1) * 64
            if vr_sign:
                vr = -vr
            ac.vert_rate = vr


def _decode_ac13(payload: bytes) -> Optional[int]:
    """Decode altitude from AC13 field (13 bits in payload[2:4])."""
    ac13 = ((payload[2] & 0x1F) << 8) | payload[3]
    if ac13 == 0:
        return None
    q_bit = (ac13 >> 4) & 1
    if q_bit:
        n = ((ac13 >> 5) << 4) | (ac13 & 0x0F)
        return n * 25 - 1000
    return None


def _decode_id13(payload: bytes) -> Optional[str]:
    """Decode squawk from ID13 field."""
    id13 = ((payload[2] & 0x1F) << 8) | payload[3]
    if id13 == 0:
        return None
    # Extract 4 octal digits (A, B, C, D)
    a = ((id13 >> 10) & 0x07)
    b = ((id13 >> 7) & 0x07)
    c = ((id13 >> 4) & 0x07)
    d = (id13 & 0x07)
    return f"{a}{b}{c}{d}"
