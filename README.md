# internal-linking

> 📖 **手把手 fork + 使用教學（GitHub Pages）**：https://wolfganghuang0083.github.io/content-ops-toolkit/

Weave the **already-published** articles of a topic cluster into a topic web using on-topic, keyword-bearing internal links. Brand-agnostic, drop-in for any site/client.

This is the implementation of **step ⑥ of a content pipeline: "go back and interlink the cluster."** Once new articles are live, you don't leave them as orphans — you stitch them into the cluster so search engines (and readers) see a coherent topic web.

> 把同一個內容叢集裡「已上線」的文章，用貼題、帶關鍵字的內鏈織成主題網。品牌無關、可直接套到任何網站/客戶。它是內容產線「第⑥步：回頭把叢集互鏈」的實作。

## Why this exists / 它解決什麼

The content map declares *which* articles should link to each other. Reality drifts: articles get published at different times, links get forgotten, scheduled posts would 404 if linked too early. This skill **reconciles the declared links with the links that actually exist on the live site** — adding only what's missing, only to live pages, and in a way that's safe to re-run.

## Core rules / 鐵則 (the value — kept from the validated SOP)

- **Only link to live (published) pages** — scheduled/draft targets would 404; skip them, re-run after they go live.
- **Idempotent / safely re-runnable** — if the source already contains the target slug, skip it.
- **Writes are deterministic code, never the LLM** — the LLM only decides *where* to link and *what to say*; the actual read/insert/PUT/verify is done by `interlink_apply.py`.
- **Anchors must be on-topic** — the link goes inside a topically-relevant paragraph with descriptive, keyword-bearing anchor text (never "click here"). No footer "related posts" dumping.
- **Hub-spoke topology by default** — hub ↔ each spoke; or `--mesh` for full interlinking of small clusters.
- **Anchor sentences obey FACTS** — no invented numbers, client names, or competitor mentions; soft CTAs only.

## Install / 安裝

Clone into your Claude skills directory:

```bash
git clone https://github.com/<you>/internal-linking.git ~/.claude/skills/internal-linking
```

Requirements: Python 3 (standard library only) and Node (for the workflow). No third-party packages.

## Configure / 設定

Secrets are **environment variables only — never written to a file**:

```bash
export CMS_USER='<your-cms-user>'
export CMS_APP_PW='<your-cms-app-password-or-token>'
```

Site + workflow paths:

```bash
export CMS_BASE='https://your-site.com'          # or pass --base per run
export INTERLINK_MISSING=/tmp/interlink_missing.json
export INTERLINK_PLAN=/tmp/interlink_plan.json
export INTERLINK_FACTS=/abs/path/to/FACTS.md     # copy from templates/FACTS.template.md
```

Copy `templates/FACTS.template.md` to your own `FACTS.md`, fill the `<PLACEHOLDER>`s, and point `INTERLINK_FACTS` at it.

## Quick start / 快速上手

```bash
# 1) Compute the matrix: live status + existing interlinks + what's MISSING.
CMS_USER=$CMS_USER CMS_APP_PW=$CMS_APP_PW \
  python3 scripts/interlink_apply.py matrix \
    --base https://your-site.com \
    --ids 101,102,103 --hub 101 --cta-path contact \
    --out /tmp/interlink_missing.json --dumpdir /tmp/interlink_raw

# 2) Find anchors: one agent per missing link writes a verbatim-unique anchor
#    + a FACTS-safe sentence -> plan.json. (Workflow uses fixed env paths, no args.)
Workflow({ scriptPath: 'scripts/interlink.js' })

# 3) Review plan.json, then apply (idempotent) and verify on the public pages.
CMS_USER=$CMS_USER CMS_APP_PW=$CMS_APP_PW \
  python3 scripts/interlink_apply.py apply  --base https://your-site.com --plan /tmp/interlink_plan.json
CMS_USER=$CMS_USER CMS_APP_PW=$CMS_APP_PW \
  python3 scripts/interlink_apply.py verify --base https://your-site.com --plan /tmp/interlink_plan.json
```

Matrix output prints `v` (already linked) / `.` (missing). If there are 0 missing, you're done.

## Topology / 連結拓樸

| Flag | Behavior |
|------|----------|
| `--hub <id>` | hub ↔ each spoke (bidirectional); spokes must link the hub |
| `--mesh` | full mesh — every live post links every other (small clusters) |
| (neither) | defaults to full mesh for the given IDs |
| `--cta-path <path>` | also ensure every live post links one shared conversion page (e.g. `contact`) |

Cross-cluster bridges are manual only (never auto-linked).

## How it fits the toolchain / 工具鏈

This is the linking layer of a 3-part content system:

1. **content-map-builder** (upstream — *decides what to write*): clusters topics by strategic role, prioritizes with real data, tracks status. It is the **source of truth** for which articles belong to which cluster and which should interlink.
2. **content-pipeline** (*writes & publishes*): topic → SERP → draft → media → publish, with anti-fabrication guardrails (the `FACTS.md` this skill reuses).
3. **internal-linking** (this repo — *step ⑥, weaves the cluster*): after articles go live, reconcile declared vs. actual links and add the missing ones safely.

Typical loop: publish a new article via content-pipeline → call **internal-linking** on its cluster to weave it in → write the linked targets back to the content map ("internal-link targets" column).

## Files

- `scripts/interlink_apply.py` — deterministic CMS I/O: `matrix` / `apply` / `verify`. All writes go through here.
- `scripts/interlink.js` — workflow: per missing link, find an on-topic verbatim anchor + FACTS-safe sentence → `plan.json`.
- `templates/FACTS.template.md` — anti-fabrication guardrail template for anchor sentences.
- `SKILL.md` — the SOP and rules (Traditional Chinese).

## Porting to another CMS / 換 CMS

The reference implementation targets the **WordPress REST API** (`/wp-json/wp/v2/...`, `context=edit` for raw content, Gutenberg block-end markers for insertion). To target another CMS, change in `interlink_apply.py`: `api`, `get_public`, `fetch_post`, the `PUT` in `cmd_apply`, and `BLOCK_ENDS`/`_insert`; and the `insertion_html` template in `interlink.js`. Everything else (live-status logic, interlink matrix, missing-link computation, idempotency, anchor workflow) is CMS-agnostic.

## License

MIT
