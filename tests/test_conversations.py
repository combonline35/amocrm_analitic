from __future__ import annotations

from amocrm_service.conversations import (
    ConversationPipeline,
    ConversationRecord,
    QASchemaConversationAnalyzer,
    RuleBasedConversationAnalyzer,
    extract_conversation_records,
    format_transcript_with_roles,
    repair_role_transcript,
)
from amocrm_service.conversation_notes import build_lead_analysis_note, find_record_and_analysis
from amocrm_service.conversation_export import ConversationExportService
from amocrm_service.conversation_settings import conversation_settings, record_matches_filters
from amocrm_service.db import connect, init_db
from amocrm_service.repository import Repository


def test_extract_call_note_as_conversation_record():
    records = extract_conversation_records("demo", {
        "id": 103,
        "entity_id": 55,
        "created_at": 1710000000,
        "note_type": "call_out",
        "params": {
            "duration": 60,
            "link": "https://records.example/call.mp3",
            "phone": "+79999999999",
            "source": "onlinePBX",
        },
    })

    assert len(records) == 1
    record = records[0]
    assert record.conversation_id == "lead_notes:103"
    assert record.lead_id == "55"
    assert record.direction == "outgoing"
    assert record.recording_url == "https://records.example/call.mp3"
    assert record.duration_seconds == 60
    assert record.status == "recording_found"
    assert record.metadata["phone"] == "+79999999999"


def test_extract_contact_call_note_as_conversation_record():
    records = extract_conversation_records("demo", {
        "id": 291194447,
        "entity_id": 30526549,
        "created_at": 1783403560,
        "note_type": "call_in",
        "params": {
            "duration": 62,
            "link": "https://records.example/contact-call.mp3",
            "phone": "+79999999999",
            "source": "telephony",
            "call_status": 4,
        },
    }, "contact_notes")

    assert len(records) == 1
    record = records[0]
    assert record.conversation_id == "contact_notes:291194447"
    assert record.lead_id is None
    assert record.contact_id == "30526549"
    assert record.direction == "incoming"
    assert record.recording_url == "https://records.example/contact-call.mp3"


def test_conversation_pipeline_discovers_and_analyzes_transcribed_calls(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        repo.upsert_entities("lead_notes", [{
            "id": 104,
            "entity_id": 56,
            "created_at": 1710000100,
            "note_type": "call_in",
            "params": {
                "duration": 120,
                "link": "https://records.example/in.mp3",
                "text": "Клиенту интересно, но дорого и нужно согласовать с директором.",
            },
        }])

        pipeline = ConversationPipeline(repo)
        assert pipeline.discover_from_hub("demo") == {"records": 1}

        records = repo.list_conversation_records("demo")
        assert records[0]["status"] == "transcribed"
        assert records[0]["lead_id"] == "56"

        assert pipeline.analyze_transcribed("demo") == {"analyses": 1}
        analyses = repo.list_conversation_analyses("demo")
        assert analyses[0]["conversation_id"] == "lead_notes:104"
        assert analyses[0]["sentiment"] in {"neutral", "negative"}
        assert "price" in analyses[0]["objections"]
        assert "decision_maker" in analyses[0]["objections"]
        assert analyses[0]["next_step"] == "Выяснить ЛПР и договориться о контакте с ним."
    finally:
        conn.close()


def test_upsert_conversation_records_does_not_downgrade_transcribed_call(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        repo.upsert_conversation_records([
            ConversationRecord(
                account_key="demo",
                conversation_id="contact_notes:1",
                source_type="contact_notes",
                source_id="1",
                lead_id="42",
                contact_id="7",
                direction="incoming",
                kind="call",
                recording_url="https://records.example/call.mp3",
                transcript_text="Client asks for a Thursday measurement.",
                duration_seconds=60,
                occurred_at=1710000000,
                status="transcribed",
                metadata={},
            )
        ])

        repo.upsert_conversation_records([
            ConversationRecord(
                account_key="demo",
                conversation_id="contact_notes:1",
                source_type="contact_notes",
                source_id="1",
                lead_id="42",
                contact_id="7",
                direction="incoming",
                kind="call",
                recording_url="https://records.example/call.mp3",
                transcript_text=None,
                duration_seconds=60,
                occurred_at=1710000000,
                status="recording_found",
                metadata={},
            )
        ])

        record = repo.list_conversation_records("demo")[0]
        assert record["status"] == "transcribed"
        assert record["transcript_text"] == "Client asks for a Thursday measurement."
    finally:
        conn.close()


def test_build_lead_analysis_note_for_analyzed_call():
    record = {
        "conversation_id": "contact_notes:1",
        "lead_id": "42",
        "direction": "incoming",
        "duration_seconds": 60,
        "metadata": {"source": "TELPHIN"},
    }
    analysis = {
        "conversation_id": "contact_notes:1",
        "summary": "Клиент согласовал замер.",
        "sentiment": "positive",
        "score": 80,
        "next_step": "Поставить замер в график.",
        "objections": [],
        "recommendations": ["Проверить адрес и время визита."],
        "metrics": {},
        "analysis": {
            "qa_json": {
                "Рекомендации менеджеру": ["Подтвердить адрес и время замера."],
                "Рекомендации РОП": ["Проверить задачу на замер в CRM."],
            }
        },
    }

    selected_record, selected_analysis = find_record_and_analysis([record], [analysis], lead_id="42")
    note = build_lead_analysis_note(selected_record, selected_analysis)

    assert "AI-анализ звонка" in note
    assert "TELPHIN" in note
    assert "Клиент согласовал замер." in note
    assert "Рекомендации менеджеру" in note
    assert "Подтвердить адрес и время замера." in note
    assert "Рекомендации РОП" in note
    assert "Проверить задачу на замер в CRM." in note


def test_rule_based_analyzer_detects_scheduled_measurement():
    record = {
        "account_key": "demo",
        "conversation_id": "contact_notes:1",
        "lead_id": "42",
        "direction": "incoming",
        "kind": "call",
        "recording_url": "https://records.example/call.mp3",
        "duration_seconds": 172,
        "occurred_at": 1710000000,
    }
    transcript = (
        "Клиент уточнил сроки. Оператор сказала: записала вас на четверг, "
        "9 июля с 10 до 11. Мастер к вам будет подъезжать и наберет."
    )

    analysis = RuleBasedConversationAnalyzer().analyze(record, transcript)

    assert analysis.score >= 78
    assert analysis.sentiment == "positive"
    assert analysis.metrics["appointment_scheduled"] is True
    assert "Проконтролировать визит мастера" in analysis.next_step


def test_format_transcript_with_roles_preserves_existing_roles():
    transcript = "Оператор: Добрый день.\nКлиент: Хочу узнать цену."

    result = format_transcript_with_roles(transcript)

    assert result.splitlines() == [
        "Менеджер: Добрый день.",
        "Клиент: Хочу узнать цену.",
    ]


def test_qa_analyzer_stores_role_based_transcript():
    record = {
        "account_key": "demo",
        "conversation_id": "contact_notes:1",
        "lead_id": "42",
        "direction": "incoming",
        "kind": "call",
        "recording_url": "https://records.example/call.mp3",
        "duration_seconds": 80,
        "occurred_at": 1710000000,
    }
    transcript = "Здравствуйте, компания Потолки. Хочу узнать цену на потолок. Запишем вас на замер?"

    analysis = QASchemaConversationAnalyzer().analyze(record, transcript)
    qa_json = analysis.analysis["qa_json"]

    assert "Менеджер:" in qa_json["Транскрибация"]
    assert "Клиент:" in qa_json["Транскрибация"]
    assert "Очищенная расшифровка" not in qa_json["Транскрибация"]
    assert qa_json["Рекомендации менеджеру"]
    assert qa_json["Рекомендации РОП"]


def test_outgoing_transcript_starts_with_client_answer():
    transcript = (
        "Угу. А, вот, здравствуйте. Здравствуйте! "
        "Дом потолок натяжные, потолки оставляли заявку у нас на сайте Верна. "
        "Ну да, я хотела уточнить у вас. "
        "Сколько квадратов планируете установить примерно, знаете?"
    )

    result = format_transcript_with_roles(transcript, direction="outgoing")

    assert result.splitlines()[0] == "Клиент: Угу."
    assert "Менеджер: Дом потолок натяжные" in result
    assert "Менеджер: Сколько квадратов" in result


def test_repair_role_transcript_flips_obviously_inverted_dialog():
    transcript = "\n".join([
        "Менеджер: Угу.",
        "Клиент: А, вот, здравствуйте.",
        "Менеджер: Здравствуйте!",
        "Клиент: Дом потолок натяжные, потолки оставляли заявку у нас на сайте Верна.",
        "Менеджер: Ну да, я хотела уточнить у вас.",
        "Клиент: Сколько квадратов планируете установить примерно, знаете?",
    ])

    result = repair_role_transcript(transcript, direction="outgoing")

    assert result.splitlines()[0] == "Клиент: Угу."
    assert "Менеджер: Дом потолок натяжные" in result
    assert "Клиент: Ну да, я хотела уточнить у вас." in result


def test_conversation_settings_filters_records_by_lead_and_duration():
    settings = conversation_settings({
        "conversation_intelligence": {
            "filters": {
                "pipeline_ids": [10],
                "status_ids": [20],
                "responsible_user_ids": [30],
                "min_duration_seconds": 60,
            }
        }
    })
    record = {"duration_seconds": 90}
    lead = {"pipeline_id": 10, "status_id": 20, "responsible_user_id": 30}

    assert record_matches_filters(record, lead, settings["filters"]) is True
    assert record_matches_filters({**record, "duration_seconds": 30}, lead, settings["filters"]) is False
    assert record_matches_filters(record, {**lead, "status_id": 21}, settings["filters"]) is False


def test_conversation_export_writes_csv(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    repo = Repository(conn)
    try:
        repo.upsert_conversation_records([
            ConversationRecord(
                account_key="demo",
                conversation_id="contact_notes:1",
                source_type="contact_notes",
                source_id="1",
                lead_id="42",
                contact_id="7",
                direction="incoming",
                kind="call",
                recording_url="https://records.example/call.mp3",
                transcript_text="Client asks for a Thursday measurement.",
                duration_seconds=60,
                occurred_at=1710000000,
                status="transcribed",
                metadata={"source": "TELPHIN"},
            )
        ])
        ConversationPipeline(repo).analyze_transcribed("demo", force=True)
        path = tmp_path / "conversation_analysis.csv"
        result = ConversationExportService(repo).export_csv("demo", path)

        assert result["rows"] == 1
        assert path.exists()
        assert "contact_notes:1" in path.read_text(encoding="utf-8-sig")
    finally:
        conn.close()
