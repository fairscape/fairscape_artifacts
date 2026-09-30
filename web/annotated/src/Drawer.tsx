// The detail pop-up for a node. It opens on a click over the graph and
// reads in layers: what the node is, what it read and made, then its
// assumptions as titled rows, and the full analysis only behind a last
// toggle. Closing it leaves the graph as the click unfolded it.
import React, { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import styled from "styled-components";
import { GraphDataService } from "./vendor/AnnotatedGraph/GraphDataService";
import AssumptionChainModal from "./vendor/AnnotatedGraph/AssumptionChainModal";
import { viewHref } from "./vendor/links";
import { AnnotationData, RawGraphEntity } from "./vendor/types/graph";
import {
  IMPACT,
  KIND_COLORS,
  ShownAssumption,
  byImpact,
  codeAssumptions,
  firstSentences,
  humanName,
  refIds,
  statusOf,
  stepAssumptions,
  typeOf,
} from "./text";

const Panel = styled.div`
  box-sizing: border-box;
  width: 100%;
  background: #fff;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 13.5px;
  line-height: 1.5;
  color: #18242a;
  padding: 18px 26px 26px;
  @media (max-width: 600px) { padding: 14px 16px 20px; }

  h2 { margin: 2px 0 8px; font-size: 17px; line-height: 1.3; font-weight: 650; font-family: inherit; text-transform: none; letter-spacing: 0; color: #18242a; }
  h3 { margin: 18px 0 6px; font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: #84939a; }
  p { margin: 0 0 8px; }
  a { color: #005f73; }
  .kv { display: grid; grid-template-columns: 62px 1fr; gap: 6px 12px; font-size: 13px; margin: 8px 0 2px; align-items: baseline; }
  .kv .k { color: #84939a; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; padding-top: 3px; }
  .chips { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px; }
  .chip { display: inline-block; box-sizing: border-box; font: inherit; font-size: 12px; line-height: 20px; height: 22px; padding: 0 8px; margin: 0; white-space: nowrap; border: 1px solid #d5dde0; border-radius: 12px; background: #f7f9f9; color: #18242a; cursor: pointer; }
  .chip:hover { border-color: #005f73; color: #005f73; }
  .chip.static { cursor: default; }
  .chip.static:hover { border-color: #d5dde0; color: #18242a; }
  .chip.more { border-style: dashed; color: #51626b; }
  .md p { margin: 0 0 8px; }
  .md ul, .md ol { margin: 4px 0; padding-left: 20px; }
  .md code { background: #f1f3f5; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  .muted { color: #84939a; }
  .small { font-size: 12px; }
  .note { border-left: 3px solid #e2e8ea; padding: 2px 10px; margin: 6px 0; font-size: 12.5px; color: #51626b; }
`;

const Top = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  .kind { font-size: 10.5px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
  .close { margin-left: auto; border: 0; background: none; font-size: 20px; line-height: 1; color: #84939a; cursor: pointer; padding: 2px 4px; }
  .close:hover { color: #18242a; }
`;

const Pill = styled.span<{ $color: string; $tint: string }>`
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  color: ${(e) => e.$color};
  background: ${(e) => e.$tint};
`;

const Row = styled.details<{ $color: string; $tint: string }>`
  border: 1px solid #e2e8ea;
  border-left: 3px solid ${(e) => e.$color};
  border-radius: 2px;
  margin: 0 0 5px;
  background: #fff;
  > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 8px; padding: 6px 10px; font-size: 13px; }
  > summary::-webkit-details-marker { display: none; }
  > summary .name { flex: 1; font-weight: 500; }
  > summary .badge { font-size: 10px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: ${(e) => e.$color}; background: ${(e) => e.$tint}; padding: 1px 6px; border-radius: 2px; }
  > summary .flag { font-size: 10px; color: #b27300; border: 1px solid #b27300; border-radius: 2px; padding: 0 5px; }
  > .body { padding: 2px 10px 10px; font-size: 12.5px; color: #51626b; border-top: 1px solid #eef2f3; }
  > .body p { margin: 8px 0 0; }
  > .body b { color: #18242a; }
`;

const Fold = styled.details`
  margin-top: 18px;
  border-top: 1px solid #e2e8ea;
  > summary { list-style: none; cursor: pointer; padding: 10px 0; font-size: 13px; font-weight: 600; color: #005f73; }
  > summary::-webkit-details-marker { display: none; }
  > summary::before { content: "\\25B8"; margin-right: 6px; color: #84939a; font-size: 11px; }
  &[open] > summary::before { content: "\\25BE"; }
  .card { border: 1px solid #e2e8ea; background: #f7f9f9; border-radius: 2px; padding: 8px 12px; margin: 6px 0; }
  .card .sw { font-weight: 600; }
`;

const ErrorBox = styled.div`
  border: 1px solid #d00000;
  border-left-width: 3px;
  background: #fdecea;
  padding: 6px 10px;
  margin: 0 0 6px;
  font-size: 12.5px;
  .sev { font-size: 10px; font-weight: 700; text-transform: uppercase; color: #d00000; }
`;

function Evidence({ evidence }: { evidence?: ShownAssumption["evidence"] }) {
  const id = evidence?.artifact?.["@id"];
  if (!id && !evidence?.location) return null;
  const href = viewHref(id);
  return (
    <p className="small muted">
      Evidence: {href ? <a href={href} target="_blank" rel="noopener noreferrer">{id}</a> : id}
      {evidence?.location ? ` · ${evidence.location}` : ""}
    </p>
  );
}

function AssumptionRow({ a }: { a: ShownAssumption }) {
  const im = IMPACT[a.impact] || IMPACT.MINOR;
  const title = a.name || (a.description.length > 90 ? a.description.slice(0, 90) + "…" : a.description);
  return (
    <Row $color={im.color} $tint={im.tint}>
      <summary>
        <span className="badge">{im.label}</span>
        <span className="name">{title}</span>
        {a.reviewRecommended && <span className="flag">review</span>}
      </summary>
      <div className="body">
        {a.name && <p>{a.description}</p>}
        {a.downstreamImpacts && <p><b>If wrong:</b> {a.downstreamImpacts}</p>}
        {a.recommendedValidation && <p><b>How to check:</b> {a.recommendedValidation}</p>}
        <Evidence evidence={a.evidence} />
      </div>
    </Row>
  );
}

const CHIP_CAP = 6;

function Chips({ ids, names, onPick }: { ids: string[]; names: (id: string) => string; onPick?: (id: string) => void }) {
  const [all, setAll] = useState(false);
  if (!ids.length) return <span className="muted">none recorded</span>;
  const shown = all || ids.length <= CHIP_CAP + 1 ? ids : ids.slice(0, CHIP_CAP);
  return (
    <span className="chips">
      {shown.map((id) =>
        onPick ? (
          <button key={id} type="button" className="chip" onClick={() => onPick(id)} title={id}>{names(id)}</button>
        ) : (
          <span key={id} className="chip static" title={id}>{names(id)}</span>
        ),
      )}
      {shown.length < ids.length && (
        <button type="button" className="chip more" onClick={() => setAll(true)}>+{ids.length - shown.length} more</button>
      )}
    </span>
  );
}

interface DrawerProps {
  nodeId: string;
  dataService: GraphDataService;
  onClose: () => void;
  onPick: (id: string) => void;
}

const Drawer: React.FC<DrawerProps> = ({ nodeId, dataService, onClose, onPick }) => {
  const node = dataService.getNode(nodeId);
  const [chain, setChain] = useState(false);
  const isCollection = nodeId.endsWith("-dataset-collection");
  // Step names read as words; file and software names stay as written.
  const names = (id: string) => {
    const n = dataService.getNode(id);
    if (!n) return id;
    const raw = n.name || n.label || id;
    return typeOf(n).includes("Computation") ? humanName(raw) : raw;
  };

  const flow = useMemo(() => {
    // Who made and who used this node, over the whole graph.
    const producers: string[] = [];
    const consumers: string[] = [];
    for (const other of dataService.getAllNodes()) {
      if (dataService.isAnnotation(other)) continue;
      if (refIds(other.generated).includes(nodeId)) producers.push(other["@id"]);
      if (refIds(other.usedDataset).includes(nodeId) || refIds(other.usedSoftware).includes(nodeId)) consumers.push(other["@id"]);
    }
    if (node) for (const p of refIds(node.generatedBy)) if (!producers.includes(p)) producers.push(p);
    return { producers, consumers };
  }, [dataService, nodeId, node]);

  if (isCollection) {
    return (
      <Panel>
        <Top><span className="kind" style={{ color: KIND_COLORS.files }}>Files</span><button className="close" onClick={onClose} aria-label="Close">&times;</button></Top>
        <h2>A group of files</h2>
        <p className="muted">This step read more files than fit on the drawing. Click the group in the graph to reveal them one at a time.</p>
      </Panel>
    );
  }
  if (!node) return null;

  const type = typeOf(node);
  const annotation = type.includes("Computation") ? dataService.getAnnotationFor(nodeId) : null;
  const title = names(nodeId);
  const href = viewHref(nodeId);

  if (annotation) return <StepPanel node={node} annotation={annotation} title={title} href={href} names={names} onClose={onClose} onPick={onPick} />;

  // A dataset, software or an unannotated computation.
  const isData = /Dataset|Sample/.test(type);
  const isSoftware = /Software|MLModel|Instrument/.test(type);
  const kindKey = isData ? "data" : isSoftware ? "software" : "step";
  const kindLabel = isData ? "Data" : isSoftware ? (type.includes("MLModel") ? "Model" : "Software") : "Step";
  const notes = isData ? datasetNotes(nodeId, dataService) : [];
  const facts: Array<[string, any]> = [];
  for (const key of ["version", "format", "fileFormat", "contentSize", "url", "contentUrl", "command"]) {
    if (node[key]) facts.push([key, node[key]]);
  }

  return (
    <Panel>
      <Top><span className="kind" style={{ color: KIND_COLORS[kindKey] }}>{kindLabel}</span><button className="close" onClick={onClose} aria-label="Close">&times;</button></Top>
      <h2>{title}</h2>
      {node.description && <p>{node.description}</p>}
      <div className="kv">
        {!!flow.producers.length && <><span className="k">Made by</span><Chips ids={flow.producers} names={names} onPick={onPick} /></>}
        {!!flow.consumers.length && <><span className="k">Used by</span><Chips ids={flow.consumers} names={names} onPick={onPick} /></>}
        {facts.map(([k, v]) => <React.Fragment key={k}><span className="k">{k}</span><span className="small" style={{ overflowWrap: "anywhere" }}>{String(typeof v === "object" ? JSON.stringify(v) : v)}</span></React.Fragment>)}
      </div>
      {notes.length > 0 && (
        <>
          <h3>What the analysis says about it</h3>
          {notes.map((n, i) => (
            <div className="note" key={i}>
              {n.role && <span className="muted small">{n.role} for {n.step} · </span>}
              {n.description}
              {n.quality && <div className="small muted">Quality: {n.quality}</div>}
            </div>
          ))}
        </>
      )}
      {isData && flow.producers.length > 0 && (
        <p style={{ marginTop: 16 }}>
          <button type="button" className="chip" onClick={() => setChain(true)}>Trace the assumptions behind this file</button>
        </p>
      )}
      {href && <p className="small"><a href={href} target="_blank" rel="noopener noreferrer">{nodeId}</a></p>}
      {chain && createPortal(
        <AssumptionChainModal datasetId={nodeId} datasetName={title} dataService={dataService} onClose={() => setChain(false)} />,
        document.body,
      )}
    </Panel>
  );
};

function datasetNotes(datasetId: string, dataService: GraphDataService) {
  const out: Array<{ step: string; role?: string; description?: string; quality?: string }> = [];
  for (const other of dataService.getAllNodes()) {
    if (!typeOf(other).includes("Computation")) continue;
    const ann = dataService.getAnnotationFor(other["@id"]);
    if (!ann) continue;
    for (const s of [...(ann["evi:inputSummaries"] || []), ...(ann["evi:outputSummaries"] || [])]) {
      if (s.dataset?.["@id"] !== datasetId) continue;
      if (!s.description && !s.dataQuality) continue;
      out.push({ step: humanName(other.name || other["@id"]), role: s.role, description: s.description, quality: s.dataQuality });
    }
  }
  return out;
}

interface StepPanelProps {
  node: RawGraphEntity;
  annotation: AnnotationData;
  title: string;
  href?: string;
  names: (id: string) => string;
  onClose: () => void;
  onPick: (id: string) => void;
}

const StepPanel: React.FC<StepPanelProps> = ({ node, annotation, title, href, names, onClose, onPick }) => {
  const status = statusOf(annotation);
  const summary = annotation["evi:stepSummary"] || "";
  const gist = firstSentences(summary, 2);
  const inputs = refIds(node.usedDataset);
  const outputs = refIds(node.generated);
  const software = refIds(node.usedSoftware);
  const assumptions = byImpact(stepAssumptions(annotation));
  const errors = annotation["evi:errors"] || [];
  const code = annotation["evi:codeAnalysis"] || [];
  const quality = [...(annotation["evi:inputSummaries"] || []), ...(annotation["evi:outputSummaries"] || [])].filter((d) => d.dataQuality);
  const number = (annotation as any)["_number"];

  return (
    <Panel>
      <Top>
        <span className="kind" style={{ color: KIND_COLORS.step }}>{number ? `Step ${number}` : "Step"}</span>
        <Pill $color={status.color} $tint={status.tint}>{status.label}</Pill>
        <button className="close" onClick={onClose} aria-label="Close">&times;</button>
      </Top>
      <h2>{title}</h2>
      {gist && <p>{gist}</p>}
      <div className="kv">
        <span className="k">Reads</span><Chips ids={inputs} names={names} onPick={onPick} />
        <span className="k">Makes</span><Chips ids={outputs} names={names} onPick={onPick} />
        {!!software.length && <><span className="k">Runs</span><Chips ids={software} names={names} onPick={onPick} /></>}
      </div>

      {errors.length > 0 && (
        <>
          <h3>Errors found</h3>
          {errors.map((e, i) => (
            <ErrorBox key={i}>
              <div className="sev">{e.severity}</div>
              {e.description}
              {e.affectedOutputs && <div className="small muted">Affects: {e.affectedOutputs}</div>}
              <Evidence evidence={e.evidence as any} />
            </ErrorBox>
          ))}
        </>
      )}

      {assumptions.length > 0 && (
        <>
          <h3>What this step assumes</h3>
          {assumptions.map((a, i) => <AssumptionRow key={i} a={a} />)}
        </>
      )}

      <Fold>
        <summary>Read the full analysis</summary>
        <div className="md"><ReactMarkdown>{summary}</ReactMarkdown></div>
        {code.map((c, i) => {
          const ca = byImpact(codeAssumptions(c));
          return (
            <div className="card" key={i}>
              <div className="sw">{c.name || c.software?.["@id"]}</div>
              <div className="md"><ReactMarkdown>{c.summary || ""}</ReactMarkdown></div>
              {!!c.keyFunctions?.length && <p className="small muted">Key functions: {c.keyFunctions.join(", ")}</p>}
              {ca.map((a, j) => <AssumptionRow key={j} a={a} />)}
            </div>
          );
        })}
        {quality.length > 0 && (
          <>
            <h3>Data quality</h3>
            {quality.map((d, i) => <div className="note" key={i}><b>{d.name || d.dataset?.["@id"]}</b> {d.dataQuality}</div>)}
          </>
        )}
        <p className="small muted" style={{ marginTop: 12 }}>
          Read by {annotation["evi:llmModel"]}{annotation.dateCreated ? ` on ${String(annotation.dateCreated).slice(0, 10)}` : ""}.
          {href && <> <a href={href} target="_blank" rel="noopener noreferrer">{node["@id"]}</a></>}
        </p>
      </Fold>
    </Panel>
  );
};

export default Drawer;
