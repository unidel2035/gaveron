"""Tests for HTTP server."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from gaveron.decoder import AircraftStore
from gaveron.server import GaveronServer


@pytest.fixture
def store():
    s = AircraftStore()
    ac = s.get_or_create("a1b2c3")
    ac.flight = "TEST123"
    ac.alt_baro = 35000
    ac.lat = 55.75
    ac.lon = 37.62
    ac.messages = 42
    return s


@pytest.fixture
def server(store, tmp_path):
    return GaveronServer(
        store,
        history_dir=str(tmp_path),
        receiver_lat=55.75,
        receiver_lon=37.62,
    )


@pytest.mark.asyncio
async def test_aircraft_endpoint(aiohttp_client, store, tmp_path):
    srv = GaveronServer(store, history_dir=str(tmp_path))
    client = await aiohttp_client(srv.app)

    resp = await client.get("/data/aircraft.json")
    assert resp.status == 200
    data = await resp.json()
    assert "aircraft" in data
    assert len(data["aircraft"]) == 1
    assert data["aircraft"][0]["hex"] == "a1b2c3"
    assert data["aircraft"][0]["flight"] == "TEST123"


@pytest.mark.asyncio
async def test_receiver_endpoint(aiohttp_client, store, tmp_path):
    srv = GaveronServer(
        store, history_dir=str(tmp_path),
        receiver_lat=55.75, receiver_lon=37.62,
    )
    client = await aiohttp_client(srv.app)

    resp = await client.get("/data/receiver.json")
    assert resp.status == 200
    data = await resp.json()
    assert data["lat"] == 55.75
    assert data["lon"] == 37.62
    assert "version" in data


@pytest.mark.asyncio
async def test_health_endpoint(aiohttp_client, store, tmp_path):
    srv = GaveronServer(store, history_dir=str(tmp_path))
    client = await aiohttp_client(srv.app)

    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["aircraft_count"] == 1


@pytest.mark.asyncio
async def test_stats_endpoint(aiohttp_client, store, tmp_path):
    srv = GaveronServer(store, history_dir=str(tmp_path))
    client = await aiohttp_client(srv.app)

    resp = await client.get("/data/stats.json")
    assert resp.status == 200
    data = await resp.json()
    assert "latest" in data
    assert data["latest"]["aircraft_total"] == 1


@pytest.mark.asyncio
async def test_chunks_empty(aiohttp_client, store, tmp_path):
    srv = GaveronServer(store, history_dir=str(tmp_path))
    client = await aiohttp_client(srv.app)

    resp = await client.get("/chunks/chunks.json")
    assert resp.status == 200
    data = await resp.json()
    assert data["chunks"] == []


@pytest.mark.asyncio
async def test_cors_headers(aiohttp_client, store, tmp_path):
    srv = GaveronServer(store, history_dir=str(tmp_path))
    client = await aiohttp_client(srv.app)

    resp = await client.get("/data/aircraft.json")
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
