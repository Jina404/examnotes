"""End-to-end API tests, all against the fake provider."""
from __future__ import annotations

from app import ratelimit
from app.config import MAX_QUESTION_COUNT
from tests.conftest import make_pdf

BODY = "Enzymes lower the activation energy of a reaction inside the cell. "


def upload(client, data: bytes, name: str = "notes.pdf", **fields):
    form = {
        "question_count": str(fields.get("question_count", 5)),
        "question_types": fields.get("question_types", "multiple_choice,short_answer"),
        "difficulty": fields.get("difficulty", "mixed"),
    }
    return client.post(
        "/api/documents",
        files={"file": (name, data, "application/pdf")},
        data=form,
    )


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["provider"] == "fake"
    assert body["limits"]["max_pages"] == 100


def test_index_page_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ExamNotes" in res.text


def test_upload_returns_notes_and_questions(client, sample_pdf):
    res = upload(client, sample_pdf)
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["document_id"]
    assert body["cached"] is False
    assert body["pages"] == 2
    assert body["options"] == {
        "question_count": 5,
        "question_types": ["multiple_choice", "short_answer"],
        "difficulty": "mixed",
    }

    result = body["result"]
    assert result["notes"] and result["notes"][0]["points"]
    for question in result["questions"]:
        assert question["question"] and question["answer"]
        assert 1 <= question["page"] <= body["pages"]
        assert question["type"] in {"multiple_choice", "short_answer", "long_answer"}
        if question["type"] == "multiple_choice":
            assert len(question["options"]) >= 2


def test_question_count_is_clamped(client, sample_pdf):
    body = upload(client, sample_pdf, question_count=999).json()
    assert body["options"]["question_count"] == MAX_QUESTION_COUNT
    assert len(body["result"]["questions"]) <= MAX_QUESTION_COUNT


def test_upload_rejects_a_non_pdf(client):
    res = upload(client, b"just some text, not a pdf at all", name="essay.txt")
    assert res.status_code == 400
    assert "not a PDF" in res.json()["error"]


def test_upload_rejects_a_scanned_pdf(client, scanned_pdf):
    res = upload(client, scanned_pdf)
    assert res.status_code == 422
    assert "scan" in res.json()["error"].lower()


def test_upload_without_a_file_explains_itself(client):
    res = client.post("/api/documents", data={"question_count": "5"})
    assert res.status_code == 400
    assert "file" in res.json()["error"]


def test_get_document_returns_the_saved_result(client, sample_pdf):
    created = upload(client, sample_pdf).json()
    res = client.get(f"/api/documents/{created['document_id']}")

    assert res.status_code == 200
    body = res.json()
    assert body["result"] == created["result"]
    assert body["filename"] == "notes.pdf"


def test_get_unknown_document_is_404(client):
    res = client.get("/api/documents/doesnotexist")
    assert res.status_code == 404
    assert "error" in res.json()


def test_second_upload_is_cached(client, sample_pdf):
    first = upload(client, sample_pdf).json()
    second = upload(client, sample_pdf).json()
    assert second["cached"] is True
    assert second["document_id"] == first["document_id"]


def test_chat_about_a_document(client, sample_pdf):
    doc_id = upload(client, sample_pdf).json()["document_id"]
    res = client.post(f"/api/documents/{doc_id}/chat", json={"message": "make the notes shorter"})

    assert res.status_code == 200
    body = res.json()
    assert body["document_id"] == doc_id
    assert isinstance(body["reply"], str) and body["reply"]


def test_chat_needs_a_message(client, sample_pdf):
    doc_id = upload(client, sample_pdf).json()["document_id"]
    res = client.post(f"/api/documents/{doc_id}/chat", json={"message": ""})
    assert res.status_code == 400


def test_chat_about_an_unknown_document_is_404(client):
    res = client.post("/api/documents/nope/chat", json={"message": "hello"})
    assert res.status_code == 404


def test_rate_limit_after_five_uploads(client):
    ratelimit.reset()
    # Different content each time so the cache never short-circuits the work.
    for i in range(5):
        res = upload(client, make_pdf([f"{BODY} Lecture number {i}."]), name=f"n{i}.pdf")
        assert res.status_code == 200, res.text

    blocked = upload(client, make_pdf([f"{BODY} Lecture number six."]), name="n6.pdf")
    assert blocked.status_code == 429
    assert "uploads for this hour" in blocked.json()["error"]
