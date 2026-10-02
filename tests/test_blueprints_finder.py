from blueprints import finder
from blueprints.models import Profile
from tests.blueprint_fixtures import good


def entry(blueprint_id, **over):
    profile = {**good()["profile"], **over}
    return {"id": blueprint_id, "title": blueprint_id, "summary": "s", "topology": "router", "risk": profile["risk"],
            "profile": profile}


def test_an_identical_profile_is_the_nearest_with_distance_zero():
    index = [entry("far", risk="low", latency="batch", autonomy="suggest"), entry("same")]
    top = finder.nearest(good()["profile"], index)
    assert [t["id"] for t in top] == ["same", "far"] and top[0]["distance"] == 0.0
    assert "risk" in top[0]["same"] and "capabilities" in top[0]["same"]


def test_risk_and_autonomy_weigh_more_than_latency():
    index = [entry("risk-off", risk="low"), entry("latency-off", latency="batch")]
    assert [t["id"] for t in finder.nearest(good()["profile"], index)] == ["latency-off", "risk-off"]


def test_shared_capabilities_and_volume_pull_closer():
    index = [entry("no-caps", capabilities=[]), entry("caps", capabilities=["read", "write"], volume_per_day=6000),
             entry("big", capabilities=["read", "write"], volume_per_day=5_000_000)]
    assert [t["id"] for t in finder.nearest(good()["profile"], index)] == ["caps", "big", "no-caps"]


def test_ties_break_by_id_and_the_limit_applies():
    index = [entry("b"), entry("a"), entry("c"), entry("d")]
    assert [t["id"] for t in finder.nearest(good()["profile"], index, limit=3)] == ["a", "b", "c"]


def test_entries_without_a_usable_profile_are_skipped():
    index = [{"id": "x", "title": "x"}, {"id": "y", "profile": {"latency": "nope"}}, "junk", entry("ok")]
    assert [t["id"] for t in finder.nearest(good()["profile"], index)] == ["ok"]
    Profile.model_validate(good()["profile"])
