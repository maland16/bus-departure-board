import importlib
import json
import re
import stat
import sys

from busboard.config import EXAMPLE_CONFIG
from busboard.publish import Publisher
from busboard.realtime import RealtimeSnapshot, parse_trip_updates

from conftest import local, trip_updates


def payload(code, now):
    return {"v": 1, "now": now, "tz": -18000, "live": True, "stop": code, "name": f"Stop {code}", "deps": [],
            "alerts": []}


def test_publish_to_local_target(tmp_path):
    site = tmp_path / "site" / "v1"
    site.parent.mkdir()
    pub = Publisher(str(site), "https://example.invalid/v1", tmp_path / "nokey", tmp_path / "data")
    pub.write({"1117": payload("1117", 1000), "586": payload("586", 1000)}, 1000)
    assert pub.push() and pub.last_error is None and pub.pushes == 1

    assert json.loads((site / "stops" / "1117.json").read_text())["stop"] == "1117"
    assert json.loads((site / "index.json").read_text()) == {"v": 1, "now": 1000,
                                                             "stops": {"1117": "Stop 1117", "586": "Stop 586"}}
    assert "no-cache" in (site / ".htaccess").read_text()
    assert stat.S_IMODE((site / "stops" / "586.json").stat().st_mode) == 0o644  # readable by the web server
    assert not list(site.rglob("*.tmp"))

    pub.write({"1117": payload("1117", 1030)}, 1030)  # a second round updates in place
    assert pub.push() and pub.pushes == 2
    assert json.loads((site / "stops" / "1117.json").read_text())["now"] == 1030


def test_target_must_end_in_v1(tmp_path):
    for target in [str(tmp_path / "site"), "user@host:board.example.com", "user@host:"]:
        pub = Publisher(target, "", tmp_path / "nokey", tmp_path / "data")
        pub.write({}, 0)
        assert not pub.push() and "must end in /v1" in pub.last_error
    assert not (tmp_path / "site").exists()


def test_unreachable_host_is_recorded_not_raised(tmp_path):
    pub = Publisher("nobody@host.invalid:site/v1", "", tmp_path / "nokey", tmp_path / "data")
    pub.write({"1117": payload("1117", 0)}, 0)
    assert not pub.push()
    assert pub.last_error and pub.last_ok is None
    assert pub.status()["error"] == pub.last_error


def load_app(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    text = re.sub(r'^data_dir = .*$', f'data_dir = "{tmp_path / "data"}"', EXAMPLE_CONFIG.read_text(), flags=re.M)
    cfg.write_text(re.sub(r"^stops = \[.*?\]", 'stops = ["1117"]', text, flags=re.S | re.M))
    monkeypatch.setenv("BUSBOARD_CONFIG", str(cfg))
    if "busboard.app" in sys.modules:
        return importlib.reload(sys.modules["busboard.app"])
    return importlib.import_module("busboard.app")


def test_payload_rows_and_stale_realtime(tmp_path, monkeypatch, index):
    app = load_app(tmp_path, monkeypatch)
    app.static.index = index
    now = local(2026, 11, 6, 17, 55)
    _, trips = parse_trip_updates(trip_updates({"T_STOP": {2: local(2026, 11, 6, 18, 9)}}))

    app.realtime.snapshot = RealtimeSnapshot(trips=trips, trip_updates_fetched_at=now - 30)
    monkeypatch.setattr(app.time, "time", lambda: now)
    p = app._payload("1117", now)
    first = p["deps"][0]
    assert p["live"] is True and p["tz"] == -18000 and (first["src"], first["t"]) == (2, local(2026, 11, 6, 18, 9))
    assert len(p["deps"]) <= app.cfg.payload_rows

    # The feed has been failing for 15 min: schedule only, and the payload says so
    app.realtime.snapshot = RealtimeSnapshot(trips=trips, trip_updates_fetched_at=now - 900)
    p = app._payload("1117", now)
    assert p["live"] is False
    assert all(d["src"] == 0 and d["t"] == d["s"] for d in p["deps"])


def test_payload_shape():
    from busboard.departures import Departure
    from busboard.payload import build_payload
    from conftest import TZ

    now = local(2026, 11, 6, 18, 0)
    deps = [Departure("20261106", f"T{i}", "0:1117", 2, "4", "4", "", "Pulse", False, now + 300 * i, now + 300 * i,
                      i % 3, None, None, False, False, False) for i in (1, 2)]
    p = build_payload("1117", "Congress St + Center St", deps, now, TZ)
    assert p["v"] == 1 and p["now"] == now and len(p["deps"]) == 2
    assert p["tz"] == -5 * 3600 and p["live"] is True  # EST in November
    assert set(p["deps"][0]) == {"r", "h", "t", "s", "src"}


def test_cycle_publishes_logs_and_shows_on_the_dashboard(tmp_path, monkeypatch, index):
    from fastapi.testclient import TestClient

    app = load_app(tmp_path, monkeypatch)
    site = tmp_path / "site" / "v1"
    site.parent.mkdir()
    app.publisher.target = str(site)  # a local rsync target
    monkeypatch.setattr(app, "PUBLISHING", True)
    app.static.index = index
    now = local(2026, 11, 6, 17, 55)
    monkeypatch.setattr(app.time, "time", lambda: now)
    _, trips = parse_trip_updates(trip_updates({"T_STOP": {2: local(2026, 11, 6, 18, 9)}}))
    monkeypatch.setattr(app.realtime, "refresh",
                        lambda: setattr(app.realtime, "snapshot",
                                        RealtimeSnapshot(trips=trips, trip_updates_fetched_at=now - 5,
                                                         trip_updates_raw=b"tu", vehicle_positions_raw=b"vp")))

    app._cycle()
    assert app.cycle_status["error"] is None
    published = json.loads((site / "stops" / "1117.json").read_text())
    assert published == app._payloads["1117"] and published["deps"][0]["t"] == local(2026, 11, 6, 18, 9)
    assert app.store.counts_since(0)["1117"]["predictions"] >= 1
    assert app.archive.last_saved == now and app.cycle_status["logged_rows"] >= 1

    # Next poll: published again, not logged again until log_interval_s has passed
    monkeypatch.setattr(app.time, "time", lambda: now + 30)
    app._cycle()
    assert app.archive.last_saved == now and json.loads((site / "index.json").read_text())["now"] == now + 30

    client = TestClient(app.app)  # without the lifespan: no background tasks
    assert client.get("/stops/1117/departures").json() == app._payloads["1117"]
    assert client.get("/stops/1117/departures?debug=1").json()["departures"][0]["trip_id"] == "T_STOP"
    page = client.get("/")
    assert page.status_code == 200 and "1117" in page.text and "Congress St + Center St" in page.text
    assert client.get("/health").json()["publish"]["pushes"] == 2


def test_dashboard_before_anything_loaded(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    app = load_app(tmp_path, monkeypatch)
    app.static.index = None
    page = TestClient(app.app).get("/")
    assert page.status_code == 200 and "Needs attention" in page.text and "static schedule" in page.text
