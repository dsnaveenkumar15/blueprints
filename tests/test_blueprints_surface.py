import json

import pytest

from blueprints import living
from core.publish import publish


def put_surface(root, data):
    publish(root, "radar", "digests", {"surface.json": json.dumps(data).encode(), "index.json": b"{}"}, {})


def test_the_reader_loads_what_radar_published(tmp_path):
    put_surface(tmp_path, {"generated_at": 1, "packages": {"pypi:langgraph": {"version": "1.3.0", "symbols": ["langgraph.graph.StateGraph"]}}})
    loaded = living.load_radar_surface(tmp_path)
    assert loaded["pypi:langgraph"]["version"] == "1.3.0" and "langgraph.graph.StateGraph" in loaded["pypi:langgraph"]["symbols"]


@pytest.mark.parametrize("data", [None, [], {}, {"packages": None}, {"packages": {"pypi:x": 3}}, {"packages": {"pypi:x": {"version": 1}}},
                                  {"packages": {"pypi:x": {"version": "1", "symbols": "no"}}}])
def test_the_reader_ignores_odd_surface_data(data):
    assert living.surface_from(data) == {}


def test_a_missing_release_gives_an_empty_surface(tmp_path):
    assert living.load_radar_surface(tmp_path) == {}
