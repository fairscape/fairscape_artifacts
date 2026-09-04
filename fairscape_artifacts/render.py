"""Render contexts into single-file HTML artifacts.

Every output is self-contained: CSS and the viewer bundle are inlined, so a
generated page works from a file:// URL, a Zenodo deposit or a GitHub Pages
directory with nothing alongside it.

Autoescaping is on. Crate-supplied text — names, descriptions, keywords —
is untrusted and gets escaped; only the three checked-in assets and the
graph JSON are marked safe, and the JSON goes through `json_for_script`
first so it cannot close the `<script>` element it sits in.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "templates")
STATIC_DIR = os.path.join(HERE, "static")


@lru_cache(maxsize=None)
def asset(name: str) -> str:
    """Read a checked-in static asset (see `static/PROVENANCE.md`)."""
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def json_for_script(data: Any) -> str:
    """Serialize JSON safe to inline inside a `<script>` element.

    `</script>` anywhere in the data — a description quoting HTML, say — would
    otherwise end the element early and spill the rest of the graph into the
    document as markup.
    """
    text = json.dumps(data, ensure_ascii=False)
    return (text.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
                .replace(" ", "\\u2028")
                .replace(" ", "\\u2029"))


@lru_cache(maxsize=1)
def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default_for_string=True, default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=False,
    )
    return env


def _render(template_name: str, context: Dict[str, Any]) -> str:
    return _environment().get_template(template_name).render(**context)


def datasheet_html(context: Dict[str, Any]) -> str:
    """Render the datasheet page from a `datasheet.build_context` dict."""
    return _render("datasheet.html.j2", {
        **context,
        "style_css": Markup(asset("style.css") + "\n" + asset("datasheet.css")),
    })


def preview_html(context: Dict[str, Any]) -> str:
    """Render a crate's preview page from a `preview.build_context` dict."""
    return _render("preview.html.j2", {
        **context,
        "style_css": Markup(asset("style.css") + "\n" + asset("datasheet.css")),
    })


def evidence_graph_html(graph: Dict[str, Any], *, title: Optional[str] = None,
                        kicker: str = "Evidence Graph",
                        source: str = "", generated_at: str = "") -> str:
    """Render the interactive evidence-graph page around a graph document.

    The viewer self-mounts: it looks for `#evidence-graph` and reads
    `window.__EVIDENCE_GRAPH__`, so the template only has to provide both.
    """
    return _render("evidence_graph.html.j2", {
        "kicker": kicker,
        "title": title or graph.get("name") or "Evidence Graph",
        "ark": graph.get("@id", ""),
        "description": "",
        "chips": [],
        "source": source,
        "generated_at": generated_at,
        "style_css": Markup(asset("style.css")),
        "reactflow_css": Markup(asset("reactflow.css")),
        "viewer_js": Markup(asset("viewer.js")),
        "graph_json": Markup(json_for_script(graph)),
    })


def write(path: str, html: str) -> str:
    """Write a rendered page, creating parent directories as needed."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return path
