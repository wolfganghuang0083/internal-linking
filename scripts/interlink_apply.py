# -*- coding: utf-8 -*-
"""Cluster internal linking — deterministic CMS I/O (compute matrix / apply / verify).

Design rule: WRITES NEVER GO THROUGH AN LLM. The LLM (see interlink.js) only
decides *where* to link (anchor marker) and *what to say* (anchor sentence).
The actual read / insert / PUT / verify is done deterministically here, so the
pass is idempotent and safely re-runnable.

Reference implementation targets the WordPress REST API. To port to another CMS,
replace the four I/O primitives below (`api`, `get_public`, `fetch_post`,
the PUT inside `cmd_apply`) and the Gutenberg block markers in `_insert`.

Secrets ALWAYS come from environment variables — never hard-code or write them
to a file:
    CMS_USER     CMS username / application-password user
    CMS_APP_PW   CMS application password (or API token)
Site base URL comes from --base or the CMS_BASE env var.

Usage:
  # 1) Matrix: read publish status + existing interlinks, output the MISSING
  #    links as JSON (only links whose TARGET is already live, to avoid 404s).
  python3 interlink_apply.py matrix --base https://example.com \
      --ids 101,102,103 [--hub 101] [--mesh] [--cta-path /contact/] \
      --out /tmp/interlink_missing.json --dumpdir /tmp/interlink_raw

  # 2) Apply: consume the plan.json produced by interlink.js (each item carries
  #    anchor_marker + insertion_html). Idempotent insert + read-back verify.
  python3 interlink_apply.py apply --base https://example.com \
      --plan /tmp/interlink_plan.json

  # 3) Verify: fetch the PUBLIC page and confirm the link is present.
  python3 interlink_apply.py verify --base https://example.com \
      --plan /tmp/interlink_plan.json
"""
import os
import sys
import json
import argparse
import base64
import urllib.request
import urllib.error

# ----------------------------------------------------------------------------
# CONFIG — everything site-specific lives here or comes from argparse/env.
# Nothing brand-specific should appear anywhere else in this file.
# ----------------------------------------------------------------------------
DEFAULT_BASE = os.environ.get("CMS_BASE", "https://example.com")
# A real browser UA avoids some WAF/CDN rules (e.g. Cloudflare bot challenges).
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
DEFAULT_DUMPDIR = os.environ.get("INTERLINK_DUMPDIR", "/tmp/interlink_raw")
# Token used to detect an existing CTA link (see --cta-path). Kept generic.
CTA_KEY = "cta"

# Site base URL; set per-run from --base (falls back to env / DEFAULT_BASE).
BASE = DEFAULT_BASE


def _auth():
    """Build a Basic auth header from CMS_USER / CMS_APP_PW (env only)."""
    u, p = os.environ.get("CMS_USER"), os.environ.get("CMS_APP_PW")
    assert u and p, "Set environment variables CMS_USER and CMS_APP_PW first."
    return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()


def api(method, path, data=None, auth=True):
    """Call the CMS REST API. WordPress reference impl: /wp-json/wp/v2/...

    To port to another CMS, change the request building / response parsing here.
    """
    h = dict(UA)
    if auth:
        h["Authorization"] = _auth()
    if data is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def get_public(url):
    """Fetch a public (unauthenticated) page as HTML — used by verify."""
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=40).read().decode()


def fetch_post(pid):
    """Fetch one post with its raw (editable) content + publish status.

    WordPress reference impl uses context=edit to get content.raw. Returns a
    CMS-agnostic dict the rest of this script relies on.
    """
    p = api(
        "GET",
        f"/wp-json/wp/v2/posts/{pid}"
        "?context=edit&_fields=id,slug,status,title,content,link",
    )
    return {
        "id": p["id"],
        "slug": p["slug"],
        "status": p["status"],
        "title": p["title"]["raw"],
        "link": p["link"],
        "raw": p["content"]["raw"],
    }


# ----------------------------------------------------------------------------
# matrix — compute live status + interlink matrix + missing links (read-only)
# ----------------------------------------------------------------------------
def cmd_matrix(a):
    ids = [int(x) for x in a.ids.split(",") if x.strip()]
    # --outbound-src: SCHEDULE-TIME interlinking. Treat these (possibly not-yet-
    # live) posts as link SOURCES and link them OUTBOUND only, to targets in
    # --ids that are already live. Their bodies will already contain the links
    # by the time they go live, so no 404. The reciprocal inbound links are
    # added later, when each source itself goes live (re-run as a normal mesh).
    outbound_src = [int(x) for x in (a.outbound_src or "").split(",") if x.strip()]
    all_ids = ids + [s for s in outbound_src if s not in ids]
    posts = {pid: fetch_post(pid) for pid in all_ids}
    # RULE: only link to pages that are already PUBLISHED. Scheduled / draft
    # targets would 404 — skip them now, re-run after they go live.
    live = [pid for pid in ids if posts[pid]["status"] == "publish"]
    future = [pid for pid in all_ids if posts[pid]["status"] != "publish"]
    hub = a.hub if (a.hub and a.hub in ids) else None

    # Required directed links (topology):
    #   --outbound-src: each source -> every live target (outbound only)
    #   --mesh        : every live post links to every other live post
    #   --hub <id>    : hub <-> each spoke (bidirectional)
    #   neither       : default to full mesh (fine for small clusters)
    needed = set()
    if outbound_src:
        for s in outbound_src:
            for t in live:
                if s != t:
                    needed.add((s, t))
        if a.mesh:  # optionally also mesh the live ones (usually already linked)
            for s in live:
                for t in live:
                    if s != t:
                        needed.add((s, t))
    elif a.mesh:
        for s in live:
            for t in live:
                if s != t:
                    needed.add((s, t))
    elif hub:
        for s in live:
            if s != hub:
                needed.add((s, hub))
                needed.add((hub, s))
    else:
        for s in live:
            for t in live:
                if s != t:
                    needed.add((s, t))

    # Source set = live posts + the (possibly non-live) outbound sources.
    sources = live + [s for s in outbound_src if s not in live]

    # A link is MISSING if the target is live AND the source body does not
    # already contain the target slug (idempotency: existing link => skip).
    missing = []
    for s, t in sorted(needed):
        if t in live and posts[t]["slug"] not in posts[s]["raw"]:
            missing.append({
                "source": s,
                "source_slug": posts[s]["slug"],
                "target": t,
                "target_slug": posts[t]["slug"],
                "target_url": posts[t]["link"],
                "target_title": posts[t]["title"],
            })

    # Optional CTA coverage check: ensure every live post links to one shared
    # conversion page (e.g. /contact/, /book/, /demo/). Set via --cta-path.
    cta = []
    if a.cta_path:
        cta_url = BASE.rstrip("/") + "/" + a.cta_path.strip("/") + "/"
        for pid in sources:
            if a.cta_path.strip("/") not in posts[pid]["raw"]:
                cta.append({
                    "source": pid,
                    "source_slug": posts[pid]["slug"],
                    "target": CTA_KEY,
                    "target_slug": CTA_KEY,
                    "target_url": cta_url,
                    "target_title": a.cta_title or "Get in touch",
                })

    # Dump each live post's raw body so the workflow agents can read it when
    # searching for a verbatim, unique anchor marker.
    dumpdir = a.dumpdir
    if dumpdir:
        os.makedirs(dumpdir, exist_ok=True)
        for pid in sources:
            with open(os.path.join(dumpdir, f"{pid}.md"), "w") as fh:
                fh.write(posts[pid]["raw"])

    out = {
        "base": BASE,
        "live": [
            {"id": p, "slug": posts[p]["slug"],
             "title": posts[p]["title"], "url": posts[p]["link"]}
            for p in live
        ],
        "future_skip": [
            {"id": p, "slug": posts[p]["slug"], "status": posts[p]["status"]}
            for p in future
        ],
        "hub": hub,
        "mode": "mesh" if a.mesh else ("hub-spoke" if hub else "full(small)"),
        "dumpdir": dumpdir,
        "missing_links": missing,
        "missing_cta": cta,
        "all_missing": missing + cta,
    }

    # Print a human-readable matrix.
    print(
        f"live {len(live)} / skipped(non-live) {len(future)} | "
        f"hub={hub} | missing links {len(missing)} | missing CTA {len(cta)}"
    )
    cols = live
    print("     " + " ".join(str(t)[-4:] for t in cols))
    for s in cols:
        row = []
        for t in cols:
            if s == t:
                row.append(" - ")
            else:
                row.append(" v " if posts[t]["slug"] in posts[s]["raw"] else " . ")
        print(f"{s} " + "".join(row))

    if a.out:
        with open(a.out, "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("-> missing-links list written to", a.out)
    return out


# ----------------------------------------------------------------------------
# apply — insert each planned link idempotently, then read-back verify
# ----------------------------------------------------------------------------
# WordPress (Gutenberg) block-end markers. Insertion happens right AFTER the
# block that contains the anchor marker, so the link reads as a natural
# follow-on. To port to another CMS, change these markers and `_insert`.
BLOCK_ENDS = (
    "<!-- /wp:paragraph -->",
    "<!-- /wp:list -->",
    "<!-- /wp:table -->",
)


def _insert(raw, marker, html):
    """Insert `html` right after the block containing `marker`.

    Requires `marker` to be VERBATIM-UNIQUE in `raw` (asserted), so insertion
    is deterministic and re-runs are safe.
    """
    assert raw.count(marker) == 1, f"marker matched {raw.count(marker)} times (must be unique)"
    pos = raw.find(marker)
    ends = []
    for c in BLOCK_ENDS:
        e = raw.find(c, pos)
        if e != -1:
            ends.append(e + len(c))
    assert ends, "no block-end marker found after the anchor marker"
    end = min(ends)
    return raw[:end] + "\n\n" + html + raw[end:]


def cmd_apply(a):
    plan = json.load(open(a.plan))
    items = plan if isinstance(plan, list) else plan.get("links", [])
    done, skip, fail = 0, 0, 0
    for it in items:
        sid = it["source"]
        marker = it["anchor_marker"]
        html = it["insertion_html"]
        # Idempotency key: the target slug (or the CTA token).
        key = it.get("target_slug") or CTA_KEY
        try:
            raw = fetch_post(sid)["raw"]
            if key in raw:
                print(f"  {sid}->{it.get('target')} already linked, skip")
                skip += 1
                continue
            new = _insert(raw, marker, html)
            api("PUT", f"/wp-json/wp/v2/posts/{sid}", {"content": new})
            # Read back to confirm the write actually persisted.
            chk = fetch_post(sid)["raw"]
            ok = key in chk
            print(f"  {sid}->{it.get('target')} {'v' if ok else 'x(not saved)'}")
            done += ok
            fail += (not ok)
        except Exception as e:
            print(f"  {sid}->{it.get('target')} x {str(e)[:90]}")
            fail += 1
    print(f"apply done: ok {done} / skip {skip} / fail {fail}")


# ----------------------------------------------------------------------------
# verify — confirm the link exists on the PUBLIC page (not just in the API)
# ----------------------------------------------------------------------------
def cmd_verify(a):
    plan = json.load(open(a.plan))
    items = plan if isinstance(plan, list) else plan.get("links", [])
    bysrc = {}
    for it in items:
        bysrc.setdefault(it["source"], []).append(it)
    for sid, its in bysrc.items():
        p = fetch_post(sid)
        html = get_public(p["link"])
        for it in its:
            key = it.get("target_slug") or CTA_KEY
            print(f"  public {sid} -> {it.get('target')}: {'v' if key in html else 'x'}")


def main():
    ap = argparse.ArgumentParser(description="Cluster internal linking (deterministic CMS I/O).")
    ap.add_argument("--base", default=None, help="Site base URL (else CMS_BASE env / DEFAULT_BASE).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("matrix", help="Compute live status + interlink matrix + missing links.")
    m.add_argument("--ids", required=True, help="Comma-separated post IDs in the cluster.")
    m.add_argument("--hub", type=int, default=None, help="Hub post ID for hub-spoke topology.")
    m.add_argument("--mesh", action="store_true", help="Full mesh: every live post links every other.")
    m.add_argument("--outbound-src", dest="outbound_src", default=None,
                   help="Schedule-time mode: treat these (maybe non-live) posts as sources, "
                        "linking them OUTBOUND only to already-live targets in --ids.")
    m.add_argument("--cta-path", default=None, dest="cta_path",
                   help="Shared conversion page path to ensure coverage, e.g. contact.")
    m.add_argument("--cta-title", default=None, dest="cta_title", help="Display title for the CTA target.")
    m.add_argument("--out", default=None, help="Where to write the missing-links JSON.")
    m.add_argument("--dumpdir", default=DEFAULT_DUMPDIR, help="Where to dump each post's raw body.")
    m.set_defaults(func=cmd_matrix)

    ap2 = sub.add_parser("apply", help="Apply a plan.json (idempotent insert + read-back verify).")
    ap2.add_argument("--plan", required=True)
    ap2.set_defaults(func=cmd_apply)

    v = sub.add_parser("verify", help="Confirm links exist on the public pages.")
    v.add_argument("--plan", required=True)
    v.set_defaults(func=cmd_verify)

    a = ap.parse_args()

    global BASE
    BASE = a.base or os.environ.get("CMS_BASE", DEFAULT_BASE)

    a.func(a)


if __name__ == "__main__":
    main()
