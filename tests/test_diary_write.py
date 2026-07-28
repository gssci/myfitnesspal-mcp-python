"""Unit tests for webpage-backed diary entry listing and deletion."""

from datetime import date

from mfp_mcp import server


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, diary=None):
        self.diary = diary or []
        self.deleted_url = None
        self.get_request = None

    def get(self, url, **kwargs):
        if url.endswith("/api/auth/csrf"):
            return _Response(payload={"csrfToken": "token"})
        self.get_request = (url, kwargs)
        return _Response(payload=self.diary)

    def delete(self, url, **kwargs):
        self.deleted_url = url
        return _Response(status_code=204)


class _Client:
    def __init__(self, diary=None):
        self.session = _Session(diary)


def test_list_diary_entries_uses_web_service_and_uuid():
    client = _Client(
        [
            {
                "id": "1ad0f48e-11fd-4919-98fd-013140fd31ef",
                "type": "food_entry",
                "meal_name": "Snacks",
                "food": {"description": "Chicken breast"},
            }
        ]
    )

    entries = server.list_diary_entries(client, date(2026, 7, 28))

    assert entries == [
        {
            "entry_id": "1ad0f48e-11fd-4919-98fd-013140fd31ef",
            "name": "Chicken breast",
            "meal": "Snacks",
        }
    ]
    _, request = client.session.get_request
    assert request["params"] == {
        "entry_date": "2026-07-28",
        "types": "food_entry",
    }


def test_remove_food_entry_uses_web_service_uuid():
    client = _Client()
    entry_id = "1ad0f48e-11fd-4919-98fd-013140fd31ef"

    server.remove_food_entry(client, entry_id)

    assert client.session.deleted_url == (
        f"https://www.myfitnesspal.com/api/services/diary/{entry_id}"
    )
