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
