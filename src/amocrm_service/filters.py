from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalyticsFilter:
    pipeline_ids: set[int]
    status_ids: set[int]

    @classmethod
    def all_enabled(cls) -> "AnalyticsFilter":
        return cls(pipeline_ids=set(), status_ids=set())

    @property
    def is_all_enabled(self) -> bool:
        return not self.pipeline_ids and not self.status_ids

    def allows(self, pipeline_id: int, status_id: int) -> bool:
        if self.is_all_enabled:
            return True
        if pipeline_id not in self.pipeline_ids:
            return False
        return not self.status_ids or status_id in self.status_ids

    def to_json(self) -> dict[str, list[int]]:
        return {
            "pipeline_ids": sorted(self.pipeline_ids),
            "status_ids": sorted(self.status_ids),
        }


def filter_path(db_path: Path) -> Path:
    return db_path.parent / "analytics_filter.json"


def load_analytics_filter(db_path: Path) -> AnalyticsFilter:
    path = filter_path(db_path)
    if not path.exists():
        return AnalyticsFilter.all_enabled()
    data = json.loads(path.read_text(encoding="utf-8"))
    return AnalyticsFilter(
        pipeline_ids={int(item) for item in data.get("pipeline_ids", [])},
        status_ids={int(item) for item in data.get("status_ids", [])},
    )


def save_analytics_filter(db_path: Path, data: dict[str, Any]) -> AnalyticsFilter:
    analytics_filter = AnalyticsFilter(
        pipeline_ids={int(item) for item in data.get("pipeline_ids", [])},
        status_ids={int(item) for item in data.get("status_ids", [])},
    )
    path = filter_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analytics_filter.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return analytics_filter

