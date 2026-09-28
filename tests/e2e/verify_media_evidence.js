#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const reportPath = process.argv[2] || 'test-results/playwright-results.json';
const contractPath = process.argv[3] || '.engineering/e2e.json';
const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));

const journeyTests = {
  'control-plane-status-and-navigation': [/control-plane routes stay in-document and survive refresh and browser history/i],
  'chat-inference-and-recovery': [/a failed inference does not poison the next request/i],
  'model-runtime-management': [/models and runtimes owns the lifecycle, resource recovery and deep-linked detail surface/i],
  'evaluation-review-and-repeatability': [
    /evaluation result uses semantic evidence values and progressively disclosed run identity/i,
    /general-purpose evaluation sends and records reasoning OFF/i,
  ],
};

const modes = new Map(
  (contract.critical_journeys || []).map((journey) => [journey.id, journey.minimum_ui_evidence_mode || 'assertions']),
);

function walkSuites(suites, parents = [], specs = []) {
  for (const suite of suites || []) {
    const next = suite.title ? [...parents, suite.title] : parents;
    for (const spec of suite.specs || []) specs.push({ ...spec, fullTitle: [...next, spec.title].filter(Boolean).join(' ') });
    walkSuites(suite.suites || [], next, specs);
  }
  return specs;
}

function existingAttachment(attachment) {
  if (!attachment?.path) return false;
  if (path.isAbsolute(attachment.path)) return fs.existsSync(attachment.path);
  return [path.resolve(attachment.path), path.resolve(path.dirname(reportPath), attachment.path)].some((candidate) => fs.existsSync(candidate));
}

function pruneToRetainableEvidence(root) {
  if (!fs.existsSync(root)) return;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) {
      pruneToRetainableEvidence(target);
      if (fs.readdirSync(target).length === 0) fs.rmdirSync(target);
      continue;
    }
    const retain = entry.name === 'ui-e2e-media-manifest.json' || entry.name.endsWith('.png') || entry.name.endsWith('.webm');
    if (!retain) fs.unlinkSync(target);
  }
}

const specs = walkSuites(report.suites || []);
const errors = [];
const manifest = {
  schema_version: 2,
  evidence_kind: 'local_llm_server_ui_e2e_risk_evidence_v2',
  report: 'playwright-results.json',
  contract: contractPath,
  journeys: {},
};

for (const [journey, patterns] of Object.entries(journeyTests)) {
  const mode = modes.get(journey);
  if (!['assertions', 'screenshots', 'full_media'].includes(mode)) {
    errors.push(`${journey}: missing/invalid minimum_ui_evidence_mode in E2E contract`);
    continue;
  }
  const matchingSpecs = specs.filter((spec) => patterns.some((pattern) => pattern.test(spec.fullTitle)));
  const passingResults = matchingSpecs.flatMap((spec) =>
    (spec.tests || []).flatMap((test) =>
      (test.results || []).filter((result) => result.status === 'passed').map((result) => ({ title: spec.fullTitle, result })),
    ),
  );
  const screenshots = passingResults.flatMap(({ result }) =>
    (result.attachments || []).filter((attachment) => attachment.contentType === 'image/png' && existingAttachment(attachment)),
  );
  const videos = passingResults.flatMap(({ result }) =>
    (result.attachments || []).filter((attachment) => attachment.contentType?.startsWith('video/') && existingAttachment(attachment)),
  );

  let complete = passingResults.length > 0;
  if (mode === 'screenshots') complete = complete && screenshots.length > 0;
  if (mode === 'full_media') complete = complete && screenshots.length > 0 && videos.length > 0;

  manifest.journeys[journey] = {
    evidence_mode: mode,
    status: complete ? 'PASS' : 'E2E_EVIDENCE_INCOMPLETE',
    mapped_tests: [...new Set(passingResults.map(({ title }) => title))],
    screenshot_count: screenshots.length,
    video_count: videos.length,
  };

  if (!passingResults.length) errors.push(`${journey}: no passing representative UI test`);
  if ((mode === 'screenshots' || mode === 'full_media') && !screenshots.length) errors.push(`${journey}: ${mode} requires screenshot evidence`);
  if (mode === 'full_media' && !videos.length) errors.push(`${journey}: full_media requires complete test video evidence`);
}

fs.mkdirSync('test-results', { recursive: true });
fs.writeFileSync('test-results/ui-e2e-media-manifest.json', `${JSON.stringify(manifest, null, 2)}\n`);

if (errors.length) {
  // Leave the raw report in place only until the next workflow step can create
  // the allow-listed failure manifest and delete all raw Playwright evidence.
  console.error('UI E2E risk-based evidence: FAIL');
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

// Successful runs retain only synthetic media required by the selected mode
// plus the sanitized manifest. Raw JSON reports and trace ZIPs are transient.
pruneToRetainableEvidence('test-results');
console.log('UI E2E risk-based evidence: PASS');
