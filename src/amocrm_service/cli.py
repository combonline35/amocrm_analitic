from __future__ import annotations

import argparse
import json

from amocrm_service.analytics import AnalyticsService
from amocrm_service.analytics_query import AnalyticsQuery, FlexibleAnalyticsService
from amocrm_service.config import load_settings
from amocrm_service.dashboard import write_dashboard
from amocrm_service.db import connect, init_db
from amocrm_service.filters import load_analytics_filter
from amocrm_service.repository import Repository


def _services(settings):
    from amocrm_service.amocrm import AmoCRMClient
    from amocrm_service.sync import SyncService

    init_db(settings.db_path)
    repo = Repository(connect(settings.db_path))
    client = AmoCRMClient(settings)
    return client, repo, SyncService(client, repo)


def main() -> None:
    parser = argparse.ArgumentParser(prog="amocrm-service")
    parser.add_argument("--user", default=None)
    parser.add_argument("--account", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--entity", choices=[
        "leads",
        "contacts",
        "companies",
        "tasks",
        "customers",
        "events",
        "lead_notes",
        "contact_notes",
        "company_notes",
        "customer_notes",
        "users",
        "pipelines",
        "lead_custom_fields",
        "contact_custom_fields",
        "company_custom_fields",
        "customer_custom_fields",
        "catalogs",
        "catalog_elements",
        "salesbots",
    ])
    sync_parser.add_argument("--all", action="store_true")

    queue_parser = sub.add_parser("process-queue")
    queue_parser.add_argument("--limit", type=int, default=25)

    bootstrap_parser = sub.add_parser("bootstrap")
    bootstrap_parser.add_argument("--entities", nargs="*")

    resync_parser = sub.add_parser("resync")
    resync_parser.add_argument("--entities", nargs="*")

    sub.add_parser("summary")

    conversations_parser = sub.add_parser("conversations")
    conversations_parser.add_argument(
        "action",
        choices=[
            "discover",
            "import-lead",
            "probe-recordings",
            "download-recordings",
            "transcribe",
            "analyze",
            "note-preview",
            "post-note",
            "auto-run",
            "auto-dry-run",
            "export",
            "list",
            "analysis",
        ],
        help="Conversation pipeline action",
    )
    conversations_parser.add_argument("--limit", type=int, default=100)
    conversations_parser.add_argument("--lead-id", type=int)
    conversations_parser.add_argument("--conversation-id")
    conversations_parser.add_argument("--force", action="store_true")

    query_parser = sub.add_parser("query")
    query_parser.add_argument("--json", required=True, help="Analytics query JSON payload")

    dashboard_parser = sub.add_parser("dashboard")
    dashboard_parser.add_argument("--output", default="dashboard.html")

    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8010)
    serve_parser.add_argument("--skip-cleanup", action="store_true")

    args = parser.parse_args()
    settings = load_settings(account_key=args.account, user_key=args.user)
    if args.command == "init-db":
        init_db(settings.db_path)
        print(json.dumps({
            "ok": True,
            "user_key": settings.user_key,
            "account_key": settings.account_key,
            "db_path": str(settings.db_path),
        }, ensure_ascii=False))
        return

    if args.command == "sync":
        client, _repo, service = _services(settings)
        try:
            if args.all:
                result = service.sync_all()
            elif args.entity:
                result = service.sync_entity(args.entity)
            else:
                raise SystemExit("Use --entity ENTITY or --all")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            client.close()
        return

    if args.command == "process-queue":
        client, _repo, service = _services(settings)
        try:
            result = service.process_queue(settings.account_key, limit=args.limit)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            client.close()
        return

    if args.command in {"bootstrap", "resync"}:
        client, _repo, service = _services(settings)
        try:
            result = service.run_sync_job(
                settings.account_key,
                args.command,
                entity_types=args.entities or None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            client.close()
        return

    if args.command == "summary":
        init_db(settings.db_path)
        repo = Repository(connect(settings.db_path))
        analytics = AnalyticsService(repo)
        analytics_filter = load_analytics_filter(settings.db_path)
        print(json.dumps({
            "pipeline_summary": analytics.pipeline_summary(analytics_filter),
            "leads_by_status": analytics.leads_by_status(),
            "tasks": analytics.tasks_summary(),
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "conversations":
        from amocrm_service.amocrm import AmoCRMClient
        from amocrm_service.conversation_automation import ConversationAutomationService
        from amocrm_service.conversation_audio import ConversationAudioService
        from amocrm_service.conversation_export import ConversationExportService
        from amocrm_service.conversation_notes import build_lead_analysis_note, find_record_and_analysis
        from amocrm_service.conversation_settings import conversation_settings
        from amocrm_service.conversation_transcription import build_transcription_service
        from amocrm_service.conversations import ConversationPipeline
        from amocrm_service.tenancy import load_account_settings

        init_db(settings.db_path)
        repo = Repository(connect(settings.db_path))
        pipeline = ConversationPipeline(repo)
        if args.action == "discover":
            result = pipeline.discover_from_hub(settings.account_key)
        elif args.action == "import-lead":
            if not args.lead_id:
                raise SystemExit("--lead-id is required")
            client = AmoCRMClient(settings)
            try:
                result = pipeline.import_lead_context(settings.account_key, client, args.lead_id)
            finally:
                client.close()
        elif args.action == "probe-recordings":
            result = ConversationAudioService(repo).probe_recordings(settings.account_key, limit=args.limit)
        elif args.action == "download-recordings":
            result = ConversationAudioService(repo).download_accessible_recordings(
                settings.account_key,
                settings.workspace_dir / "recordings",
                limit=args.limit,
            )
        elif args.action == "transcribe":
            try:
                result = build_transcription_service(repo).transcribe_downloaded(
                    settings.account_key,
                    limit=args.limit,
                )
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
        elif args.action == "analyze":
            raw_settings = load_account_settings(
                user_key=settings.user_key,
                account_key=settings.account_key,
                data_root=settings.data_root,
            )
            result = pipeline.analyze_transcribed(
                settings.account_key,
                limit=args.limit,
                force=args.force,
                analysis_config=conversation_settings(raw_settings),
            )
        elif args.action in {"note-preview", "post-note"}:
            record, analysis = find_record_and_analysis(
                repo.list_conversation_records(settings.account_key, limit=args.limit),
                repo.list_conversation_analyses(settings.account_key, limit=args.limit),
                conversation_id=args.conversation_id,
                lead_id=str(args.lead_id) if args.lead_id else None,
            )
            note_text = build_lead_analysis_note(record, analysis)
            if args.action == "note-preview":
                result = {
                    "lead_id": record.get("lead_id"),
                    "conversation_id": record.get("conversation_id"),
                    "note_text": note_text,
                }
            else:
                lead_id = record.get("lead_id")
                if not lead_id:
                    raise SystemExit("Selected conversation is not linked to a lead")
                client = AmoCRMClient(settings)
                try:
                    note = client.add_lead_note(int(lead_id), note_text)
                finally:
                    client.close()
                repo.update_conversation_record_status(
                    settings.account_key,
                    str(record["conversation_id"]),
                    status=str(record.get("status") or "transcribed"),
                    metadata_patch={
                        "last_posted_note_id": str(note.get("id") or ""),
                        "last_posted_lead_id": str(lead_id),
                    },
                )
                result = {
                    "posted": True,
                    "lead_id": lead_id,
                    "conversation_id": record.get("conversation_id"),
                    "note": note,
                }
        elif args.action == "auto-run":
            result = ConversationAutomationService(settings, repo).run(limit=args.limit, dry_run=False)
        elif args.action == "auto-dry-run":
            result = ConversationAutomationService(settings, repo).run(limit=args.limit, dry_run=True)
        elif args.action == "export":
            result = ConversationExportService(repo).export_csv(
                settings.account_key,
                settings.workspace_dir / "exports" / "conversation_analysis.csv",
                limit=args.limit,
            )
        elif args.action == "list":
            result = repo.list_conversation_records(settings.account_key, limit=args.limit)
        else:
            result = repo.list_conversation_analyses(settings.account_key, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "query":
        init_db(settings.db_path)
        repo = Repository(connect(settings.db_path))
        service = FlexibleAnalyticsService(repo)
        result = service.run(AnalyticsQuery.from_payload(json.loads(args.json)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "dashboard":
        init_db(settings.db_path)
        repo = Repository(connect(settings.db_path))
        analytics = AnalyticsService(repo)
        analytics_filter = load_analytics_filter(settings.db_path)
        path = write_dashboard(
            settings.db_path.parent / args.output,
            analytics.pipeline_summary(analytics_filter),
            analytics.tasks_summary(),
        )
        print(json.dumps({"ok": True, "dashboard": str(path)}, ensure_ascii=False))
        return

    if args.command == "serve":
        from amocrm_service.server import serve

        serve(args.host, args.port, cleanup=not args.skip_cleanup)


if __name__ == "__main__":
    main()
