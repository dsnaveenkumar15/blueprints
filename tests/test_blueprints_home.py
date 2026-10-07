# tests/test_blueprints_home.py
from fastapi.testclient import TestClient

from tests.test_blueprints_site import build_release, settings  # noqa: F401
from web.app import create_app


def home(settings, query=""):
    return TestClient(create_app(settings), base_url="http://blueprints.localhost").get("/" + query).text


def test_home_is_a_gallery_with_the_filters_in_the_hero(settings):
    build_release(settings)
    html = home(settings)
    assert 'class="hero bp-hero"' in html and html.index('name="use_case"') < html.index('class="gallery"')
    assert html.count('class="bp-card"') == 4 and html.count('class="topo') >= 4
    assert 'href="/b/support-agent"' in html and "/mo" in html and "Passes linter" in html
    for href in ('href="/finder"', 'href="/patterns"', 'href="/custom"'):
        assert href in html


def test_filtered_gallery_shows_only_matches(settings):
    build_release(settings)
    html = home(settings, "?topology=workflow")
    assert html.count('class="bp-card"') == 1 and 'href="/b/docs-rag"' in html


def test_empty_site_is_still_a_product_page(settings):
    html = home(settings)
    assert "No blueprints published yet" in html and 'class="hero bp-hero"' in html and 'href="/finder"' in html


def test_detail_flow_is_a_canvas_of_node_cards(settings):
    build_release(settings)
    html = TestClient(create_app(settings), base_url="http://blueprints.localhost").get("/b/support-agent").text
    block = html[html.index('class="flow-canvas canvas"'):]
    block = block[:block.index("</ol>")]
    assert block.count('class="node"') >= 2 and " in / " in block


def test_every_topology_has_its_own_thumbnail():
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    from blueprints.models import TOPOLOGIES
    env = Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "web" / "templates")), autoescape=True)
    macro = env.get_template("blueprints_topology.html").module.topology
    drawings = {kind: str(macro(kind, kind)) for kind in TOPOLOGIES}
    bodies = {kind: svg[svg.index(">") + 1:] for kind, svg in drawings.items()}    # the drawing, not the labelled root
    assert len(set(bodies.values())) == len(TOPOLOGIES), "two topologies share one drawing"
    assert "Orchestrator" not in bodies["supervisor"] + bodies["evaluator-optimizer"] + bodies["autonomous"]


def test_filter_bar_may_wrap_rather_than_push_the_page_sideways():
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "blueprints" / "theme.css").read_text(encoding="utf-8")
    assert "nowrap" not in "".join(m for m in __import__("re").findall(r"\.filter-bar\s*\{([^}]*)\}", css))


def test_the_flow_canvas_scrolls_sideways_instead_of_wrapping_so_no_connector_dangles():
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "blueprints" / "theme.css").read_text(encoding="utf-8")
    rule = re.search(r"\.flow-canvas\s*\{([^}]*)\}", css).group(1)
    assert "flex-wrap: nowrap" in rule and "overflow-x: auto" in rule
    assert re.search(r"\.flow-canvas \.node\s*\{[^}]*flex:\s*none", css)


def test_the_architect_questions_are_compact():
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "blueprints" / "theme.css").read_text(encoding="utf-8")
    assert re.search(r"\.arch-questions fieldset p\s*\{[^}]*margin:\s*0", css)
