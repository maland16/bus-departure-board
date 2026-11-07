import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


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
    window_ahead_s: int
    past_grace_s: int
    stale_vehicle_s: int
    block_layover_s: int | None
    board_rows: int
    ucop_base_url: str
    ucop_display_name: str
    compare_interval_s: int
    match_tolerance_s: int
    retention_days: int
    archive_days: int
    static_versions_kept: int


def load_config(path: Path | None = None) -> Config:
    path = Path(path or os.environ.get("BUSBOARD_CONFIG", DEFAULT_CONFIG))
    raw = tomllib.loads(path.read_text())
    feeds, deps, ucop = raw["feeds"], raw["departures"], raw["ucop"]
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
        window_ahead_s=deps["window_ahead_s"],
        past_grace_s=deps["past_grace_s"],
        stale_vehicle_s=deps["stale_vehicle_s"],
        block_layover_s=deps.get("block_layover_s", 0) if deps.get("block_propagation", False) else None,
        board_rows=deps["board_rows"],
        ucop_base_url=ucop["base_url"].rstrip("/"),
        ucop_display_name=ucop["display_name"],
        compare_interval_s=ucop["compare_interval_s"],
        match_tolerance_s=ucop["match_tolerance_s"],
        retention_days=ucop["retention_days"],
        archive_days=raw.get("logging", {}).get("archive_days", 7),
        static_versions_kept=raw.get("logging", {}).get("static_versions_kept", 30),
    )
