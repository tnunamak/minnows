// slopgate — MUST-FLAG / MUST-PASS decoy fixtures, slopkit decoy_rejection.py style.
//
// Every MUST-FLAG row states which finding(s) must survive gate() and why.
// Every MUST-PASS row is real or realistic prose that must NOT flag —
// including the documented false-positive-harm categories: RFC/spec
// normative language, terse honest status reports, and non-native-English
// technical writing. A suite that can't reject every decoy, or that flags
// any control, fails the gate — see run.mjs.

export const MUST_FLAG = [
  {
    id: 'paradigm-leverage-seamless',
    text: 'Our paradigm-shifting platform leverages cutting-edge AI to seamlessly integrate with your workflow.',
    mustRules: ['tier1'],
  },
  {
    id: 'in-the-world-of',
    text: 'In the world of modern web development, choosing the right framework can feel overwhelming, but our tool simplifies every step of that decision for engineering teams.',
    mustRules: ['in-the-world-of'],
  },
  {
    id: 'not-just-but',
    text: 'This library is not just a formatter, it is a complete philosophy for how your team should think about code style across every repository you maintain.',
    mustRules: ['not-just-x-but-y'],
  },
  {
    id: 'isnt-just-em-dash',
    text: "This release isn't just faster — it's the foundation every future performance improvement in the product will build on for years to come.",
    mustRules: ['not-just-x-but-y'],
  },
  {
    id: 'wasnt-just-period',
    text: "That outage wasn't just a bug. It was a wake-up call for how the whole team thought about on-call ownership going forward.",
    mustRules: ['not-just-x-but-y'],
  },
  {
    id: 'empowers-to',
    text: 'This dashboard empowers your team to make data-driven decisions faster than ever before, across every department in the organization.',
    mustRules: ['empowers-to'],
  },
  {
    id: 'unlock-possibilities',
    text: 'By integrating our SDK, you unlock new possibilities for your application that were previously out of reach for teams without a dedicated platform group.',
    mustRules: ['unlock-possibilities'],
  },
  {
    id: 'testament',
    text: 'The fact that this service has run for five years without an outage stands as a testament to the engineering discipline of the team that built it from scratch.',
    mustRules: ['stands-testament'],
  },
  {
    id: 'game-changer',
    text: 'This new caching layer is a total game-changer for how we think about read latency across the entire fleet of production services we operate today.',
    mustRules: ['game-changer'],
  },
  {
    id: 'whether-beginner-expert',
    text: "Whether you're a beginner or an expert, this guide will walk you through everything you need to know to get productive with the framework in under an hour.",
    mustRules: ['whether-x-or-y-rule'],
  },
  {
    id: 'not-about-about',
    text: "It's not about writing more tests, it's about writing the RIGHT tests that actually catch the regressions your team cares about before they reach production.",
    mustRules: ['its-not-about-its-about'],
  },
  {
    id: 'not-about-period-dropped-about',
    text: "It's not about speed. It's trust, and trust is the thing that actually keeps engineers coming back to a tool after the initial excitement wears off.",
    mustRules: ['its-not-about-its-about'],
  },
  {
    id: 'adjective-stack-solution',
    text: 'We built a fast, reliable, and scalable solution that handles every workload your team throws at it without any manual tuning required from your engineers.',
    mustRules: ['triple-adjective-stack'],
  },
  {
    id: 'dense-ai-vocab-cluster',
    text: 'To truly harness the power of this robust and comprehensive framework, developers must delve into its innovative architecture, unlocking a seamless and transformative experience across the entire product surface, from onboarding to deployment.',
    mustRules: ['tier1'],
  },
  {
    id: 'unearned-name-oneoff',
    text: 'The Coherence Layer is the beating heart of this design, sitting quietly between every request and every response, coordinating state so nothing downstream ever contradicts itself.',
    mustRules: ['UNEARNED-NAME'],
    needsRepoRoot: true,
  },
  {
    id: 'hedge-stack',
    text: 'This approach could potentially eventually improve throughput for teams running large fleets of workers, though the gains have not been benchmarked yet on production traffic.',
    mustRules: ['hedge-stack'],
  },
  {
    id: 'future-narrative-closer',
    text: 'This technology may become one of the most important developments of the next decade for cloud infrastructure teams, reshaping how every company thinks about compute.',
    mustRules: ['future-narrative'],
  },
  // ADVERSARIAL: real AI-cadence slop with ZERO banned vocabulary words —
  // this is the hard case cadence exists to catch, since a wordlist-only
  // gate is structurally blind to it. Six near-identical-length sentences,
  // each opening the same way, saying nothing concrete.
  {
    id: 'cadence-uniform-no-vocab',
    text: 'This tool helps your team work better. This tool helps your team move faster. This tool helps your team stay aligned. This tool helps your team reduce friction. This tool helps your team ship more often. This tool helps your team feel confident.',
    mustRules: ['cadence-repeated-start'],
  },
  // ADVERSARIAL: bland_clean_sentence alone, no vocabulary, no cadence
  // repetition — a single ungrounded confident sentence with two bland terms.
  {
    id: 'bland-clean-sentence-alone',
    text: 'This process improves outcomes for people and creates real value for every team involved in the work. The rest of this document covers setup instructions in detail.',
    mustRules: ['bland-clean-sentence'],
  },
  // ADVERSARIAL: tool-artifact leftover, the kind of thing that survives
  // careless copy-paste from an AI tool's own citation UI.
  {
    id: 'tool-artifact-leak',
    text: 'The refund policy was updated last quarter to cover partial shipments turn0search3 and now applies retroactively to orders placed after March.',
    mustRules: ['chatgpt-citation-marker'],
  },
];

export const MUST_PASS = [
  {
    id: 'rfc-normative-must',
    text: 'A server MUST NOT reuse a previously issued nonce for a different client request. If a duplicate nonce is received, the server MUST reject the request with a 409 status and SHOULD log the collision for audit purposes.',
  },
  {
    id: 'rfc-normative-may',
    text: 'Implementations MAY cache the resolved value for up to the TTL advertised in the response header. A cached value that has exceeded its TTL MUST be treated as stale and MUST NOT be served without revalidation.',
  },
  {
    id: 'terse-status-report',
    text: 'Fixed the null pointer in the retry handler. Root cause was a missing check after the connection pool returned a closed socket. Added a test that reproduces the crash and verifies the fix. All 412 existing tests still pass.',
  },
  {
    id: 'terse-status-report-2',
    text: 'Ran the migration against a staging snapshot of prod. Took 6 minutes for 2.1M rows. No errors. Rolled back and re-ran to confirm idempotency. Ready to schedule against prod during the next maintenance window.',
  },
  {
    id: 'non-native-english-1',
    text: 'The system is checking the input before is saving to database. If input have wrong format, the error is throw and user must to correct before continue. This is important because many user are not careful with the format.',
  },
  {
    id: 'non-native-english-2',
    text: 'We are testing this function since two week, and result is showing that memory usage is going up slowly but is not stopping. Maybe is memory leak in the loop where we open file but not always closing it properly.',
  },
  {
    id: 'plain-architecture-doc',
    text: 'The worker pool has four goroutines, each pulling jobs from a shared channel. A job that panics is caught by a deferred recover in the worker loop and logged with its stack trace; the worker then continues pulling from the channel.',
  },
  {
    id: 'plain-changelog-entry',
    text: 'Added support for reading configuration from environment variables in addition to the config file. Environment variables take precedence when both are set. Existing config files continue to work without changes.',
  },
  {
    id: 'technical-heading-title-case',
    text: '## Database Connection Pooling\n\nThe connection pool is sized based on the number of CPU cores available at startup. Each connection is validated with a lightweight ping before being handed to a caller.',
  },
  {
    id: 'enumeration-three-real-nouns',
    text: 'The parser produces a token stream, an abstract syntax tree, and a symbol table. Each of these three artifacts is consumed independently by a later compiler pass.',
  },
  {
    id: 'security-advisory-plain',
    text: 'A path traversal vulnerability was found in the file upload handler. An attacker who controls the filename parameter can write files outside the intended upload directory. Upgrade to version 4.2.1, which validates the resolved path against the upload root before writing.',
  },
  {
    id: 'personal-note-informal',
    text: "Spent the morning debugging the flaky test. Turned out the test was asserting on wall-clock time, which is just never going to be reliable in CI. Switched it to a fake clock and it's been green for 40 runs in a row now.",
  },
  {
    id: 'design-tradeoff-discussion',
    text: 'We considered using a queue here instead of a direct call, but decided against it: the added latency and operational complexity were not worth it for a code path that runs at most once per user per day.',
  },
  {
    id: 'contains-real-url-and-path',
    text: 'See the handler at src/api/upload.go for the current validation logic, and https://example.com/docs/uploads for the public documentation of the expected request shape.',
  },
  {
    id: 'code-block-with-slop-words-inside',
    text: 'Here is the old implementation for reference:\n\n```js\n// This paradigm-shifting, cutting-edge function leverages seamless integration.\nfunction leverage() { return true; }\n```\n\nThe function above is dead code and should be deleted in the next cleanup pass.',
  },
  // HARD CONTROL: legitimate terse status updates that happen to be similar
  // length and share an opening word — stress-tests cadence rules against
  // the exact shape of an honest, repetitive-by-necessity status report.
  {
    id: 'terse-repetitive-status-list',
    text: 'Ran migration 041 against staging. Ran migration 042 against staging. Ran migration 043 against staging. Ran the full test suite after each one and confirmed zero failures before promoting to the next step.',
  },
  // HARD CONTROL: dense real technical jargon with real anchors (numbers,
  // named entities, load-bearing words) — the case a wordlist would
  // over-flag but cadence/bland-clean should correctly pass since every
  // sentence has a concrete factual anchor.
  {
    id: 'dense-jargon-with-anchors',
    text: 'The Raft leader election timeout is set to 150-300ms with jitter to avoid split votes. When node 3 lost quorum during the 2026-07-14 incident, the cluster took 4.2 seconds to elect a new leader because two followers had clocks drifted by 40ms beyond the configured threshold. This is why the runbook now requires NTP sync verification before any node restart.',
  },
  // HARD CONTROL: non-native English WITH repeated sentence structure — the
  // exact combination that would trip a naive cadence rule unfairly if
  // cadence measured length uniformity without regard for what a normal
  // non-native writer's sentence-length distribution actually looks like.
  {
    id: 'non-native-repetitive-structure',
    text: 'First we are checking if user is logged in. Then we are checking if user have permission. Then we are checking if the resource still exist. If any check is failing, we are returning error message to frontend so user can understand what happen and try again later with correct data.',
  },
  // HARD CONTROL: a real RFC-style enumeration that is naturally uniform in
  // length because each item follows the same MUST/SHOULD grammatical
  // template — legitimate normative repetition, not AI cadence.
  {
    id: 'rfc-enumeration-uniform',
    text: 'Clients MUST validate the signature before use. Clients MUST reject any token missing the issuer claim. Clients MUST reject any token with an expired timestamp. Clients SHOULD cache validated tokens for their remaining lifetime only.',
  },
  // HARD CONTROL: "isn't" and a plain factual continuation with "it" that
  // is NOT the antithesis frame — the broadened not-just-x-but-y regex must
  // not fire on ordinary negation followed by an unrelated "it" clause.
  {
    id: 'isnt-just-unrelated-it-clause',
    text: "The staging database isn't just slower than prod, it runs on a single replica with half the memory, which the team already flagged as a known gap in the infra backlog.",
  },
];
