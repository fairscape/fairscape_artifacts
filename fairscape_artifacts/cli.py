"""`fairscape-artifacts` — generate RO-Crate artifacts from the command line.

    fairscape-artifacts datasheet       <crate>
    fairscape-artifacts evidence-graph  <crate>
    fairscape-artifacts review          <crate>
    fairscape-artifacts add-io          <crate>
    fairscape-artifacts link-inverses   <crate>
    fairscape-artifacts interpret       <crate>
    fairscape-artifacts all             <crate>

`<crate>` is an RO-Crate directory or its `ro-crate-metadata.json`. Outputs
land beside the crate unless `-o` says otherwise. `add-io` and `link-inverses`
are the only commands that write back into the crate; everything else only
ever adds files.

`all` is the offline set: graph, review, datasheet and previews. `interpret`
calls an LLM and is run on its own; the datasheet links to its output when
one is already beside the crate.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List, Optional

from fairscape_artifacts import composition as composition_mod
from fairscape_artifacts import datasheet as datasheet_mod
from fairscape_artifacts import evidence as evidence_mod
from fairscape_artifacts import outputs as outputs_mod
from fairscape_artifacts import grading
from fairscape_artifacts import interpretation as interpretation_mod
from fairscape_artifacts import inverses as inverses_mod
from fairscape_artifacts import preview as preview_mod
from fairscape_artifacts import render
from fairscape_artifacts.crate import METADATA_FILENAME, Crate

DATASHEET_HTML = "ro-crate-datasheet.html"
PREVIEW_HTML = composition_mod.PREVIEW_HTML
GRAPH_HTML = "ro-crate-evidence-graph.html"
GRAPH_JSON = "ro-crate-evidence-graph.json"
GRAPH_DOMAIN_HTML = "ro-crate-evidence-graph-domain.html"
GRAPH_DOMAIN_JSON = "ro-crate-evidence-graph-domain.json"
REVIEW_JSON = "ai-ready-presentation.json"
REVIEW_HTML = "ai-ready-review.html"
INTERPRETATION_JSON = interpretation_mod.INTERPRETATION_JSON
INTERPRETATION_HTML = interpretation_mod.INTERPRETATION_HTML


def _resolve(path: str) -> str:
    if os.path.isdir(path):
        return os.path.join(path, METADATA_FILENAME)
    return path


def _out_dir(crate_path: str, override: Optional[str]) -> str:
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    return os.path.dirname(os.path.abspath(crate_path))


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _write_json(path: str, data: Any) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return path


def _progress(quiet: bool):
    if quiet:
        return lambda message: None
    return lambda message: print(message, file=sys.stderr)


def _load_review(crate_dir: str, out_dir: str, args) -> Optional[Dict[str, Any]]:
    """The review to show on a datasheet: a fresh one, or one already on disk.

    Running the grader walks the whole crate, so an existing presentation is
    reused unless `--rerun-review` says otherwise.
    """
    existing = os.path.join(out_dir, REVIEW_JSON)
    if not args.rerun_review and os.path.exists(existing):
        with open(existing, encoding="utf-8") as handle:
            return json.load(handle)
    try:
        return grading.review(crate_dir, network=args.network,
                              progress=_progress(args.quiet))
    except grading.GraderUnavailable as err:
        print(f"warning: {err}; datasheet will omit the AI-Ready section",
              file=sys.stderr)
        return None


def _links(out_dir: str) -> Dict[str, str]:
    """Companion pages the datasheet may link to, if they exist beside it."""
    return {key: name if os.path.exists(os.path.join(out_dir, name)) else ""
            for key, name in (("evidence_graph", GRAPH_HTML), ("graph_json", GRAPH_JSON),
                              ("review_html", REVIEW_HTML), ("review_json", REVIEW_JSON),
                              ("interpretation_html", INTERPRETATION_HTML),
                              ("interpretation_json", INTERPRETATION_JSON))}


def _rel(target: str, start_dir: str) -> str:
    return os.path.relpath(target, start_dir).replace(os.sep, "/")


def _write_previews(comp, out_dir: str, path: str, link_base: str) -> List[str]:
    """One `ro-crate-preview.html` per composition card, beside its crate."""
    written = []
    for item in comp.items:
        if not item.preview_path:
            continue
        preview_dir = os.path.dirname(os.path.abspath(item.preview_path))
        evidence_href = item.context["evidence_href"]
        if evidence_href and not evidence_href.startswith(("http://", "https://")):
            evidence_href = _rel(os.path.join(out_dir, evidence_href), preview_dir)
        context = preview_mod.build_context(
            item.crate, comp.index, comp.owner, link_base=link_base,
            datasheet_href=_rel(os.path.join(out_dir, DATASHEET_HTML), preview_dir),
            evidence_href=evidence_href,
            source=os.path.basename(item.crate.path or path), generated_at=_stamp())
        written.append(render.write(item.preview_path, render.preview_html(context)))
    return written


# -- commands ---------------------------------------------------------------

def cmd_datasheet(args) -> List[str]:
    path = _resolve(args.crate)
    crate = Crate.load(path)
    out_dir = _out_dir(path, args.output_dir)
    review = None if args.no_review else _load_review(
        os.path.dirname(os.path.abspath(path)), out_dir, args)
    comp = composition_mod.build(crate, out_dir=out_dir, link_base=args.link_base,
                                 previews=not args.no_previews)
    context = datasheet_mod.build_context(crate, composition=comp, review=review,
                                          generated_at=_stamp(), links=_links(out_dir),
                                          link_base=args.link_base)
    target = args.output or os.path.join(out_dir, DATASHEET_HTML)
    written = [render.write(target, render.datasheet_html(context))]
    written += _write_previews(comp, out_dir, path, args.link_base)
    return written


def cmd_evidence_graph(args) -> List[str]:
    path = _resolve(args.crate)
    crate = Crate.load(path)
    out_dir = _out_dir(path, args.output_dir)
    threshold = args.condense_threshold
    pool = list(getattr(args, "reference", []) or [])
    if args.domain:
        # Condensation defaults off for the domain layer: its whole point is
        # seeing every node, so only an explicit --condense-threshold groups.
        graph = evidence_mod.build_domain(crate, node_id=args.node,
                                          condense_threshold=threshold, pool=pool)
    else:
        graph = evidence_mod.build(crate, node_id=args.node,
                                   condense_threshold=5 if threshold is None
                                   else threshold, pool=pool)

    html_name, json_name = ((GRAPH_DOMAIN_HTML, GRAPH_DOMAIN_JSON) if args.domain
                            else (GRAPH_HTML, GRAPH_JSON))
    written = []
    target = args.output or os.path.join(out_dir, html_name)
    written.append(render.write(target, render.evidence_graph_html(
        graph, source=os.path.basename(path), generated_at=_stamp())))
    if not args.no_json:
        written.append(_write_json(os.path.join(out_dir, json_name), graph))
    return written


def cmd_review(args) -> List[str]:
    path = _resolve(args.crate)
    crate_dir = os.path.dirname(os.path.abspath(path))
    out_dir = _out_dir(path, args.output_dir)

    presentation = grading.review(crate_dir, network=args.network,
                                  progress=_progress(args.quiet))
    written = [_write_json(args.output or os.path.join(out_dir, REVIEW_JSON),
                           presentation)]
    if not args.no_html:
        link_base = os.path.relpath(crate_dir, out_dir).replace(os.sep, "/")
        written.append(render.write(
            os.path.join(out_dir, REVIEW_HTML),
            grading.review_html(presentation, link_base=link_base)))

    summary = grading.summarize(presentation)
    print(f"{summary['rubric']}")
    print(f"  {summary['estimated']}/{summary['criteria_total']} criteria estimated "
          f"({summary['tally']['Human review']} await human review)")
    for section in summary["sections"]:
        marks = " ".join(str(c["score"]) if c["score"] is not None else "-"
                         for c in section["criteria"])
        gate = " (gating)" if section["gating"] else ""
        print(f"  {section['number']}. {section['title']:26} {marks}{gate}")
    return written


def cmd_interpret(args) -> List[str]:
    """LLM interpretation: the annotated evidence graph JSON and its page.

    Deliberately not part of `all`. It needs an API key, costs one call per
    computation plus a few for synthesis, and can take minutes on a large
    crate. `--render-only` rebuilds the page from an interpretation already
    on disk without calling the model.
    """
    path = _resolve(args.crate)
    crate = Crate.load(path)
    out_dir = _out_dir(path, args.output_dir)
    json_path = args.output or os.path.join(out_dir, INTERPRETATION_JSON)
    written: List[str] = []

    if args.agentic:
        done = _interpret_agentic(args, crate, path, out_dir, json_path)
        if done is not None:
            return done
        aeg = interpretation_mod.load(json_path)
        written.append(json_path)
    elif args.render_only:
        if not os.path.exists(json_path):
            raise SystemExit(f"error: no interpretation at {json_path}")
        aeg = interpretation_mod.load(json_path)
    else:
        try:
            interpretation_mod.configure_api_key(args.model, args.api_key)
        except ValueError as err:
            raise SystemExit(f"error: {err}")
        references = [Crate.load(_resolve(ref)) for ref in args.reference]
        trace = json_path + ".llm-trace.jsonl" if args.debug_llm else None
        try:
            aeg = interpretation_mod.run(
                crate, output_path=json_path, references=references,
                model=args.model, temperature=args.temperature,
                max_workers=args.max_workers, condensed_path=args.save_condensed,
                trace_path=trace, progress=_progress(args.quiet))
        except interpretation_mod.InterpreterUnavailable as err:
            raise SystemExit(f"error: {err}")
        written.append(json_path)
        if args.save_condensed:
            written.append(args.save_condensed)
        if trace:
            written.append(trace)

    if not args.no_html:
        context = interpretation_mod.summarize(aeg, link_base=args.link_base)
        html_path = os.path.join(os.path.dirname(os.path.abspath(json_path)),
                                 INTERPRETATION_HTML)
        written.append(render.write(html_path, render.interpretation_html({
            **context, "source": os.path.basename(path), "generated_at": _stamp()})))
    return written


def _interpret_agentic(args, crate, path, out_dir, json_path) -> Optional[List[str]]:
    """One stage of `interpret --agentic`. Returns the files written for
    `prepare` and `steps`; `None` after `assemble` so the caller renders."""
    try:
        from fairscape_artifacts.interpret import agentic
    except ImportError as err:  # pragma: no cover - depends on the environment
        raise SystemExit(f"error: {err}; install 'fairscape-artifacts[interpret]'")
    work = args.work_dir or os.path.join(out_dir, "interpretation-work")
    try:
        if args.agentic == "prepare":
            references = [Crate.load(_resolve(ref)) for ref in args.reference]
            steps = agentic.prepare(crate, work, references=references,
                                    output_path=json_path, model_label=args.model_label)
            return [os.path.join(d, agentic.PACKET) for d in steps]
        if args.agentic == "steps":
            return [agentic.collect_steps(work)]
        agentic.assemble(work)
    except agentic.AgenticError as err:
        raise SystemExit(f"error: {err}")
    return None


def cmd_add_io(args) -> List[str]:
    path = _resolve(args.crate)
    ok, message = outputs_mod.write(path)
    print(message)
    if not ok:
        raise SystemExit(1)
    return [path]


def cmd_link_inverses(args) -> List[str]:
    path = _resolve(args.crate)
    ok, message = inverses_mod.write(path)
    print(message)
    if not ok:
        raise SystemExit(1)
    return [path]


def cmd_all(args) -> List[str]:
    """Graph, datasheet (with previews), review, datasheet again.

    The grader inspects the crate directory for artifacts, and criterion 3.a
    (data documentation template) looks for a datasheet. Writing the datasheet
    before the review runs means a fresh crate is not marked down for a
    datasheet this very command is about to produce. It is then re-rendered so
    it carries the review it just earned — the page is small and rendering is
    cheap, which is the price of getting both right in a single pass.
    """
    path = _resolve(args.crate)
    crate = Crate.load(path)
    crate_dir = os.path.dirname(os.path.abspath(path))
    out_dir = _out_dir(path, args.output_dir)
    written = []

    graph = evidence_mod.build(crate, condense_threshold=args.condense_threshold,
                               pool=list(getattr(args, "reference", []) or []))
    written.append(render.write(
        os.path.join(out_dir, GRAPH_HTML),
        render.evidence_graph_html(graph, source=os.path.basename(path),
                                   generated_at=_stamp())))
    written.append(_write_json(os.path.join(out_dir, GRAPH_JSON), graph))

    comp = composition_mod.build(crate, out_dir=out_dir, link_base=args.link_base,
                                 previews=not args.no_previews)

    def render_datasheet(review):
        context = datasheet_mod.build_context(crate, composition=comp, review=review,
                                              generated_at=_stamp(), links=_links(out_dir),
                                              link_base=args.link_base)
        return render.write(os.path.join(out_dir, DATASHEET_HTML),
                            render.datasheet_html(context))

    datasheet_path = render_datasheet(None)
    written += _write_previews(comp, out_dir, path, args.link_base)

    presentation = None
    if not args.no_review:
        try:
            presentation = grading.review(crate_dir, network=args.network,
                                          progress=_progress(args.quiet))
            written.append(_write_json(os.path.join(out_dir, REVIEW_JSON), presentation))
            link_base = os.path.relpath(crate_dir, out_dir).replace(os.sep, "/")
            written.append(render.write(
                os.path.join(out_dir, REVIEW_HTML),
                grading.review_html(presentation, link_base=link_base)))
        except grading.GraderUnavailable as err:
            print(f"warning: {err}; skipping the AI-Ready review", file=sys.stderr)

    if presentation:
        render_datasheet(presentation)
    written.append(datasheet_path)
    return written


# -- argument parsing -------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fairscape-artifacts",
        description="Generate datasheets, evidence graphs and AI-Ready scores "
                    "from a local RO-Crate.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text):
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("crate", help="RO-Crate directory or ro-crate-metadata.json")
        sub.add_argument("-o", "--output", help="explicit output file path")
        sub.add_argument("-d", "--output-dir",
                         help="directory for outputs (default: beside the crate)")
        sub.set_defaults(handler=handler)
        return sub

    sheet = add("datasheet", cmd_datasheet,
                "Render the HTML datasheet and a preview page per crate.")
    sheet.add_argument("--no-review", action="store_true",
                       help="omit the AI-Ready section")
    sheet.add_argument("--rerun-review", action="store_true",
                       help="re-run the grader even if a presentation exists")
    _datasheet_options(sheet)
    _review_options(sheet)

    graph = add("evidence-graph", cmd_evidence_graph,
                "Render the interactive evidence-graph page.")
    graph.add_argument("--node", help="root the graph at this @id "
                                      "(default: the crate itself)")
    graph.add_argument("--condense-threshold", type=int, default=None,
                       help="collapse sibling datasets above this fan-in "
                            "(default: 5, and off with --domain)")
    graph.add_argument("--domain", action="store_true",
                       help="domain-layer graph for CPM-style crates: backbone "
                            "connectors are replaced by their "
                            "prov:specializationOf domain entities. Writes "
                            f"{GRAPH_DOMAIN_HTML} / {GRAPH_DOMAIN_JSON}")
    graph.add_argument("--reference", action="append", default=[], metavar="CRATE",
                       help="extra crate to resolve ids against (repeatable); "
                            "constituents and linked crates are always included. "
                            "Use it for a sibling that describes entities this "
                            "crate only refers to")
    graph.add_argument("--no-json", action="store_true",
                       help="skip the sidecar graph JSON")

    review = add("review", cmd_review,
                 "Run the AI-Ready grader and write its evidence presentation.")
    review.add_argument("--no-html", action="store_true",
                        help="skip the grader's human-review page")
    _review_options(review)

    add("add-io", cmd_add_io,
        "Write EVI:inputs / EVI:outputs onto the crate root (modifies the crate).")

    add("link-inverses", cmd_link_inverses,
        "Complete the inverse EVI links (generated for generatedBy, and so on) "
        "on every entity (modifies the crate).")

    interp = add("interpret", cmd_interpret,
                 "Interpret the crate with an LLM: annotated evidence graph "
                 "JSON and page. Needs the 'interpret' extra and an API key.")
    interp.add_argument("--reference", action="append", default=[], metavar="CRATE",
                        help="extra crate to resolve ids against (repeatable); "
                             "constituents and linked crates are always included")
    interp.add_argument("--model", default=interpretation_mod.DEFAULT_MODEL,
                        help="pydantic-ai model string, provider:name "
                             f"(default: {interpretation_mod.DEFAULT_MODEL})")
    interp.add_argument("--api-key", help="provider API key; otherwise read from "
                                          "the provider's environment variable")
    interp.add_argument("--temperature", type=float, default=0.0)
    interp.add_argument("--max-workers", type=int, default=2,
                        help="parallel annotation calls (default: 2)")
    interp.add_argument("--save-condensed", metavar="PATH",
                        help="also write the condensed crate the engine built")
    interp.add_argument("--debug-llm", action="store_true",
                        help=f"append every raw LLM response to "
                             f"{INTERPRETATION_JSON}.llm-trace.jsonl")
    interp.add_argument("--render-only", action="store_true",
                        help="rebuild the page from the existing JSON, no LLM")
    interp.add_argument("--no-html", action="store_true",
                        help="write the JSON only")
    interp.add_argument("--link-base", default="",
                        help="server URL to link identifiers to (default: plain text)")
    interp.add_argument("-q", "--quiet", action="store_true",
                        help="suppress progress output")
    interp.add_argument("--agentic", choices=("prepare", "steps", "assemble"),
                        help="run without an API key, with the calling agent "
                             "writing the model answers: 'prepare' writes one "
                             "packet per computation, 'steps' checks the answers "
                             "and writes the synthesis packet, 'assemble' builds "
                             "the JSON and page")
    interp.add_argument("--work-dir", help="agentic work directory (default: "
                                           "interpretation-work beside the outputs)")
    interp.add_argument("--model-label", default="agentic:claude-code",
                        help="what the interpretation records as its model "
                             "(agentic runs only)")

    every = add("all", cmd_all, "Graph, review, datasheet and previews in one pass.")
    every.add_argument("--condense-threshold", type=int, default=5,
                       help="collapse sibling datasets above this fan-in "
                            "(default: 5)")
    every.add_argument("--no-review", action="store_true",
                       help="skip the AI-Ready review")
    every.add_argument("--reference", action="append", default=[], metavar="CRATE",
                       help="extra crate to resolve ids against (repeatable); "
                            "constituents and linked crates are always included")
    _datasheet_options(every)
    _review_options(every)
    return parser


def _datasheet_options(sub) -> None:
    """Options shared by every command that renders the datasheet."""
    sub.add_argument("--no-previews", action="store_true",
                     help="skip the per-crate ro-crate-preview.html pages")
    sub.add_argument("--link-base", default="",
                     help="server URL to link identifiers to, e.g. "
                          "https://fairscape.net (default: plain text)")


def _review_options(sub) -> None:
    """Options shared by every command that may run the grader."""
    sub.add_argument("--network", action="store_true",
                     help="let the grader resolve URLs and registries "
                          "(default: offline)")
    sub.add_argument("-q", "--quiet", action="store_true",
                     help="suppress the grader's per-criterion progress")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    crate_path = _resolve(args.crate)
    if not os.path.exists(crate_path):
        print(f"error: no RO-Crate metadata at {crate_path}", file=sys.stderr)
        return 1

    written = args.handler(args)
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
