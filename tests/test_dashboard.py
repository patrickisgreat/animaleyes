from __future__ import annotations

from fastapi.testclient import TestClient

from animaleyes.config import Secrets
from animaleyes.dashboard import create_app
from tests.conftest import Harness


def client(h: Harness) -> TestClient:
    secrets = Secrets(
        kasa_stream_url="",
        kasa_email="",
        kasa_password="",
        anthropic_api_key="",
        petlibro_serial="",
        slack_webhook="",
        dash_token="s3cret",
        dash_public_url="http://dash",
    )
    app = create_app(h.machine, h.store, h.config, secrets, h.tmp_path / "frames")
    return TestClient(app)


def test_every_route_requires_the_token(h: Harness) -> None:
    c = client(h)
    for path in ("/", "/api/status", "/api/events", "/frame.jpg", "/events/1", "/api/config"):
        assert c.get(path).status_code == 401
        assert c.get(path + "?token=wrong").status_code == 401
    assert c.get("/?token=s3cret").status_code == 200


def test_status_and_frame(h: Harness) -> None:
    h.load_plates(1)
    h.tick(motion=False)
    c = client(h)
    s = c.get("/api/status?token=s3cret").json()
    assert s["state"] == "IDLE"
    assert s["loaded_plates"] == [1]
    assert s["dry_run"] is False
    assert c.get("/frame.jpg?token=s3cret").headers["content-type"] == "image/jpeg"


def test_setting_plates_and_config_reach_the_machine(h: Harness) -> None:
    c = client(h)
    h.tick(motion=False)
    assert h.state() == "DONE"
    r = c.post("/api/plates?token=s3cret", json={"loaded": [2, 3]})
    assert r.json()["plates"] == {"1": "empty", "2": "loaded", "3": "loaded"}
    h.tick(motion=False)
    assert h.state() == "IDLE"

    r = c.post(
        "/api/config?token=s3cret",
        json={"MIN_GAP_MIN": "45", "DRY_RUN": True, "ACTIVE_START": "20:30"},
    )
    assert r.status_code == 200
    h.tick(motion=False)
    assert h.machine.settings.MIN_GAP_MIN == 45
    assert h.machine.settings.DRY_RUN is True
    assert c.post("/api/config?token=s3cret", json={"ACTIVE_END": "nope"}).status_code == 400
    assert c.post("/api/config?token=s3cret", json={"BOGUS": 1}).status_code == 400


def test_feed_now_and_event_page(h: Harness) -> None:
    c = client(h)
    h.tick(motion=False)
    c.post("/api/feed-now?token=s3cret")
    h.tick(motion=False)
    assert h.state() == "FEEDING"
    events = c.get("/api/events?token=s3cret").json()
    open_event = next(e for e in events if e["kind"] == "open")
    page = c.get(f"/events/{open_event['id']}?token=s3cret")
    assert page.status_code == 200 and "manual feed" in page.text
    assert c.get(f"/frames/{open_event['frames'][0]}?token=s3cret").status_code == 200
    assert c.get("/frames/../config.toml?token=s3cret").status_code in (404, 400)
