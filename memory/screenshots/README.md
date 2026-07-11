# Screenshots directory

This directory is reserved for the release-evidence screenshot pack described in `/app/memory/CLINICAL_RELEASE_SCREENSHOT_INDEX.md`.

**Nothing persisted in this container.** The release-gate closeout on 2026-02-15 captured proof-of-life screenshots inline via the automation tool, which streamed the images in the tool response rather than persisting to this directory. See the closeout tool trace for the inline evidence.

**Expected contents (after the authorized capture pass):**

- 25 role/viewport-tagged JPGs (see `CLINICAL_RELEASE_SCREENSHOT_INDEX.md` §Workspace-mode set and §Additional required shots).
- One `APPROVAL.md` file listing reviewer initials per screenshot.

**PHI safeguards:**

- Fixture-only. Riverbend Chiropractic & Wellness demo tenant.
- Redact or blur anything that could identify a real person.
- Never capture production PHI, even accidentally.
- Preview-environment watermark should be marked as environmental artifact.
