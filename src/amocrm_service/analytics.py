from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from amocrm_service.filters import AnalyticsFilter
from amocrm_service.repository import Repository


class AnalyticsService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def leads_by_status(self) -> list[dict[str, Any]]:
        leads = self.repository.all_payloads("leads")
        statuses = Counter(str(lead.get("status_id")) for lead in leads)
        return [
            {"status_id": status_id, "leads_count": count}
            for status_id, count in statuses.most_common()
        ]

    def tasks_summary(self) -> dict[str, Any]:
        return self.repository.tasks_summary_counts()

    def pipeline_filter_options(self) -> list[dict[str, Any]]:
        pipelines = self.repository.all_payloads("pipelines")
        options = []
        for pipeline in pipelines:
            statuses = [
                {
                    "status_id": int(status["id"]),
                    "status_name": status.get("name") or f"Status {status['id']}",
                    "status_sort": int(status.get("sort") or 0),
                }
                for status in pipeline.get("_embedded", {}).get("statuses", [])
            ]
            statuses.sort(key=lambda item: (item["status_sort"], item["status_name"]))
            options.append({
                "pipeline_id": int(pipeline["id"]),
                "pipeline_name": pipeline.get("name") or f"Pipeline {pipeline['id']}",
                "pipeline_sort": int(pipeline.get("sort") or 0),
                "statuses": statuses,
            })
        options.sort(key=lambda item: (item["pipeline_sort"], item["pipeline_name"]))
        return options

    def pipeline_summary(self, analytics_filter: AnalyticsFilter | None = None) -> dict[str, Any]:
        analytics_filter = analytics_filter or AnalyticsFilter.all_enabled()
        pipelines = self.repository.all_payloads("pipelines")

        pipeline_names: dict[int, str] = {}
        status_names: dict[tuple[int, int], str] = {}
        status_sort: dict[tuple[int, int], int] = {}

        for pipeline in pipelines:
            pipeline_id = int(pipeline["id"])
            pipeline_names[pipeline_id] = pipeline.get("name") or f"Pipeline {pipeline_id}"
            for status in pipeline.get("_embedded", {}).get("statuses", []):
                key = (pipeline_id, int(status["id"]))
                status_names[key] = status.get("name") or f"Status {status['id']}"
                status_sort[key] = int(status.get("sort") or 0)

        pipeline_totals: dict[int, dict[str, Any]] = defaultdict(lambda: {
            "pipeline_id": 0,
            "pipeline_name": "",
            "leads_count": 0,
            "open_count": 0,
            "won_count": 0,
            "lost_count": 0,
            "total_price": 0,
            "statuses": [],
        })

        for row in self.repository.lead_stage_summary_rows(analytics_filter):
            pipeline_id = row["pipeline_id"]
            status_id = row["status_id"]
            values = {
                "leads_count": row["leads_count"],
                "open_count": 0 if status_id in {142, 143} else row["leads_count"],
                "won_count": row["leads_count"] if status_id == 142 else 0,
                "lost_count": row["leads_count"] if status_id == 143 else 0,
                "total_price": row["total_price"],
            }
            pipeline = pipeline_totals[pipeline_id]
            pipeline["pipeline_id"] = pipeline_id
            pipeline["pipeline_name"] = pipeline_names.get(pipeline_id, f"Pipeline {pipeline_id}")
            for key in ("leads_count", "open_count", "won_count", "lost_count", "total_price"):
                pipeline[key] += values[key]
            pipeline["statuses"].append({
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline["pipeline_name"],
                "status_id": status_id,
                "status_name": status_names.get((pipeline_id, status_id), f"Status {status_id}"),
                "status_sort": status_sort.get((pipeline_id, status_id), 0),
                **values,
            })

        pipelines_list = list(pipeline_totals.values())
        for pipeline in pipelines_list:
            pipeline["statuses"].sort(key=lambda item: (item["status_sort"], item["status_name"]))
        pipelines_list.sort(key=lambda item: item["leads_count"], reverse=True)
        leads_count = sum(p["leads_count"] for p in pipelines_list)

        return {
            "totals": {
                "pipelines_count": len(pipelines_list),
                "leads_count": leads_count,
                "open_count": sum(p["open_count"] for p in pipelines_list),
                "won_count": sum(p["won_count"] for p in pipelines_list),
                "lost_count": sum(p["lost_count"] for p in pipelines_list),
                "total_price": sum(p["total_price"] for p in pipelines_list),
            },
            "pipelines": pipelines_list,
        }
