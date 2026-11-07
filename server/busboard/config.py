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
    board_rows: int
    payload_rows: int
    ucop_base_url: str
    ucop_display_name: str
    compare_interval_s: int
    match_tolerance_s: int
    retention_days: int
    archive_days: int
    static_versions_kept: int
    publish_enabled: bool
    publish_target: str
    publish_public_url: str
    publish_interval_s: int
    publish_ssh_key: Path
    publish_verify_interval_s: int


def load_config(path: Path | None = None) -> Config:
    path = Path(path or os.environ.get("BUSBOARD_CONFIG", DEFAULT_CONFIG))
    if not path.exists() and path == DEFAULT_CONFIG:
        raise FileNotFoundError(f"{path} not found. Start from the template: cp {EXAMPLE_CONFIG.name} {path.name}")
    raw = tomllib.loads(path.read_text())
    feeds, deps, ucop = raw["feeds"], raw["departures"], raw["ucop"]
    pub = raw.get("publish", {})
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
        board_rows=deps["board_rows"],
        payload_rows=deps.get("payload_rows", deps["board_rows"]),
        ucop_base_url=ucop["base_url"].rstrip("/"),
        ucop_display_name=ucop["display_name"],
        compare_interval_s=ucop["compare_interval_s"],
        match_tolerance_s=ucop["match_tolerance_s"],
        retention_days=ucop["retention_days"],
        archive_days=raw.get("logging", {}).get("archive_days", 7),
        static_versions_kept=raw.get("logging", {}).get("static_versions_kept", 30),
        publish_enabled=bool(pub.get("enabled", False)),
        publish_target=str(pub.get("target", "")).strip(),
        publish_public_url=str(pub.get("public_url", "")).rstrip("/"),
        publish_interval_s=int(pub.get("interval_s", 30)),
        publish_ssh_key=Path(str(pub.get("ssh_key", "~/.ssh/busboard_publish"))).expanduser(),
        publish_verify_interval_s=int(pub.get("verify_interval_s", 600)),
    )
