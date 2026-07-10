from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ForgetForgeConfig:
    home: Path
    db_path: Path
    archive_dir: Path
    pruner_interval_hours: int = 6
    cold_retention_threshold: float = 0.40
    hot_window_days: int = 7
    no_recall_archive_days: int = 180
    retrieval_events_max_age_days: int = 90
    retrieval_events_max_per_memory: int = 100


def default_home() -> Path:
    override = os.environ.get("FORGETFORGE_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".forgetforge"


def load_config(path: Path | None = None) -> ForgetForgeConfig:
    home = default_home()
    config_path = path or (home / "config.yaml")
    data: dict[str, object] = {}
    if config_path.exists():
        # File OSError (permission, IO) must propagate unchanged for CLI storage_error.
        try:
            raw = config_path.read_text(encoding="utf-8")
        except UnicodeError as e:
            raise OSError(f"invalid config at {config_path}: {e}") from e
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise OSError(f"invalid config at {config_path}: {e}") from e
        if isinstance(loaded, dict):
            data = loaded
    pruner = data.get("pruner", {}) if isinstance(data.get("pruner"), dict) else {}
    thresholds = data.get("thresholds", {}) if isinstance(data.get("thresholds"), dict) else {}
    try:
        cold_retention_threshold = float(thresholds.get("cold_retention", 0.40))
        if not math.isfinite(cold_retention_threshold):
            raise ValueError(f"cold_retention must be finite, got {cold_retention_threshold!r}")
        return ForgetForgeConfig(
            home=home,
            db_path=home / "db.sqlite",
            archive_dir=home / "archive",
            pruner_interval_hours=int(pruner.get("interval_hours", 6)),
            cold_retention_threshold=cold_retention_threshold,
            hot_window_days=int(thresholds.get("hot_window_days", 7)),
            no_recall_archive_days=int(thresholds.get("no_recall_archive_days", 180)),
            retrieval_events_max_age_days=int(pruner.get("retrieval_events_max_age_days", 90)),
            retrieval_events_max_per_memory=int(pruner.get("retrieval_events_max_per_memory", 100)),
        )
    except (ValueError, TypeError, OverflowError) as e:
        raise OSError(f"invalid config at {config_path}: {e}") from e


__all__ = ["ForgetForgeConfig", "default_home", "load_config"]
