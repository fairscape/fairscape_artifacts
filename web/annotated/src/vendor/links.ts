// Offline pages have no server to link to. `window.__FS_LINK_BASE__` names
// one when the page is published next to a FAIRSCAPE instance; otherwise
// identifiers render as plain text anchors with no href.
export function viewHref(id: string | undefined | null): string | undefined {
  const base = (window as any).__FS_LINK_BASE__;
  if (!id || !base) return undefined;
  return `${String(base).replace(/\/$/, "")}/view/${id}`;
}
