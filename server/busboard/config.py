import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"  # not in git: holds the publish target
EXAMPLE_CONFIG = DEFAULT_CONFIG.with_name("config.example.toml")


@dataclass(frozen=True)
class Config:
    stops: list[str]
    data_dir: Path
    static_url: str
    trip_updates_url: str
    vehicle_positions_url: str
    static_check_interval_s: int
    realtime_poll_interval_s: int
    realtime_hold_missing_s: int
    realtime_max_age_s: int
    window_ahead_s: int
    past_grace_s: int
    stale_vehicle_s: int
    block_layover_s: int | None
    drift_correction: bool
    drift_window_s: int
    payload_rows: int
    log_interval_s: int
    log_ahead_s: int
    retention_days: int
    archive_days: int
    static_versions_kept: int
    publish_enabled: bool
    publish_target: str
    publish_public_url: str
    publish_ssh_key: Path
    publish_verify_interval_s: int


def load_config(path: Path | None = None) -> Config:
    """Unknown keys and sections (e.g. the old [ucop] and [minimap]) are ignored."""
    path = Path(path or os.environ.get("BUSBOARD_CONFIG", DEFAULT_CONFIG))
    if not path.exists() and path == DEFAULT_CONFIG:
        raise FileNotFoundError(f"{path} not found. Start from the template: cp {EXAMPLE_CONFIG.name} {path.name}")
    raw = tomllib.loads(path.read_text())
    feeds, deps = raw["feeds"], raw["departures"]
    logs, pub = raw.get("logging", {}), raw.get("publish", {})
    old_ucop = raw.get("ucop", {})  # settings that lived there before the UCOP comparison was removed
    data_dir = Path(raw.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir
    return Config(
        stops=[str(s) for s in raw["stops"]],
        data_dir=data_dir,
        static_url=feeds["static_url"],
        trip_updates_url=feeds["trip_updates_url"],
        vehicle_positions_url=feeds["vehicle_positions_url"],
        static_check_interval_s=feeds["static_check_interval_s"],
        realtime_poll_interval_s=feeds["realtime_poll_interval_s"],
        realtime_hold_missing_s=feeds.get("realtime_hold_missing_s", 0),
        realtime_max_age_s=feeds.get("realtime_max_age_s", 600),
        window_ahead_s=deps["window_ahead_s"],
        past_grace_s=deps["past_grace_s"],
        stale_vehicle_s=deps["stale_vehicle_s"],
        block_layover_s=deps.get("block_layover_s", 0) if deps.get("block_propagation", False) else None,
        drift_correction=bool(deps.get("drift_correction", False)),
        drift_window_s=int(deps.get("drift_window_s", 3 * 3600)),
        payload_rows=int(deps.get("payload_rows", deps.get("board_rows", 12))),
        log_interval_s=int(logs.get("log_interval_s", old_ucop.get("compare_interval_s", 120))),
        log_ahead_s=int(logs.get("log_ahead_s", 3600)),
        retention_days=int(logs.get("retention_days", old_ucop.get("retention_days", 30))),
        archive_days=int(logs.get("archive_days", 30)),
        static_versions_kept=int(logs.get("static_versions_kept", 60)),
        publish_enabled=bool(pub.get("enabled", False)),
        publish_target=str(pub.get("target", "")).strip(),
        publish_public_url=str(pub.get("public_url", "")).rstrip("/"),
        publish_ssh_key=Path(str(pub.get("ssh_key", "~/.ssh/busboard_publish"))).expanduser(),
        publish_verify_interval_s=int(pub.get("verify_interval_s", 600)),
    )
