// Internal-linking workflow: for each MISSING link (computed by
// interlink_apply.py matrix), spawn an agent that finds a verbatim-unique,
// on-topic anchor in the source post and writes a safe, FACTS-compliant anchor
// sentence — then emit plan.json for interlink_apply.py to apply deterministically.
//
// IMPORTANT: this workflow does NOT take args (passing args via scriptPath gets
// stringified in some harnesses). It uses FIXED read/write paths below, so you
// MUST run `interlink_apply.py matrix --out <MISSING>` first to produce the input.
//
// CMS-agnostic: the only CMS-specific assumption is the insertion_html block
// format (Gutenberg paragraph). Change INSERTION_TEMPLATE for a different CMS.
export const meta = {
  name: 'internal-linking',
  description:
    'For each missing intra-cluster link, find an on-topic verbatim anchor + ' +
    'write a FACTS-safe anchor sentence, and emit an applyable plan.json.',
  phases: [
    { title: 'Read', detail: 'Read the missing-links list from matrix' },
    { title: 'Anchor', detail: 'One agent per missing link finds an on-topic anchor' },
    { title: 'Write', detail: 'Emit plan.json' },
  ],
}

// --- Fixed paths (avoid the scriptPath-args-stringification trap) -----------
// matrix --out writes MISSING; this workflow reads it and writes PLAN.
// Override with env vars when needed.
const MISSING = process.env.INTERLINK_MISSING || '/tmp/interlink_missing.json'
const PLAN = process.env.INTERLINK_PLAN || '/tmp/interlink_plan.json'
// Path to your project's anti-fabrication guardrail file (anchor sentences must
// obey it). Generalized: point this at YOUR FACTS file (see templates/FACTS.template.md).
const FACTS = process.env.INTERLINK_FACTS || '/path/to/your/FACTS.md'

const MISSING_SCHEMA = {
  type: 'object',
  properties: {
    dumpdir: { type: 'string' },
    all_missing: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          source: { type: ['integer', 'string'] },
          source_slug: { type: 'string' },
          target: { type: ['integer', 'string'] },
          target_slug: { type: 'string' },
          target_url: { type: 'string' },
          target_title: { type: 'string' },
        },
        required: ['source', 'target_url', 'target_slug'],
      },
    },
  },
  required: ['dumpdir', 'all_missing'],
}

const ANCHOR_SCHEMA = {
  type: 'object',
  properties: {
    anchor_marker: {
      type: 'string',
      description: 'A 10-40 char snippet copied VERBATIM from content.raw, UNIQUE in the whole body.',
    },
    insertion_html: {
      type: 'string',
      description: 'A complete CMS block (Gutenberg paragraph) containing the <a> to the target.',
    },
    relevance: {
      type: 'string',
      description: 'One line explaining why this anchor is on-topic (for human review).',
    },
  },
  required: ['anchor_marker', 'insertion_html'],
}

phase('Read')
const data = await agent(
  `Use Read to open ${MISSING}, then return its "dumpdir" and "all_missing" fields verbatim as JSON.`,
  { schema: MISSING_SCHEMA, label: 'read-missing', phase: 'Read' }
)
const dumpdir = data.dumpdir
const links = data.all_missing || []
log(`${links.length} missing link(s); finding anchors`)

phase('Anchor')
function anchorPrompt(l) {
  const isCta = l.target_slug === 'cta'
  return (
    'You are maintaining the internal links of a topic cluster. First use Read on ' +
    FACTS + ' (anti-fabrication rules: no invented numbers, no invented client/customer ' +
    'names, no naming competitors, soft CTAs only, do not contradict the facts file).\n' +
    'The source post body (content.raw) is at ' + dumpdir + '/' + l.source + '.md (use Read).\n' +
    'Target post: "' + (l.target_title || 'the conversion page') + '" ' + l.target_url + '\n' +
    'Task: find ONE paragraph in the source that is topically related to the target, ' +
    'where a link to the target reads naturally.' +
    (isCta
      ? ' (Target is a conversion/CTA page; pick a passage where the reader is stuck / ' +
        'wants help / wants to act — that is where a soft CTA fits.)'
      : '') +
    '\n' +
    'Return:\n' +
    '1. anchor_marker: a snippet copied VERBATIM from content.raw that is UNIQUE in the ' +
    'whole body (10-40 chars, typically part of one sentence or an H2). Keep original ' +
    'punctuation, verbatim, unique (the link is inserted right after the block it sits in).\n' +
    '2. insertion_html: ONE complete CMS block, EXACTLY this format:\n' +
    '   <!-- wp:paragraph -->\\n<p><em>Related: {one natural sentence on why this is ' +
    'relevant, obeying FACTS, nothing invented} -- <a href="' + l.target_url + '">' +
    '{descriptive anchor text containing the target keyword}</a>.</em></p>\\n' +
    '<!-- /wp:paragraph -->\n' +
    '   Anchor text MUST be descriptive and keyword-bearing (never "click here"). ' +
    'The relevance sentence should naturally lead the reader to want the target.\n' +
    '3. relevance: one line on why the anchor is on-topic.\n' +
    'Rules: marker must be verbatim AND unique in content.raw; the anchor must be ' +
    'topically relevant (if not, pick a different passage — do NOT force it); ' +
    'no invented numbers, no invented names, do not name competitors.'
  )
}

const planned = await parallel(
  links.map((l) => () =>
    agent(anchorPrompt(l), {
      schema: ANCHOR_SCHEMA,
      label: `anchor:${l.source}->${l.target}`,
      phase: 'Anchor',
    }).then((r) =>
      r
        ? {
            source: l.source,
            target: l.target,
            target_url: l.target_url,
            target_slug: l.target_slug,
            target_title: l.target_title,
            anchor_marker: r.anchor_marker,
            insertion_html: r.insertion_html,
            relevance: r.relevance || '',
          }
        : null
    )
  )
)
const plan = planned.filter(Boolean)

phase('Write')
await agent(
  'Use Write to save the JSON below EXACTLY as-is to ' + PLAN +
    ' (must be a valid JSON array; do not add any prose or markdown fences):\n' +
    JSON.stringify(plan, null, 1),
  { label: 'write-plan', phase: 'Write' }
)
log(`plan written: ${plan.length} link(s) -> ${PLAN}`)
return { count: plan.length, planPath: PLAN, dropped: links.length - plan.length }
