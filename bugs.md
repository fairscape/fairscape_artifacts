# fairscape_artifacts — bug hunt (2026-10-12)

Scope: valid RO-Crate input → wrong, missing, or broken artifacts (crashes,
misrendered data, wrong/broken links). Nothing here has been fixed. Each entry
says whether it was **confirmed** by running the code on a small crate, or is
**suspected** (from reading the code only, not run).

Repro scripts used for confirmation live outside the repo; each entry has
enough of the input shape to rebuild it. Line numbers are as of `b2bad48`.

---

## A. Crashes on valid input

### A1. Root `name` that is a list (or any non-string) crashes the datasheet — confirmed
`composition.py:392`
`search = " ".join([root.get("name", ""), ...])` assumes `name` is a string.
`"name": ["Main", "Alt"]` (valid JSON-LD) →
`TypeError: sequence item 0: expected str instance, list found`, so
`datasheet` / `all` fail. A `{"@value": ..., "@language": ...}` name crashes the same way.
Related: `Crate.name` (`crate.py:169`) falls back to the root `@id` for any
non-string name, so even after the crash is fixed the title would read `ark:...`.

ME: name can only be a string so this is then not a valid crate and not a concern. INTENDED

### A2. `format` given as a list or object crashes evidence-graph condensation — confirmed
`condense.py:62,229`
`signature()` puts `dataset.get("format")` into the signature tuple, and the tuple
is used as a dict key. If a computation has more than 5 inputs (the default
threshold) and they have `"format": ["text/csv"]` or
`"format": {"@id": "http://edamontology.org/format_3752"}`, you get
`TypeError: cannot use 'tuple' as a dict key (unhashable type: 'list')`.
This breaks `evidence-graph` and `all`. `_group_node` would also build ids and
names from `str(list)` if it got that far.

ME: Again dataset (see fairscape_models) format is a string not a list so not a bug. INTENDED

### A3. String `evi:*` counters on the root crash the summary — confirmed
`datasheet.py:160-164`
`int(root.get("evi:totalEntities"))` / `int(root.get(counter))`. A value such as
`"1,234"` (or any non-integer string) raises `ValueError` and takes the whole
datasheet down. These fields aren't schema-typed, so a crate writer can
legitimately emit strings.

ME: Again evi:* is an int so not a valid crate if str all of A is invalid RO-Crates not bugs ignore. INTENDED

---

## B. Evidence graph: wrong or missing nodes and edges

### B1. A computation that uses a whole RO-Crate shows that crate's outputs as "not found" — confirmed
`evidence.py:105-112, 115-132, 338-352`
During projection, `_expand_used_dataset` swaps a `usedDataset` that points at a
nested ROCrate for that crate's `EVI:outputs`. The BFS never visited those
outputs, though: `_referenced_ids` doesn't expand ROCrate nodes, and ROCrate
isn't an entity or activity type. So the outputs aren't in the local `cache`, and
`_project` emits `{"@id": ..., "error": "not found"}` for each one, even when the
outputs are described in the same graph.
Repro: `comp.usedDataset -> inner (ROCrate, EVI:outputs -> innerout)`, with
`innerout` present in `@graph` → `innerout` is projected as `error: not found`.

ME: this is an important one to fix. People should be able to say a computation used a previous ro-crate and have it resolve to that ro-crates outputs. If I need a way to link to an ro-crate other tooling has ways of doing that. I think conversion has link crate for that purpose. HIGH

### B2. DatasetGroup id collision: groups overwrite each other and members disappear — confirmed
`condense.py:144-149, 231-261`
`group_id = f"ark:group/{label}-{slug}-{side}"` uses only the consuming
computation's name, the format and the side. Two groups collide when:
- one computation has two signature groups with the same format (same format,
  different producing software or schema), or
- two different computations share a `name` (common: every `cellranger count` run
  has the same name) and consume the same format.

In both cases the later group replaces the earlier one in `node_cache`.
Repro: `merge` uses 6 csv files made by software A and 6 made by software B →
the stats list two groups with the same id. The projected graph has one group
with `memberCount: 6`, so the other 6 inputs vanish from the graph entirely.

ME: definietly a bug not as pressing as the first one but worth fixing. MEDIUM

### B3. Condensation removes ancestors other parts of the graph still use, and rewires them to the group — confirmed
`condense.py:107-125, 244-247`, `evidence.py:265-284`
`_collect_exclusive` drops every ancestor of a non-representative member that the
*representative* doesn't depend on. It never checks whether something outside
the group still uses that ancestor. `collapsed_into` then redirects every
reference to it onto the group.
Repro: member `m6`'s producer used dataset `X`, and an unrelated computation
`c_other` (feeding another crate output) also used `X`. Result: `X` is removed
and `c_other.usedDataset` points at `ark:group/merge-csv-inputs`. That
provenance edge is false.

ME: Not entirely clear if 2 paths lead to the creation of a dataset one a large resprentative group and the other maybe just a single input that has it's own unique creation path we'd want both if that's not currently working we need to fix that. UNCLEAR could be HIGH if I understand correctly but could also be fine. Seems like HIGH in that I think we are dropping an edge that is useful

### B4. Only the first `generatedBy` is followed — confirmed (by design, but loses data)
`evidence.py:90-102`
A dataset with `generatedBy: [c1, c2]` shows only `c1`. Everything upstream of
`c2` is missing from the graph, and nothing tells the reader. The docstring calls
this the "established wire format". Still, a valid multi-producer crate is
presented incorrectly.

ME: We don't really expect to have a dataset generatedBy 2 computations that doesn't seem meaningful that is 2 computations back to back and we'd want it presented as such with an intermeidate and maybe embargoed dataset in the middle as placeholder. INTENDED

### B5. `usedDataset` and `prov:used` on the same activity: `prov:used` is dropped — confirmed
`crate.py:486-488` (`used_dataset_ids`)
It returns `usedDataset or prov:used`, not both. An activity that has both (for
example a partially converted crate) loses its `prov:used` inputs from the graph,
condensation, add-io and composition.

ME: This is fine not a bug we default to evi usedDataaset like terms when both are there. INTENDED 

### B6. Standard (non-EVI) RO-Crates produce an empty evidence graph — confirmed
`crate.py:385-389, 428-455`, `outputs.py:394`
A valid RO-Crate 1.1 or Workflow Run Crate uses `File`, `CreateAction`
(`object` / `result` / `instrument`), `SoftwareSourceCode` and
`ComputationalWorkflow`. None of these is in `EVI_TYPES` or the PROV fallback,
and `result` / `object` aren't walked. Result: the graph has the root node only
(`./`), add-io derives no outputs, and nothing explains why.
The graph id also comes out as `./-evidence-graph` (`graph_id_for`).

ME: Are stanadrd prov ro-crates working? This is not a bug if prov only ro-crates reproduce fine. I think for now INTENDED (if prov works) and possibly more a new feature request

### B7. A crate root with no metadata descriptor can resolve to a sub-crate stub — confirmed
`crate.py:154-161`
Without `ro-crate-metadata.json` → `about`, `_find_root` takes the *first* node
typed ROCrate, ahead of `./`. In a release crate whose stub for a constituent
crate comes before the root, the stub becomes the root. Every artifact is then
built for the wrong entity.

ME: That's user error at that point we don't really use ./ since these crates have @id with arks. This seems fine. INTENDED 

### B8. A sub-crate copy silently overrides a full entity in the parent — suspected
`crate.py:319-324`, `composition.py:72-77`
`provenance_index` and `build_index` let *any* entity in the closure overwrite
the parent's own copy, not only stubs. When several constituents describe the
same id (shared software, a shared input), the last one loaded wins. Which copy
gets shown, and which crate it's attributed to (`owner`), depends on load order.

ME: A choice we made perhaps a better assumption is available. LOW

### B9. Constituent graphs built on their own show siblings' ids as "not found" — confirmed on real data
Example: on `CM4AISeptUpdate/Perturb-Seq/cell-atlas`, `evidence-graph` gives
90 `error: not found` nodes. All of them are described in the sibling `sra`
crate. This is documented (`--reference`), but `all` on a constituent never
consults its parent release or its siblings, even though the crate has
`isPartOf` pointing at the release. Low priority / possibly by design.

ME: I am confused by this bug. What is the issue I'll need more details and examples seems low proirity but could be convinced otherwise. If this leads to missing nodes when a reference is provided HIGH.

---

## C. Datasheet, preview and composition: data shown incorrectly

### C1. Link targets are lost when an object has both `@id` and `name` — confirmed
`fields.py:20-32, 161-166`; used by `datasheet.badges/overview/distribution`,
`composition.card` and `preview.build_context`
`text()` prefers `name` over `@id`, and `http_url()` runs its values through
`text()`. So `license: {"@id": "https://spdx.org/licenses/MIT", "name": "MIT"}`
renders "MIT" with **no link**. The same applies to `publisher`,
`conditionsOfAccess` and `contentUrl` objects. `badges()` also passes the
already-flattened string to `http_url` (`datasheet.py:138`).

ME: MEDIUM ideally we'd get a name hyperlinked to the url

### C2. JSON-LD value objects render as empty — confirmed
`fields.py:28-29`
`text({"@value": "x", "@language": "en"})` returns `""`: only `name`, `@id` and
`value` are checked. Language-tagged names and descriptions disappear. The
preview falls back to the raw `@id` for the name, and the root's Description
row is dropped. (Root `name` in this form crashes instead; see A1.)

ME: Concern is fairscpe ro-crates not all generic ro-crates. LOW (make note new feature maybe to support generic ro-crates)

### C3. A non-DOI `identifier` is shown as "DOI" — confirmed
`datasheet.py:129-135, 279-285`, `preview.py:131-134`, `composition.py:366`
Whatever is in `identifier` is shown under "DOI", both as a badge and as a row.
FAIRSCAPE crates often put an ARK there, so the page shows a "DOI" badge reading
`ark:59852/...` with no link. A `PropertyValue` identifier with a `propertyID`
isn't checked either.

ME: LOW maybe update the html

### C4. Computation and experiment patterns, and computation outputs, depend on inverse links — confirmed
`composition.py:99-139`, `preview.py:56-57`
"Work patterns" on the composition card, and the Outputs column in the preview's
Computations tab, read only `generated` / `prov:generated` on the computation.
Most crates record only `generatedBy` on the dataset. That's the declared side,
and the `inverses.py` docstring itself says builders shouldn't rely on inverses.
So unless `link-inverses` was run, the card shows no patterns and every
computation lists no outputs.

ME: This is known but idk we can't force people to have run it so maybe add a warning if not inveresed? MEDIUM

### C5. Crate with `EVI:outputs` but no `EVI:inputs` shows no inputs — confirmed
`outputs.py:448-459`, `composition.py:172-173`
`ensure()` returns the stored `(inputs, outputs)` whenever outputs are stored,
even when inputs are empty. "Input datasets" on the card is then empty, though
the inputs could be derived.

ME: Seems like last one we'd want it populated if they run it and should flag somehow. MEDIUM

### C6. Standard RO-Crate data entities are counted as "Other" — confirmed
`fields.py:296-327`
`@type: "File"` (the RO-Crate data entity type) goes in the "other" bucket.
Full-IRI `https://schema.org/Dataset` / `http://schema.org/Dataset` also lands in
"other" (`evi_short_types` splits on `:` and yields `//schema.org/Dataset`),
while `schema:Dataset` counts as a dataset. Result on a plain RO-Crate: 0 files,
N "other", no formats or access tallies, and an empty "Datasets" tab.
`Annotation` activities also land in "other".

ME: LOW

### C7. Mebibyte sizes are parsed as decimal — confirmed
`fields.py:127-140`
`"1.5 KiB"` → `1500` bytes, because `KIB`→`KB` after stripping the `I`.
Binary units should use 1024. This only matters when sizes are summed
(`summary` totals) or reformatted.

ME: LOW

### C8. IRB given as a reference shows only its `@id` — confirmed
`datasheet.py:333-335`, `templates/datasheet.html.j2:150-160`
`irb: {"@id": "#irb"}` with an `Organization` node in the graph renders the
literal "#irb". The reference isn't resolved through the index, so name, contact
and address are lost. Same idea for `publisher` given as an `@id` only (C1).

ME: LOW

### C9. Release summary ignores entities on the release root itself — suspected
`datasheet.py:168-178`
Without `evi:*` counters, `summary()` counts only `comp.crates`, which are the
sub-crates when the crate is a release. Datasets described directly in the
release root's graph (not in a constituent) are left out of the totals and
formats.

ME: LOW

### C10. Owner and crate attribution is keyed by crate *name* — suspected
`composition.py:56-78, 123-125, 200-202`, `preview.py:38-39`
`owner[nid] = crate.name`, and "from another crate" is decided by
`source != here`. If two constituents share a name (or have no name and so fall
back to ids that collide), cross-crate inputs read as local, and vice versa.

ME: LOW

---

## D. Broken or wrong links

### D1. `ark_href` links every id, not only ARKs — confirmed
`fields.py:193-197`; used by `preview._item`, `datasheet`, `composition.card`
With `--link-base https://fairscape.net`, the preview links `data/a.csv` to
`https://fairscape.net/data/a.csv`, `https://example.org/x` to
`https://fairscape.net/https://example.org/x`, and the root `./` to
`https://fairscape.net/./`.

ME: Medium idk what we do here instead? But does seem worth fixing

### D2. A sub-crate pointer to a directory breaks all its links — confirmed
`crate.py:102-104, 196-215`
`Crate.load` accepts a directory, so `"ro-crate-metadata": "sub"` loads fine.
But `metadata_path` keeps `"sub"`, and `rel_dir = dirname("sub") = ""`. The
card's preview is then written to the **release's** own
`ro-crate-preview.html`, overwriting it or colliding with siblings, and
`metadata_path` / preview links point at the parent. (`"sub/"` happens to work.)

ME: I don't really get what is being said so can't make a judgement.

### D3. `localPath` fallback keeps the stale relative path — confirmed
`crate.py:209-215`
When the relative pointer doesn't resolve and the absolute `localPath` does, the
`SubCrate` is still created with `metadata_path=rel`, the path that didn't
resolve. `rel_dir` is wrong, so previews are written to and linked at a
directory that doesn't hold the crate.

ME: MEDIUM

### D4. `-d` / `-o`: several links are relative to the wrong directory — suspected
(Known `-d` relpath bug noted elsewhere; these are further cases.)
- `datasheet.linked_crate_items` (`datasheet.py:269-272`) builds links relative
  to the *crate* directory, not `out_dir`.
- The preview's QC-report link (`preview.py:149`) is relative to `crate.dir`,
  but the preview is written under `out_dir`.
- `datasheet -o other/path.html` writes the datasheet elsewhere, but
  `_links(out_dir)` and the previews' `datasheet_href` still assume
  `out_dir/ro-crate-datasheet.html` (`cli.py:126,146`).
- `evidence-graph -o` moves the HTML but still writes the JSON to `out_dir`.
- `all`, `add-io` and `link-inverses` accept `-o` and silently ignore it.

ME: NO idea what is being said so use your judgement seems LOW

### D5. `localEvidenceGraph` is resolved against the release directory — suspected
`composition.py:377-382`
A relative `.html` path on a sub-crate is joined with the release `crate_dir`,
not with the sub-crate's own directory (`sub_dir_abs`). The link is broken
whenever the path is written relative to the sub-crate.

ME: LOW

### D6. `file:///` absolute contentUrls are re-rooted under the crate — suspected
`crate.py:346-362`
`file:///home/u/data.csv` becomes `<crate>/home/u/data.csv` (documented as
intentional for this corpus, but wrong for genuinely absolute file URLs).
Percent-encoded paths (`my%20file.csv`) aren't decoded. Entities with no
`contentUrl` and a relative `@id` path (the standard RO-Crate convention) never
resolve. `../` paths can also point outside the crate (QC-report link).

ME: LOW

---

## E. Commands that modify the crate

### E1. `add-io` leaves stale `EVI:outputs` beside the new value — confirmed
`outputs.py:482-487`
`write()` always writes the full-IRI keys. If the root already has
`EVI:outputs` / `EVI:inputs` (or `outputs` / `inputs`), the old values stay, and
the root ends up with two conflicting output lists. `_rocrate_outputs` and
`stored()` prefer the IRI key, but any other consumer may read the stale one.
`add-io` also writes with `ensure_ascii=True` (unlike `link-inverses`), so every
non-ASCII character in the file gets escaped.

ME: HIGH want them overwritten not repeated

### E2. `link-inverses` ignores string references — confirmed
`inverses.py:197-200`
`"generatedBy": "ark:1/c"` adds no inverse link, but `crate.ref_ids`, and so the
evidence graph, treats bare strings as references. The two parts of the package
disagree on what counts as a link.

ME: Based on the models we want to assume they give an identifier value. So this seems more like the new feature support non-fairscape ro-crates

### E3. `link-inverses` mixes spellings and misreads PROV `used` — suspected
`inverses.py:219-244`
- `_has_link` checks only the same-spelling key. A dataset with
  `evi:generatedBy` whose computation already has bare `generated` gets a
  duplicate `evi:generated`.
- In a PROV-only crate whose context maps bare `used` to `prov:used`, the EVI
  pair `used`/`usedBy` adds an EVI `usedBy` to every entity.

### E4. add-io: datasets consumed only by Annotation/Experiment are listed as outputs — confirmed
`outputs.py:403-407`
Only Computation consumption counts as "used". A dataset consumed only by an
`EVI:Annotation` is reported as both an input and a leaf output. Derived-only
entities (`wasDerivedFrom`, no `generatedBy`) are also reported as inputs.

ME: LOW

---

## Notes

- The smoke run (39 real crates under `CM4AIReleases`, conversion examples and
  grader examples: evidence graph, composition, datasheet and preview render)
  crashed on none of them. Every crash above needs a valid but less common
  JSON-LD shape (list or `@value` name, list or object `format`, string counters).
- The `CM4AISeptUpdate-dataverse` release has 18 graph nodes that reference
  `https://fairscape.net/api/ark:...` ids never defined in any crate. That's a
  crate data issue, not an artifacts bug, but the graph shows them only as
  "not found", with no hint why.

---

## Plan (2026-10-13): what gets fixed, how, and answers to the open questions

The rating each item got from the review comments is in brackets. Everything
marked INTENDED is dropped, and so are the suspected-only items with no rating
(E3). Each fix ships with a regression test built from the repro above
(`tests/test_evidence_parity.py`, `test_linked_crates.py`, `test_outputs_parity.py`
and `test_fields.py` are where they belong).

### Dropped: intended behaviour or not FAIRSCAPE-valid input
A1, A2, A3 (not valid FAIRSCAPE crates), B4 (two producers should be modelled
as a chain), B5 (`usedDataset` wins over `prov:used` on purpose), B7 (FAIRSCAPE
crates use ARK roots with a descriptor), E3 (suspected only, no rating).
B6 moves to the feature section below. PROV-only crates already work: a crate
with only `prov:Entity` / `prov:Activity` / `prov:used` / `prov:wasGeneratedBy`
builds a full graph and add-io derives its outputs. That was checked by running it.

### Answers to the comments that asked a question

**B3, "is a dataset reachable two ways dropped?"** Yes, and an edge comes out
wrong, so this is HIGH. Reproduced: `merge` uses `m1`…`m6`, each made by a
`prep` step. `m6`'s `prep` used dataset `X`. A separate computation `c_other`
also used `X` to make a different crate output. Condensation groups `m1`…`m6`
and deletes everything only the non-representative members lead back to, which
includes `X`. It never checks that `c_other` still uses `X`. It then redirects
`c_other.usedDataset` to `ark:group/merge-text_csv-inputs`, so the graph claims
`c_other` consumed merge's input group. `X` and the true `c_other → X` edge are gone.

**B9, "missing nodes when a reference is provided?"** No. On
`CM4AISeptUpdate/Perturb-Seq/cell-atlas` I measured: 90 not-found nodes with no
reference, **0** with `--reference ../sra`, and **0** when the graph is built
from the release and rooted at cell-atlas. The only gap is that running on a
constituent alone doesn't find its siblings automatically. `isPartOf` holds only
ARK ids, not paths, so there's nothing to follow. (The crate's `isPartOf` also
lists two release ids with a trailing comma, `...data-release,`. That's a data
issue in the crate.) This stays LOW, and the fix is a hint, not auto-discovery: see below.

**D2, in plain terms.** A release lists each sub-crate through its
`ro-crate-metadata` pointer. That pointer is meant to be a file
(`sub/ro-crate-metadata.json`), but a directory (`sub`) also loads, because
`Crate.load` accepts directories. The sub-crate's folder is then computed as
`dirname("sub")`, which is `""`, the release folder itself. So that sub-crate's
preview page is written over the release's own `ro-crate-preview.html`, and its
links point at the release. It's the same root cause as D3, so it gets the same
fix (MEDIUM, with D3).

**D4, in plain terms.** By default every page is written next to the crate, and
links between pages assume that. With `-d OUTDIR` (and `-o FILE` for one page),
pages move but some links are still computed from the crate's folder, so they
break. Examples: the datasheet's links to linked crates, the preview's QC-report
link, and the datasheet link inside previews after `datasheet -o elsewhere.html`.
Also, `evidence-graph -o` moves the HTML but leaves the JSON in the default
spot, and `all` / `add-io` / `link-inverses` accept `-o` and quietly ignore it.
This is the same family as the known `-d` `../../home/oj/...` href bug. LOW, but
cheap to fix in one pass (below), since nobody can rely on `-d` today.

### HIGH

**B1: a `usedDataset` pointing at an RO-Crate resolves to that crate's outputs.**
- Root cause: the BFS (`evidence._referenced_ids`) never steps from a computation
  into a nested ROCrate's outputs. Only the later projection
  (`_expand_used_dataset`) does, so the outputs are never in `cache`.
- Fix: do the expansion once, during the BFS. When an activity's used id resolves
  to a ROCrate node, enqueue that crate's outputs instead of the crate node.
  Projection then finds them in `cache`. `_expand_used_dataset` stays as the one
  helper both call.
- Where the outputs come from, in order:
  1. the crate node's own `EVI:outputs`;
  2. if the node is a stub of a linked crate on disk (the conversion
     `core/linking.py` stub + pointer), the loaded crate's root.
     `provenance_index` already lets the loaded root replace the stub, so this
     works once (1) is read from the index copy;
  3. if neither declares outputs, derive them with `outputs.calculate` over the
     linked crate's graph, as `ensure()` does for the root.
  A crate with no resolvable outputs stays a single node, as today, but gets a
  description saying why. It no longer shows as "not found".
- Condensation should see the expanded ids as well (it reads `used_dataset_ids`).
  Expanding in the BFS covers that, as long as the computation's `usedDataset` is
  rewritten in the copied cache node and not in the crate.
- Test: the repro (innerout in the same graph), plus a linked-crate variant
  where the inner crate lives in a sibling directory.

**B3: condensation must not remove ancestors that something outside the group still uses.**
- Fix in `condense._collect_exclusive`: before marking an ancestor collapsed,
  check its consumers in the cache. Build one reverse map (id → consuming
  computations / generated datasets) per `condense_cache` call. If any consumer
  is neither collapsed nor part of this group's chain, keep the ancestor and stop
  walking past it. In the example, `X` stays and so does `c_other → X`.
  `m6`'s `prep` still collapses, because only the group reaches it.
- `collapsed_into` then only ever gets ids nothing else points at, which removes
  the false redirect at its source.
- Test: the repro above (`X` survives, `c_other.usedDataset == [X]`, group
  still has 6 members).

**E1: `add-io` overwrites, never duplicates.**
- Fix in `outputs.write`: before writing, remove every other spelling of both
  fields (`EVI:inputs`, `inputs`, `EVI:outputs`, `outputs`, plus the full IRI)
  and write a single pair. Keep the spelling the crate already used if it had
  one, so diffs stay small; otherwise use the full IRI as today.
- Also write with `ensure_ascii=False`, as `link-inverses` does.
- Test: a root with stale `EVI:outputs` → after `add-io` there's exactly one
  outputs field, holding the new value, and non-ASCII text is preserved.

### MEDIUM

**B2: unique DatasetGroup ids.** Build the group id from the consuming
computation's `@id` (slugged, not its `name`) plus a short hash of the
signature, for example `ark:group/<comp-id-slug>-<fmt>-<sig8>-inputs`. Keep the
readable part, so ids still say what they are. Two `cellranger count` runs, or
two signature groups with the same format, can then no longer collide.
Test: 6 + 6 csv inputs from two software → two groups, 12 members in total.

**C1: link targets kept when an object has both `@id` and `name`.** Add a
`fields.link(value) -> (label, href)` helper. The label is `name`, else the URL.
The href is the dict's `@id` / `url` when it's http(s) (or a DOI via
`doi_href`). Use it for license, publisher, conditionsOfAccess and contentUrl in
datasheet / composition / preview, so "MIT" renders as a link to SPDX. Fix
`badges()` so it stops passing the already-flattened string to `http_url`.

**C4: computation outputs and work patterns without `link-inverses`.** Don't
stop at a warning. Derive the missing side in memory, as `outputs.ensure` does:
in `composition.build_index`, build `generated` per computation from every
dataset's `generatedBy`, then union it with any stored `generated` /
`prov:generated`. The file isn't touched. If any inverses had to be derived,
show a short note on the card ("computation outputs derived from generatedBy;
run `link-inverses` to store them"). The preview's Computations tab reads the
same derived map.

**C5: derive inputs when only outputs are stored.** `outputs.ensure` returns
stored outputs, and inputs from `calculate()` when stored inputs are empty.
Return a flag, so the card can say "inputs derived; run `add-io` to store them"
(the same wording style as C4).

**D1: only link ARKs through `--link-base`.** `ark_href` returns
`link_base/ark:...` only for ids matching `^ark:`. An http(s) id links to
itself. Anything else (`./`, `data/a.csv`, `#local`) gets no link from
`link_base`. Local files keep their existing relative-path links where those
exist. There are 10 call sites, all go through `ark_href`, so the change is one function.

**D2 + D3: record the path that actually loaded.** In `Crate._load_stub`, set
`metadata_path` to `relpath(loaded.path, self.dir)` (the real
`.../ro-crate-metadata.json` that loaded). Don't use the raw pointer. That fixes
both the directory pointer (D2) and the `localPath` fallback (D3). If the
fallback lands outside the parent directory, the relative path will contain `..`.
That's still correct for links, and previews then go beside the real crate.

### LOW (after the above, in roughly this order)

- **D4 + the known `-d` bug, one pass:** route every inter-page href through one
  helper that knows the final path of the page being written (`_rel(target,
  dirname(page_path))`). Make `evidence-graph -o x.html` write `x.json` beside
  it. Make `all` / `add-io` / `link-inverses` reject `-o` instead of ignoring it.
  Fold in the `../../home/oj/...` fix tracked in memory.
- **B9:** after building, if there are not-found nodes and the crate has
  `isPartOf`, print one stderr line: "N referenced ids not described here; this
  crate is part of <release>. Build from the release or pass `--reference`."
  As an optional extra, look one or two directories up for a
  `ro-crate-metadata.json` whose root id is in `isPartOf`, and use it as the
  pool automatically.
- **C3:** label the identifier row by what it is: DOI (with a doi.org link),
  ARK (with a `link_base` link via the fixed `ark_href`), or "Identifier"
  otherwise.
- **B8:** when several crates in the closure describe one id, prefer the copy
  with provenance (`generatedBy` / `usedDataset` present, no
  `ro-crate-metadata` pointer) over a bare description, and only then fall back
  to load order. `owner` follows whichever copy wins.
- **C7** (KiB etc. use 1024), **C8** (resolve `irb` / `publisher` `@id` through
  the index), **C9** (the release summary counts entities on the release root
  too), **C10** (key owner by crate root id, show the name), **D5** (resolve
  `localEvidenceGraph` against the sub-crate dir), **D6** (decode `%20`, guard
  `../` escaping the crate), **E4** (Annotation/Experiment consumption counts as
  used). Each is a few lines.
- **C2 / C6** move to the feature below.

### New feature: wider (non-FAIRSCAPE) RO-Crate support

Several items are really the same gap: B6, C2, C6, E2, part of C3 and D6. The
package reads EVI (and, as a fallback, PROV). A plain RO-Crate 1.1 or a Workflow
Run Crate renders as a root node with "0 files, N other".

Recommended approach: **a read-time vocabulary adapter in `crate.py`**, built
the same way as the existing PROV fallback. Strict EVI always wins, nothing is
written to disk, and builders don't change.

| Standard RO-Crate / WRROC | Read as |
|---|---|
| `File`, `schema:Dataset`, `https://schema.org/Dataset` | Dataset (fix the `evi_short_types` IRI split, C6) |
| `CreateAction` / `OrganizeAction` | Computation |
| `object` | `usedDataset` |
| `result` | inverse `generatedBy` on each result (built once at load) |
| `instrument` → `SoftwareApplication` / `SoftwareSourceCode` / `ComputationalWorkflow` | `usedSoftware`, typed Software |
| `{"@value": ...}` | its value (C2) |
| bare string where a reference is expected | `{"@id": ...}` (E2; `link-inverses` and the graph then agree) |
| relative `@id` that is a path, no `contentUrl` | that path (D6) |

The root's outputs then come from `outputs.calculate`, which already works once
types and edges read correctly. `graph_id_for("./")` should use the crate name
or `evidence-graph` rather than `./-evidence-graph`.

Alternative: tell users to convert first with `fairscape_conversion` (the
`wrroc` / `cromwell` plugins already map WRROC to EVI). That needs no artifacts
code, but it means a viewer can't simply open a crate it was given. My
recommendation is the adapter for *viewing*, with conversion kept as the route
to a proper FAIRSCAPE crate. Even before the adapter, a crate whose graph comes
out as the root alone should say so ("no EVI/PROV provenance found; this looks
like a standard RO-Crate"), not render an empty page.

Scope guard: the adapter covers types and edges only. No attempt at arbitrary
JSON-LD contexts, list-valued `name`, etc. (A1/A2 stay out of scope.)

### Order of work
1. B1, B3, E1 (HIGH), each with its test.
2. B2, D2+D3, C1, D1, C4, C5 (MEDIUM).
3. Re-run the 39-crate smoke set and the CM4AI Sept release to confirm nothing
   regressed (graph node counts before and after; B3 can legitimately *add*
   nodes back).
4. LOW items, then the RO-Crate adapter as its own branch.

---

## Status (2026-10-13, branch `artifact-bugfixes`, not committed)

Every item in the plan except the RO-Crate adapter is fixed. Each has a test
in `tests/test_bugfixes.py`, named after its entry here. The tests were written
first and confirmed failing for the reason the entry describes, then the fix
went in. 41 new tests; the full suite is 198 passed, 22 skipped. The 4
`test_render` errors were there before this work: they load a
`/home/oj/wizards/example-wf` fixture that doesn't exist on this machine.

| Item | Fix | Where |
|---|---|---|
| B1 | used RO-Crate → its outputs, expanded during the walk; declared outputs, else derived from `hasPart`; works through linked-crate stubs | `evidence._crate_output_ids`, `EvidenceGraph._unwrap_crates` |
| B3 | an ancestor is collapsed only if everything pointing at it is going too | `condense._dependents`, `_collect_exclusive` |
| E1 | one spelling of inputs/outputs (the one already there, else full IRI); others removed; `ensure_ascii=False` | `outputs._replace` |
| B2 | group id = consumer `@id` slug + format + 8-char signature digest | `condense._group_node` |
| C1 | `http_url` reads an object's `@id` / `url` before its name; license badge fixed | `fields.http_url`, `datasheet.badges` |
| D1 | `ark_href`: ARKs via `--link-base`, http ids as themselves, anything else unlinked | `fields.ark_href` |
| D2/D3 | sub-crate `metadata_path` = the file that actually loaded | `Crate._load_stub` |
| C4 | activity outputs = stored `generated` ∪ `generatedBy` inverse, in memory; card note suggests `link-inverses` | `composition.derived_generated`, `generated_ids`, `notes`; `preview.computation_details` |
| C5 | stored outputs + no stored inputs → inputs derived; card note suggests `add-io` | `outputs.ensure`, `composition.notes` |
| D4 | `-d`: links to crate files written as if the output dir sat at the crate root (also fixes the `../../home/oj/...` bug); `datasheet -o` links/previews follow the page; `evidence-graph -o x.html` writes `x.json`; `all` rejects `-o`, `add-io` / `link-inverses` reject `-o` and `-d` | `composition._href`, `cli` |
| D5 | `localEvidenceGraph` resolved against whichever of sub-crate / release dir holds the file (CM4AI writes release-relative values on sub-crate roots too) | `composition.card` |
| B9 | stderr note when ids are unresolved and the root has `isPartOf` | `cli._hint_unresolved` |
| C3 | identifier labelled DOI / ARK / `propertyID` / "Identifier", with the matching link | `fields.identifier` |
| B8 | a later copy replaces an earlier one unless only the earlier has provenance | `crate.replaces`, used by `provenance_index` and `composition.build_index` |
| C7 | KiB/MiB/... use 1024 | `fields.size_bytes` |
| C8 | bare `{"@id"}` irb / publisher resolved through the index | `fields.resolved` |
| C9 | release summary also counts entities on the release root not in any constituent | `datasheet.summary` |
| C10 | `owner` maps to crate root ids; `Composition.names` for display; `preview.build_context(..., names=)` | `composition`, `preview`, `cli` |
| D6 | contentUrl percent-decoded; paths escaping the crate dir don't resolve | `Crate.file_path` |
| E4 | Annotation/Experiment consumption counts as used; `derivedFrom` counts as having a source | `outputs.calculate` |

Checked on real data:
- The 45 crates under `CM4AIReleases`, conversion examples and grader examples
  all build without errors, and their evidence graphs (node counts, not-found
  counts, groups) are identical before and after.
- One card input count changed: `CM4AISeptUpdate-dataverse` went from 19 to 1.
  That's E4: its 19 datasets are only `derivedFrom` the 18 undefined
  `https://fairscape.net/api/ark:...` ids, so those external ids are now the
  inputs and the card leaves them out as unresolved. Before, the derived
  datasets themselves were wrongly listed as inputs.
- One card evidence link changed (June release, `mass-spec/iPSCs`): the value
  sits on the sub-crate root and the file exists under neither base, so the
  link was broken before and after; it now follows the D5 rule.
- `all CM4AISeptUpdate -d OUT`: 0 links leave OUT, and every link resolves once
  OUT sits on top of the crate. The `ro-crate-prov-graph.html` links point at
  crate files that `-d` doesn't copy.

API changes for callers:
- `composition.build_index` returns `(index, owner, names)`, and `owner`
  values are now root ids.
- `preview.build_context` takes `names=`.
- `composition.build` takes `page_dir=`.
- `datasheet.distribution` takes `link_base`.

The Dataverse previewer vendors its own copy, so it's unaffected until it's
re-vendored.

Not done: the RO-Crate vocabulary adapter (its own branch, per the plan).
