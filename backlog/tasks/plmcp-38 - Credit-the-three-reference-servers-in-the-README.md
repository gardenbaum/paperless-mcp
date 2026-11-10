---
id: PLMCP-38
title: Credit the three reference servers in the README
status: Done
assignee:
  - '@claude'
created_date: '2026-11-10T21:46:00Z'
updated_date: '2026-11-10T21:47:00Z'
labels: []
dependencies: []
type: docs
ordinal: 38000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The design (doc-1 §4) uses pvliesdonk/paperless-mcp, baruchiro/paperless-mcp and OrellBuehler/paperless-mcp as references and adopts ideas from each. Credit them in the README with links and what was taken from each.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 README has a Credits section linking all three repositories
- [x] #2 Each entry names the ideas adopted from that server, consistent with doc-1 §4
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a Credits section to the README linking pvliesdonk, baruchiro and OrellBuehler paperless-mcp with licences and the ideas adopted from each (doc-1 §4).
<!-- SECTION:FINAL_SUMMARY:END -->
