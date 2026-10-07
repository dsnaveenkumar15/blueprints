# tests/test_blueprints_architect_admin.py
import json
import time

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from blueprints.architect import runs  # noqa: E402
from core.db import connect  # noqa: E402
from tests.test_blueprints_architect_render import result as finished  # noqa: E402
from tests.webutil import make_settings  # noqa: E402
from web.app import create_app  # noqa: E402

AUTH = ("owner", "correct-horse-battery-staple")


@pytest.fixture
def c(tmp_path):
    app = create_app(make_settings(tmp_path, admin_user="owner", admin_password=AUTH[1]))
    client = TestClient(app, base_url="http://blueprints.localhost")
    client.conn = connect(app.state.settings.db_path)
    runs.STORE.ensure_schema(client.conn)
    return client


def test_dashboard_shows_spend_switch_and_recent_runs(c):
    rid = runs.STORE.create_run(c.conn, {}, "new", "x", time.time())
    html = c.get("/admin", auth=AUTH).text
    assert "Architect" in html and rid[:8] in html and 'action="/admin/architect/toggle"' in html


def test_the_switch_turns_new_designs_off(c):
    assert c.post("/admin/architect/toggle", auth=AUTH, headers={"origin": "http://blueprints.localhost"}, follow_redirects=False).status_code == 303
    assert not runs.STORE.is_enabled(c.conn)


def test_the_brief_carries_the_profile_and_none_of_the_visitors_words(c):
    rid = runs.STORE.create_run(c.conn, {"description": "SECRET"}, "new", "x", time.time())
    runs.STORE.finish(c.conn, rid, "passed", finished(), None, time.time())
    text = c.get(f"/admin/architect/{rid}/brief", auth=AUTH).text
    brief = json.loads(text)
    assert set(brief) == {"id", "title", "use_case", "profile"} and brief["title"] == "Name this blueprint"
    assert "SECRET" not in text and c.get(f"/admin/architect/{rid}/brief").status_code == 401


def test_a_cross_site_switch_post_is_refused(c):
    assert c.post("/admin/architect/toggle", auth=AUTH, headers={"origin": "https://evil.example"}).status_code == 403
    assert runs.STORE.is_enabled(c.conn)
