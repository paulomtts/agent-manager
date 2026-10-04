export const meta = {
  name: 'audit',
  description: 'Run the repo audit dimensions against a diff or PR (an audit) or a named unit or file list (a swipe): select the applicable checks, audit, adversarially verify each finding, then group survivors into proposed bundles. Report-only: never changes code.',
  phases: [
    { title: 'Scope', detail: 'take the file surface handed in by the calling skill' },
    { title: 'Select', detail: 'match the surface against each dimension\'s applies-to frontmatter in .claude/skills/audit/dimensions/' },
    { title: 'Audit', detail: 'one agent per selected audit dimension' },
    { title: 'Verify', detail: 'one adversarial refuter per finding; drop refuted findings' },
    { title: 'Bundle', detail: 'group survivors into proposed PR bundles; nothing is fixed' },
  ],
}

const SELECT_SCHEMA = {
  type: 'object',
  properties: {
    selections: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          dimension: { type: 'string', description: 'axis directory name under .claude/skills/audit/dimensions/' },
          checks: { type: 'array', items: { type: 'string' }, description: 'check ids in that dimension (e.g. PB1) whose scope and applies-to match the surface' },
        },
        required: ['dimension', 'checks'],
      },
    },
  },
  required: ['selections'],
}

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          what: { type: 'string' },
          fix: { type: 'string' },
          standard: { type: 'string', description: 'docs/standards/** section or CLAUDE.md section that makes this a violation' },
          severity: { type: 'string', enum: ['critical', 'major', 'moderate', 'minor'] },
        },
        required: ['id', 'file', 'what', 'fix', 'severity'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    holds: { type: 'boolean' },
    reasoning: { type: 'string', description: 'why the finding holds against the real code, or the concrete evidence refuting it' },
  },
  required: ['holds', 'reasoning'],
}

const BUNDLE_SCHEMA = {
  type: 'object',
  properties: {
    bundles: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'kebab-case branch-friendly bundle name' },
          theme: { type: 'string', description: 'one sentence: what unites these findings' },
          findingIds: { type: 'array', items: { type: 'string' } },
        },
        required: ['name', 'theme', 'findingIds'],
      },
    },
  },
  required: ['bundles'],
}

const EXCLUDED_PATHSPECS = `':(exclude)uv.lock' ':(exclude)docs/superpowers' ':(exclude).claude/worktrees'`

const params = typeof args === 'string' ? JSON.parse(args) : (args || {})
const scope = params.scope || 'diff'
if (!['diff', 'pr', 'module', 'files'].includes(scope)) {
  throw new Error(`audit: unknown scope "${scope}" (expected diff, pr, module, or files)`)
}
if (params.fix !== undefined && params.fix !== false) {
  throw new Error('audit: the fix phase does not exist in this repo; the audit is report-only (pass reportOnly: true and no fix)')
}
if (params.reportOnly !== true) {
  throw new Error('audit: args.reportOnly must be true; this audit only reports')
}

const standardsSource = params.standardsRef
  ? `Read standards docs from git ref ${params.standardsRef} (e.g. \`git show ${params.standardsRef}:docs/standards/architecture.md\`) when they are absent from the checked-out tree. `
  : ''

phase('Scope')

let surface
if (scope === 'module' || scope === 'files') {
  if (scope === 'module' && (typeof params.module !== 'string' || !params.module.trim())) {
    throw new Error(`audit: scope "module" requires args.module (a unit name from the audit skill's unit map), got: ${JSON.stringify(params.module)}`)
  }
  if (!Array.isArray(params.files) || !params.files.length) {
    throw new Error(`audit: scope "${scope}" requires args.files (the unit's repo-relative file list, resolved by the calling skill)`)
  }
  surface = {
    files: params.files,
    diffCmd: null,
    label: scope === 'module' ? `unit ${params.module}` : 'file list',
    summary: params.summary || null,
  }
} else {
  // The workflow runtime has no shell, so the calling skill runs git and
  // hands the results in.
  if (!params.range) {
    throw new Error(`audit: scope "${scope}" requires args.range (e.g. "<base>...HEAD" or "<sha>^1 <sha>")`)
  }
  let files = Array.isArray(params.files) ? params.files : []
  const range = params.range
  if (!files.length && params.listFiles === true) {
    const listed = await agent(
      `Run \`${params.gitDir ? 'git -C ' + params.gitDir : 'git'} diff --name-only ${range} -- . ${EXCLUDED_PATHSPECS}\` ` +
      `and return its output as a list of repo-relative paths. Do nothing else.`,
      { phase: 'Scope', label: 'list-files', effort: 'low', schema: { type: 'object', properties: { files: { type: 'array', items: { type: 'string' } } }, required: ['files'] } },
    )
    files = listed?.files || []
  }
  surface = {
    files,
    diffCmd: `git diff ${range} --`,
    label: scope === 'pr' ? `PR #${params.pr || 'unknown'}` : `branch vs ${params.base || 'master'}`,
    summary: null,
  }
}

surface.files = surface.files.filter(f =>
  f !== 'uv.lock' && !f.startsWith('docs/superpowers/') && !f.startsWith('.claude/worktrees/'))

if (!surface.files.length) {
  log(`${surface.label}: empty surface — nothing to audit`)
  return { scope, label: surface.label, status: 'no-scope', findings: [] }
}
log(`${surface.label}: ${surface.files.length} file(s) under audit`)

phase('Select')

const includeGuarded = params.includeGuarded === true
const selected = await agent(
  `Select which audit checks apply to a set of files. List .claude/skills/audit/dimensions/ — each subdirectory is an axis dimension whose CHECKLIST.md frontmatter has a \`checks:\` list; ` +
  `each entry carries \`id\`, \`scopes\`, \`applies-to\`, \`detection\` and, for mechanical checks, \`guard\`. ` +
  `Read ONLY the frontmatter. A check is selected when (1) its scopes include "${scope === 'diff' || scope === 'pr' ? 'diff' : 'unit'}", (2) it has an applies-to field ` +
  `(entries without one never select by path), and (3) at least one file below matches that applies-to ` +
  `(applies-to is a comma-separated glob list; a glob prefixed with "!" excludes; apply every entry that matches, do not stop at the first hit). ` +
  (includeGuarded ? '' : `Skip a mechanical check whose \`guard\` is anything other than \`pending\` (an executable test already owns it). `) +
  `uv.lock, docs/superpowers/** and .claude/worktrees/** never select anything. ` +
  `Group the selected check ids by dimension; omit dimensions with no selected check. ` +
  (Array.isArray(params.dimensions) && params.dimensions.length ? `Consider ONLY these dimensions: ${params.dimensions.join(', ')}. ` : '') +
  `If nothing matches, return an empty list. ` +
  `Do NOT analyse the code for findings: this is selection only.\n\nFiles (${surface.label}):\n${surface.files.join('\n')}`,
  { phase: 'Select', label: 'select', effort: 'low', schema: SELECT_SCHEMA },
)

const picks = (selected?.selections || [])
  .filter(x => /^[a-z][a-z-]*$/.test(x.dimension) && (x.checks || []).length)
const audits = picks.map(x => x.dimension)
const checksFor = Object.fromEntries(picks.map(x => [x.dimension, x.checks]))

if (!audits.length) {
  log(`${surface.label}: no audit check applies to this surface`)
  return { scope, label: surface.label, status: 'no-audit-applies', findings: [] }
}
log(`Selected: ${picks.map(x => `${x.dimension}[${x.checks.join(',')}]`).join(', ')}`)

phase('Audit')

const fileList = surface.files.join('\n')
const scopeFraming = surface.diffCmd
  ? `Read the diff yourself with: \`${surface.diffCmd}\` (add specific paths to narrow it). ` +
    `Scope: only code introduced or modified by this diff. Where a check's own Detect step says to run full-repo, do so, ` +
    `but only report a finding when the diff is what makes it wrong or newly wrong. ` +
    `Read the surrounding files for context — a hunk alone is not enough to judge.`
  : `There is NO diff — this is a whole-surface pass: treat every file below as in scope. ` +
    `Run every selected check against these files, following each check's own Detect step; where a check says to look full-repo, do so.` +
    (surface.summary ? `\n\nSurface summary: ${surface.summary}` : '')

const results = await pipeline(
  audits,
  (auditName) => agent(
    `Apply ONLY these checks (ids ${checksFor[auditName].join(', ')}) from .claude/skills/audit/dimensions/${auditName}/CHECKLIST.md (with .claude/skills/audit/dimensions/${auditName}/examples.md for calibration, if it exists) to ${surface.label}. Ignore the dimension's other checks.\n\n` +
    (params.notes ? `IMPORTANT context: ${params.notes}\n\n` : '') +
    `${scopeFraming}\n\nFiles:\n${fileList}\n\n` +
    `The standards are docs/standards/** and CLAUDE.md. ${standardsSource}` +
    `A violation listed as a known exception in docs/standards/architecture.md §11 is not a finding. ` +
    `Never read, cite or audit anything under docs/superpowers/ (local untracked history). ` +
    `Do NOT modify anything — this is a read-only audit. Report only real violations of the cited standards, ` +
    `each with an exact file:line and the standards section it violates. Prefix each finding id with its check id (e.g. "PB1:").`,
    { phase: 'Audit', label: `audit:${auditName}`, schema: FINDINGS_SCHEMA },
  ),
  (auditResult, auditName) => parallel(
    (auditResult?.findings || []).map(finding => () =>
      agent(
        `Adversarially verify one audit finding from the ${auditName} audit (.claude/skills/audit/dimensions/${auditName}/CHECKLIST.md). Try hard to REFUTE it: ` +
        (params.notes ? `Context: ${params.notes} ` : '') +
        `read the actual current code at ${finding.file}${finding.line ? ':' + finding.line : ''} on the checked-out tree and its callers/context, ` +
        `check the check's own carve-outs and examples.md, check the known exceptions in docs/standards/architecture.md §11, ${standardsSource}` +
        `and check whether a later commit already fixed it. Never treat anything under docs/superpowers/ as evidence. Do NOT modify anything. Finding:\n\n` +
        `[${finding.severity}] ${finding.what}\nStandard: ${finding.standard || 'n/a'}\nProposed fix: ${finding.fix}\n\n` +
        `holds=true only if the violation is real against the CURRENT code on this branch.`,
        { phase: 'Verify', label: `verify:${finding.id}`, schema: VERDICT_SCHEMA },
      ).then(verdict => ({ ...finding, dimension: auditName, verdict }))
    )
  ),
)

const SEVERITY_ORDER = { critical: 0, major: 1, moderate: 2, minor: 3 }
const survivors = results.flat().filter(Boolean).filter(f => f.verdict?.holds)
survivors.sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9))
log(`${survivors.length} finding(s) survived adversarial verification`)

const findingBlock = f =>
  `### ${f.id} [${f.severity}] (${f.dimension})\n` +
  `**Where:** ${f.file}${f.line ? ':' + f.line : ''}\n` +
  `**What:** ${f.what}\n` +
  `**Standard:** ${f.standard || 'n/a'}\n` +
  `**Fix:** ${f.fix}\n` +
  `**Verified:** ${f.verdict.reasoning}\n`

const report =
  `# Audit — ${surface.label}\n\n` +
  `Report-only: no code was changed.\n\n` +
  `${survivors.length} verified finding(s) across ${audits.length} dimension(s): ${audits.join(', ')}.\n\n` +
  survivors.map(findingBlock).join('\n')

const base = { scope, label: surface.label, report, findings: survivors, bundles: null, fixed: null }

if (!survivors.length) return { ...base, status: 'clean' }
if (params.bundle === false) return { ...base, status: 'reported' }

phase('Bundle')
const bundling = await agent(
  `Group these verified audit findings for ${surface.label} into proposed PR bundles. Nothing will be fixed now; the bundles are a proposal for a human. Rules: ` +
  `each major finding that changes a frozen contract (docs/standards/architecture.md §10) gets its OWN bundle; ` +
  `group layering findings by the docs/standards/architecture.md §6.5 move that removes them; ` +
  `group docstring and citation findings together, test-quality findings together, and duplicated-definition findings by the one home they converge on. ` +
  `Aim for 2-6 bundles total; every finding id appears in exactly one bundle. Do NOT modify anything. Findings:\n\n${survivors.map(findingBlock).join('\n')}`,
  { phase: 'Bundle', label: 'bundle', schema: BUNDLE_SCHEMA },
)
log(`${bundling.bundles.length} proposed bundle(s)`)

return { ...base, status: 'reported', bundles: bundling.bundles }
