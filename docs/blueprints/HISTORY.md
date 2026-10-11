# Blueprints: what was built, and when

Blueprints' completed work, in order. How it works: [`ARCHITECTURE.md`](ARCHITECTURE.md). Commit messages have the
detail.

## Where things stand (2026-10-09)

- Live at `blueprints.naveenlabs.tech`: library, patterns, Finder, compare, runnable starters, custom blueprints,
  lint API, Architect agent.
- Nightly generation is **paused** (2026-10-11): 3 generated drafts wait unreviewed in `/admin`; the Architect's caps are
  $0.20 per design and $0.25 per day.
- About 710 tests; library evaluation at 1.00.

## Timeline

| Date | Work |
|---|---|
| 2026-09-30 | Designed: linted, priced reference architectures where code decides and a model only drafts |
| 2026-10-01 | Engine: schema, decision engine, linter, cost model, diagrams, owner approval, living links to tool grades and package data, generation with lint-driven revisions, 4 seeds and 16 briefs. Site: gallery, blueprint pages with cost sliders, patterns, Finder, lint API, admin draft preview |
| 2026-10-02 | v1.1: compare, runnable LangGraph starters checked against published API data, bring-your-own-key custom blueprints. Hardening sweep (linter warnings, NaN bodies, rate limits before validation). Became its own repository |
| 2026-10-03 | Deployed; product design with light and dark themes; hardened container and headers |
| 2026-10-07 | "Design canvas" identity: gallery home with filters in the hero, topology thumbnails, flow canvas on each blueprint. Architect agent: clarify, ground, draft, record and runbook, check-and-fix loop, one paid review that also red-teams; launch gate 8/8; switched on. Salted visitor keys; compact question cards |
| 2026-10-08 | Owner controls (Architect switch and limits, batch cap, weekly brake, stop-all) and the private control interface; generation moved to OpenRouter with a daily cap; rate-limited calls retry in minutes |
| 2026-10-09 | Own architecture and history documents |
| 2026-10-11 | Nightly generation paused from the owner's console until the waiting drafts are reviewed; the private report lists recent runs and batch jobs (metadata only) for the console |

## Open items

- Review the 3 generated drafts in `/admin` (publish the good ones), then decide whether to resume nightly generation.
- Pattern catalogue entries stay drafts until marked `reviewed`.
- Deferred minors: a volume of 0 shows "$0/mo"; loose spacing on the questions page.
