"""Adapters that let `fairscape_graph_tools` interpret a crate on disk.

The interpretation engine — condensation, per-computation annotation, graph
synthesis, the AnnotatedEvidenceGraph model — lives in `fairscape_graph_tools`
and knows nothing about files. It talks to four ports (`GraphSource`,
`ResultSink`, `TaskTracker`, `SoftwareFetcher`); this package supplies them
over a `Crate`, a sidecar JSON path, a progress callable and the local
filesystem. Nothing here decides *what* an interpretation says.

Importing this package requires the `interpret` extra. The artifact side —
running a pipeline, loading its output and rendering the page — is
`fairscape_artifacts.interpretation`, which imports this lazily.
"""

from fairscape_artifacts.interpret.adapters import (  # noqa: F401
    SOURCE_PLACEHOLDER,
    CrateGraphSource,
    LocalSoftwareFetcher,
    ProgressTracker,
    SidecarSink,
)
