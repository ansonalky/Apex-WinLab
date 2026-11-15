# Submission verification — 2026-11-15

Audited application baseline: `fb45e0747ef62b38ce4975a8f321b89a5a103d0b` on `model-pipeline`, the head of PR #4 before this documentation preparation. The preparation adds documentation only. It does not merge the PR, change models, regenerate predictions, alter the frozen evaluation, change commit dates or remove history.

## Verification

- Tested a clean `git archive` of the committed baseline in a temporary directory, with access to the existing local `data/` artifacts. Uncommitted application changes were not included. Database and provider credentials were removed from the test environment.
- Python: **299 tests run: 298 passed, one skipped**, via `.venv/bin/python -m unittest discover -s tests -q` in that snapshot. Expected mocked error logs are not live connection/provider failures.
- Frontend: **all nine committed `*_regression.cjs` suites passed** against that snapshot using a Node VM runner. Coverage includes review modes/locking, cancellation/late responses/Retry, key-moment navigation, exact timeline seeking/dragging, ring rendering/observer races, tactical synchronization and URL-import errors. No paid review, Overstat request, database import or real-data training was run for this verification.
- Read-only checks of the existing dashboard on loopback port 5050 reported Tiger Data, 100 catalog matches and Gemini configured via `GEMINI_API_KEY`. No test-preview response header was present. This was a configuration/catalog check, not a new live coaching or browser walkthrough.
- SHA-256 checks confirmed **15 existing model, export and frozen-evaluation artifacts unchanged** after tests.
- README local documentation links exist; `git diff --check` passed.

## Submission scope and repository hygiene

The pending player-selection, identity-header and timeline-design changes were reviewed as a separate uncommitted diff and left out. They remain available in the shared working tree. Unfinished remote-access changes in `app.py`, `coach.py`, `requirements.txt`, `remote_access.py`, its tests and its guide are also excluded. Do not stage the entire working tree to prepare this submission.

The committed tree was checked for key/token/private-key patterns, credential URLs, large artifacts and paths associated with telemetry, caches, logs, databases and secrets. Detected credential URLs were the placeholder `.env.example` and synthetic test inputs; no real credential was identified in the submission tree. Downloaded telemetry and local agent runtime files are ignored. The intentionally tracked fixture server is separate from the application launch, prominently labeled, and tested not to affect the real application. Pattern scanning is not a comprehensive security audit.

Separate local `refs/agents/.../checkpoints/...` refs retain agent state, including secret-key/log/database paths. Those checkpoint commits are **not ancestors of this submission branch** and are not included in PR #4. They were not pushed or rewritten by this preparation; do not push all local refs.

## Before merging/submitting

- Provide judges the ignored replay/model/export artifacts and, for the full demo, access to an already-populated Tiger database through a private handoff. A public source checkout cannot reproduce those artifacts by itself.
- Demo video supplied by the owner: [Watch Apex WinLab](https://youtu.be/3AhgQunru90).
- PR #4 had no reported CI checks at inspection. Local automated checks passed; rehearse the live demo on the intended submission machine and confirm its installed artifact paths. Older walkthrough sections may describe superseded controls; README describes the current committed action.
- Confirm the final PR head contains the documentation preparation and remains the intended version. Merge is intentionally left to the owner.
- The owner explicitly authorized the demo-video documentation commit after the original Sunday noon Pacific cutoff. Preserve its actual commit timestamp; do not backdate commits.
