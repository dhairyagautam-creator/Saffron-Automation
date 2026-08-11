# Saffron Automation v2.0.0 — Production Release

**Release date:** 2026-07-27

## Highlights

- **Full cloud synchronization** across all three modules — Path Validator, Inventory Monitoring, and Payment Analytics — backed by Supabase. Every module now has a manual Refresh/Sync action; Path Validator additionally polls automatically every 15 seconds.
- **One consistent sync rule everywhere:** Last-Modified-Wins. Every Refresh compares each record's local and cloud copy by timestamp and keeps whichever is newer — the same rule, in one shared service, used identically by every module. No more per-module sync quirks.
- **Centralized reporting hierarchy.** Organization Data now shows a single **Senior** column — the correct final escalation contact for every employee, resolved through the full BM → ABM → RBM → Senior RBM → SM → AGM → GM fallback chain. Email notifications route to the exact same person shown in that column; there is no longer a separate, inconsistent fallback path for emails.
- Fixed a data-entry bug where an employee under a vacant ABM/RBM position could incorrectly inherit the wrong manager instead of correctly escalating to the next level up.
- Fixed a sync bug where Path Validator's findings and email-notification history could fail to push to the cloud during Refresh.

## Upgrade notes

- Existing installations upgrade in place — same install location, same Add/Remove Programs entry.
- All local settings, uploaded data, and configuration are preserved automatically (stored separately from the application files, unaffected by this or any future update).
- No manual migration steps are required.

## Known limitations

- The Last-Modified-Wins sync rule does not propagate deletions: a record removed locally can reappear on the next Refresh if its cloud copy still exists. Expected to be revisited if it causes issues in practice.
