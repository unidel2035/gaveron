"""Tests for ADS-B decoder."""

import time
from gaveron.decoder import (
    AircraftStore,
    decode_sbs_message,
    _cpr_nl,
)


def test_aircraft_store_create():
    store = AircraftStore()
    ac = store.get_or_create("a1b2c3")
    assert ac.hex == "a1b2c3"
    assert ac.messages == 0


def test_aircraft_store_same_instance():
    store = AircraftStore()
    ac1 = store.get_or_create("a1b2c3")
    ac1.messages = 5
    ac2 = store.get_or_create("a1b2c3")
    assert ac2.messages == 5


def test_aircraft_store_cleanup():
    store = AircraftStore(timeout=10.0)
    ac = store.get_or_create("a1b2c3")
    ac._last_message_time = time.time() - 20
    store.cleanup(time.time())
    assert "a1b2c3" not in store.aircraft


def test_aircraft_to_dict():
    store = AircraftStore()
    ac = store.get_or_create("a1b2c3")
    ac.flight = "UAL123"
    ac.alt_baro = 35000
    ac.lat = 51.5
    ac.lon = -0.1
    ac.gs = 450.3
    ac.track = 270.5
    ac.messages = 100

    d = ac.to_dict(time.time())
    assert d["hex"] == "a1b2c3"
    assert d["flight"] == "UAL123"
    assert d["alt_baro"] == 35000
    assert d["lat"] == 51.5
    assert d["lon"] == -0.1
    assert d["gs"] == 450.3
    assert d["messages"] == 100


def test_store_to_json():
    store = AircraftStore()
    ac = store.get_or_create("a1b2c3")
    ac.flight = "TEST"
    ac.messages = 1

    data = store.to_json()
    assert "now" in data
    assert "messages" in data
    assert "aircraft" in data
    assert len(data["aircraft"]) == 1
    assert data["aircraft"][0]["hex"] == "a1b2c3"


def test_sbs_msg_type1_callsign():
    store = AircraftStore()
    line = "MSG,1,1,1,a1b2c3,,2024/01/01,12:00:00.000,2024/01/01,12:00:00.000,UAL123,,,,,,,,,,,"
    decode_sbs_message(line, store)
    assert "a1b2c3" in store.aircraft
    assert store.aircraft["a1b2c3"].flight == "UAL123"


def test_sbs_msg_type3_position():
    store = AircraftStore()
    line = "MSG,3,1,1,a1b2c3,,2024/01/01,12:00:00.000,2024/01/01,12:00:00.000,,35000,,,51.500000,-0.100000,,,,,,0"
    decode_sbs_message(line, store)
    ac = store.aircraft["a1b2c3"]
    assert ac.alt_baro == 35000
    assert ac.lat == 51.5
    assert ac.lon == -0.1


def test_sbs_msg_type4_velocity():
    store = AircraftStore()
    line = "MSG,4,1,1,a1b2c3,,2024/01/01,12:00:00.000,2024/01/01,12:00:00.000,,,450.0,270.5,,,1500,,,,,0"
    decode_sbs_message(line, store)
    ac = store.aircraft["a1b2c3"]
    assert ac.gs == 450.0
    assert ac.track == 270.5
    assert ac.vert_rate == 1500


def test_sbs_invalid_message():
    store = AircraftStore()
    decode_sbs_message("NOT,A,VALID,MESSAGE", store)
    assert len(store.aircraft) == 0


def test_sbs_empty_hex():
    store = AircraftStore()
    line = "MSG,1,1,1,,,2024/01/01,12:00:00.000,2024/01/01,12:00:00.000,UAL123,,,,,,,,,,,"
    decode_sbs_message(line, store)
    assert len(store.aircraft) == 0


def test_cpr_nl():
    # NL at equator should be 59
    assert _cpr_nl(0.0) == 59
    # NL at poles should be 1
    assert _cpr_nl(87.0) == 1
    assert _cpr_nl(-87.0) == 1
