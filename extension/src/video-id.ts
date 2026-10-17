// /watch and the embedded player carry the id in `?v=`; /shorts/, /live/ and
// the old /embed/ and /v/ forms carry it in the path.
const PATH_VIDEO_ID = /^\/(?:shorts|live|embed|v)\/([A-Za-z0-9_-]{6,})/;

export function videoIdFromHref(href: string): string | null {
  try {
    const u = new URL(href);
    const v = u.searchParams.get("v");
    if (v) return v;
    return PATH_VIDEO_ID.exec(u.pathname)?.[1] ?? null;
  } catch {
    return null;
  }
}
