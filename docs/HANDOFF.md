# HANDOFF — Saffron Automation v2.1

**Current release: v3.0.0** (2026-09-23) — see [CHANGELOG.md](../CHANGELOG.md). Updated by every
release per [RELEASING.md](../RELEASING.md); the body below this line was last caught up on
2026-09-11 and is not re-verified on every release, so treat older sections as historical context,
not current state, unless RELEASING.md's most recent pass touched them.

State-of-the-world for a fresh session with no memory of how it got here. Written 2026-09-11.
Every claim below was checked against live `git status`/`git log`/code at time of writing, except
where marked as a conversation-only decision (Section 3) — those are real product decisions with
no other written record; treat them as authoritative anyway.

---

## 1. Git state — exact, not aspirational

Branch: `development` (renamed from `review-system-sync-slice`; tracks `origin/review-system-sync-slice`,
**not** `origin/main`). `development` has never been merged into `main` — `git log origin/main..development`
returns nothing because `origin/main` doesn't have this branch's history at all yet; this is all still
on a feature branch. Nothing here has been pushed beyond what `origin/review-system-sync-slice` already
had before this work.

**Both email authority phases are now genuinely committed** (2026-09-11), as two separate commits on top
of `b441770` "Pin pandas to 3.0.5":

- `cb51072` — "Email authority Phase 1: manual Send Emails buttons for Path Validator, Inventory, Work
  Distribution". RBAC foundation (`app/permissions.py`), the shared button/dialog widgets
  (`ui/components.py`'s `SendEmailsButton`, `ui/send_emails_dialog.py`), all three modules' buttons and
  automatic-send removals, `tests/test_permissions.py` + one `send_button_state()` test per module.
- `ceac753` — "Email authority Phase 2: shared cross-machine send history". `email_send_history` service
  + migration, `ModuleDataVersion` counter + its hooks, all three modules' state logic rewritten to query
  shared history, the Work Distribution `upload_log` correction, `app/profile_names_service.py`'s
  extraction, `tests/test_email_send_history_service.py`.

One caveat worth knowing if you ever bisect this branch: Commit `cb51072` alone does not import cleanly
standalone — it already references `app.email_send_history_service` (for each module's "already sent"
check and status line), which isn't added until `ceac753`. Both phases were built and verified together in
one working tree before either was committed, so there was no clean historical split point that didn't
require reconstructing unverified code; the commit message says this explicitly. Not a concern for a
private, unpushed checkpoint — would matter if this branch is ever rebased/bisected before merge.

**`ui/login_page.py`'s fix now has its own commit**, `ce4e807` — a real, pre-existing fix (the
login-startup-check race condition, deferred via `self.after(0, ...)`) that predates this work entirely
and was deliberately kept out of both email-authority commits above. `docs/HANDOFF.md` (this file) is
committed too, in the commit right after this sentence was written — it's meant to survive as the
snapshot a new session reads first, so it doesn't stay a loose untracked file.

**What else is committed and merged nowhere yet (also feature-branch-only):** `137f13e` "Review System sync
slice" — the Review System's cross-machine sync (Storage bucket + `sync_manifest` table). See Section 4.

Supabase migration `0025_email_send_history.sql` **has been applied** to the live project (user ran it in
the SQL Editor after two pre-flight checks — see Section 2/Section 7). The migration file is now committed
in `ceac753` as well.

---

## 2. Email authority — Phase 1 and Phase 2

**Phase 1** replaced automatic email sending with a manual "Send Emails" button, one per module (Path
Validator, Inventory, Work Distribution), each with three states (no data / new data / resend-confirm-if-unchanged)
driven by a shared `SendEmailsButton` + `SendEmailsDialog` (confirm → live progress → completion). It added
a real RBAC layer for this specifically: `can_send_emails()` plus a per-module `<module>:email_authority`
sub-permission, editable as sub-checkboxes under each module's existing checkbox in User Management. All
automatic-sending code paths, settings toggles, and dead UI (old Progress Center stages, Work Distribution's
old Notifications card/Send Log) were removed rather than left dormant. Real sends were tested against real
data with strict safety rules (every email redirected to `dhairyagautam@andrewsosborne.com`, either via an
SMTP-layer monkeypatch for hierarchy-resolved recipients or a literal temp-row override/restore for
config-driven recipient lists) and verified restored afterward both times.

**Phase 2** replaced each module's local, per-machine "have I already sent this?" check with a shared
Supabase table (`email_send_history`: module, data_version, sent_by, sent_at, append-only, RLS `using(true)`
for any authenticated user) so the answer to "was this already sent, by whom, when" is the same regardless
of which of the ~6 users' machines is asking — plus an always-visible "Last sent Xh ago by Y" status line
under the button (a static line refreshed on page show, not a live timer). It also introduced a
per-module local counter (`ModuleDataVersion`/`module_data_version_service.py`) as Inventory's and Work
Distribution's stand-in for "has the data changed," and along the way fixed a real Phase 1 bug: Work
Distribution's "new data" check was comparing the wrong table (see Section 3). End-to-end verification with
real sends and a real cross-machine read is complete and clean — see Section 7.

---

## 3. Decisions made in conversation — not written anywhere else

**Why `data_version` is a standalone local counter, not the sync manifest's `seq`.** The sync manifest
(`sync_manifest.seq`, Section 4) is the one thing that's genuinely comparable across machines today — but
it only exists for the Review System. Inventory and Work Distribution don't sync their underlying business
data at all yet (Section 4), so there is no cross-machine-comparable version number to reuse for them.
`ModuleDataVersion` is a deliberate, explicitly-documented stand-in: a plain per-machine integer, bumped by
1 every time that module's data actually changes (right after the upload-processing commit, in
`replenishment_service.py`, `work_distribution_service.py`, `manager_work_allocation_service.py`,
`manager_work_allocation_rbm_service.py`). It is NOT a claim that two machines' counters mean the same
thing — they don't, if two different machines both upload independently. That gap is accepted, not solved
(next point).

**The cross-machine send-detection gap is accepted, not a bug.** Because `data_version` isn't
cross-machine-comparable for Inventory/Work Distribution, a machine that uploads fresh data right after a
different machine already sent for a numerically-different-but-locally-equal version could get a
misleading "no new data" or "new data" reading. This was surfaced explicitly, and the decision — made in
conversation, confirmed by the user — is to ship it anyway: the existing resend-confirmation dialog (Phase
1) is the accepted safety net for the false-positive-toward-caution direction, and the established
one-designated-uploader-per-module convention (already true before this work) is the real mitigation. Do
not build a second confirmation layer for this; it was explicitly declined as unnecessary.

**Review System is excluded from email authority entirely — not an oversight.** `docs/EMAIL_AUTHORITY_CONTEXT.md`
§1/§5 documents why this needed a decision rather than a default: Review System's only email trigger is
scoped to the Coverage Summary report specifically (not the module as a whole — Opus Summary and RGD
Visit/Support have no email workflow at all), fires on report *generation*, not upload, and already has its
own manual "Send Emails Now" button + per-report auto-send toggle predating this work. Whether a new
module-top button should replace, coexist with, or relocate that existing button — and what "send" even
means when it's scoped to one report type out of three — was left as an open question for the user to
decide later. It was deliberately not bundled into Phase 1/2.

**Payment Analytics has no send capability, and that's fine.** `docs/EMAIL_AUTHORITY_CONTEXT.md` §5 confirms
there is no existing send function, recipient table, or settings service for this module at all — nothing
for a "manual button instead of automatic" redesign to attach to. The decision was not to build a whole new
email system for Payment Analytics as a side effect of this work; its Send Emails button (if/when it gets
one) stays permanently disabled until building that from scratch is explicitly scoped as its own task.

---

## 4. Sync system state — per module, exactly

| Module | Sync status |
|---|---|
| **Review System** | Built (`137f13e`, uncommitted-to-main, see Section 1) and verified live in this project's own testing: Storage bucket (`sync-uploads`) + append-only `sync_manifest` table (module/slot_key/seq/sha256), polled by every client, each upload downloaded, hash-verified, then applied locally exactly as if uploaded by hand. **NOT merged to `main`.** The 12 real files currently sitting in `review_file_slots`/`review_uploads/` on this machine have **not been re-uploaded** through the new sync path to seed Storage — until that happens, a second machine polling the manifest finds nothing to pull, even though this machine's local slots are populated. That re-upload is a prerequisite, not done. |
| **Inventory** | Zero sync work. No Storage bucket, no manifest rows, nothing. Does not even retain its own source files locally today (`docs/SYNC_CONTEXT.md` §11 — full-replace-then-rebuild, no "retains source?" column ticked) — retaining source files is itself a prerequisite for any future sync design here, before sync logic can even be designed. |
| **Payment Analytics** | Same as Inventory — zero sync work, doesn't retain source files. |
| **Work Distribution** | Same as Inventory — zero sync work; also never had a cloud schema at all, historically (`docs/SYNC_CONTEXT.md` §1/§8) — no prior sync to build from even as precedent. |
| **Path Validator** | Same as Inventory — zero sync work, doesn't retain source files. |

`docs/SYNC_CONTEXT.md` (pre-dates the Review System sync build — written when the old cloud sync layer had
just been fully removed and nothing new existed yet) is still the accurate general-architecture reference
for the other four modules' current state; don't read its Review-System sections as current, they describe
the "before" state.

---

## 5. Unresolved open problems — not solved, listed plainly

- **Historical replay gap.** `manager_work_allocation_records` and `payment_invoices` have no retained
  source files behind their current rows (`docs/SYNC_CONTEXT.md` §5/§11 — "Recomputable: No" /
  "Retains source? No"). If either table's data is ever lost or needs to be rebuilt/re-synced from scratch,
  there is nothing to replay from except re-uploading the original report by hand, if it can still be
  found outside the app. This is a real gap in any future sync/recovery design, not yet addressed.
- **Findings identity isn't stable across machines.** `investigation_findings` uses a local SQLite
  autoincrement integer as its only identity (`docs/SYNC_CONTEXT.md` §9) — two machines independently
  creating findings would each hand out the same IDs to different rows. No natural key, hash, or
  UUID exists for this table. Any cross-machine findings sync needs an identity scheme that doesn't
  exist yet.
- **The armed factory-reset flag.** `app/inventory_factory_reset.py` — a one-time, company-wide Inventory
  data wipe (`InventoryThreshold`/`InventoryReplenishment`/`CwhStock`), gated behind `sys.frozen` so it can
  only ever fire in a packaged build, never `python main.py` from source, and gated to run exactly once via
  `AppSettings.inventory_data_reset_completed`. As of this writing it has **never actually fired** on a real
  installation — the marker column didn't even exist in this repo's working database until this guard was
  added. It is armed and will fire automatically the next time a packaged build is installed/updated and
  launched, wiping that machine's Inventory data once. Anyone cutting a new release needs to know this is
  live and intentional, not leftover debug code.

---

## 6. macOS port

**Correction to an earlier version of this section**, which claimed "zero code written" — that was
wrong even when it was written: a working build pipeline already existed at that point (`ee2296c
Release v2.4.0: ... macOS signing`, committed *before* this doc), this section just never mentioned it.
Don't trust a doc's claim of "nothing exists yet" over what's actually in the tree — check first.

**What actually exists, arm64-only, target confirmed Apple Silicon:**
- `.github/workflows/build.yml`'s `macos` job — `runs-on: macos-latest` (confirmed arm64-only as of
  Sept 2026; GitHub dropped Intel from the default macOS image). Runs: checkout → Python 3.12 → write
  `.env` from secrets → **pytest → headless smoke test (`python main.py --smoke-test`, gated on it
  passing)** → optional signing-cert import → `build_app.sh` → upload `.dmg` + build/test logs as
  artifacts (`if: always()`, so failed runs still leave something to inspect).
- `Saffron Automation-mac.spec` — a separate PyInstaller spec producing a real `.app` bundle (onedir:
  `EXE(exclude_binaries=True)` + `COLLECT()` + `BUNDLE()`), `.icns` icon, `Info.plist`. Kept intentionally
  separate from the Windows spec so the Windows build stays byte-for-byte unchanged; see that file's
  header comment for the full list of what differs (upx, icon format, macOS keyring hidden-import,
  the `BUNDLE()` stanza, and — as of this pass — not bundling `.env`, see below).
- `build_app.sh` — venv, PyInstaller build, `codesign --deep --options runtime` against
  `entitlements.plist`, packages into a drag-to-Applications `.dmg` via `hdiutil`, optionally notarizes
  via `notarytool`/`stapler` if Apple credentials are present.
- **Notarization/signing:** already wired as an optional no-op — `SIGN_IDENTITY` defaults to `-`
  (ad-hoc) and notarization is skipped with a warning when Apple credentials aren't set (`build_app.sh`).
  This already satisfies a $0 budget; nothing needed removing, it just needs the secrets left unset.
- **`.env`:** was bundled directly into the `.app` (`datas=[('.env', '.')]`) exactly like Windows, which
  is wrong for a signed/notarized bundle meant to be read-only. Fixed this pass: `app/platform_paths.py`
  (new, minimal — only handles the macOS data-dir case) points a *frozen* macOS build at
  `~/Library/Application Support/Saffron Automation/.env` instead; source runs and the frozen Windows
  build are unchanged. Consequence: a freshly built `.dmg` has no Supabase credentials on first launch
  on a real Mac until that file is placed there manually — nothing in the codebase writes it at runtime
  yet.
- **Known real gap, not yet fixed:** `app/config.py`'s frozen-build `DATA_DIR` resolution has no macOS
  branch — on a frozen mac build it falls through to `Path.home() / "Saffron Validator"` rather than the
  `~/Library/Application Support/...` convention. Folding this (and the rest of `app/config.py`'s
  Windows-specific logic) into `platform_paths.py` is a separate, larger pass — not done here.
- **Still gapped as of this doc:** whatever this pass's own report to the user says is still open
  (e.g. the CI trigger scope) — check that conversation/PR, not this paragraph, for the current state.

---

## 7. Phase 2 real end-to-end verification — actual result

Run 2026-09-11, real Supabase project, real SMTP, redirected to `dhairyagautam@andrewsosborne.com` per the
standing safety rule (SMTP-layer redirect for Path Validator/Work Distribution's hierarchy-resolved
recipients; literal temp-row override/restore for Path Validator's master recipient and Inventory's
config-driven recipient, both confirmed restored to empty afterward). Sender identity: the real app's own
live keyring session (`dhairyagautam@andrewsosborne.com`, super admin) via `auth_service.restore_session()`
— the two scratchpad session files saved during earlier multi-machine testing had their refresh tokens
rotated/invalidated by real subsequent app usage (confirmed via a live `400 Invalid Refresh Token` from
Supabase, not assumed) and couldn't be reused without a password, which per this project's own
`sign_in_for_test.py` is deliberately never something to type through an agent.

Cross-machine visibility was proven directly: each row was read back in a fresh query against the live
Supabase table, which has no per-machine cache in front of it. Cross-machine **name resolution**
specifically was proven by temporarily swapping the local `current_profile` to a different real profile
(`dhairyagamer07@gmail.com`'s real id) after clearing the local name cache for the sender — forcing
`display_name_for()` off its "it's me" shortcut and through the real cache-refresh-then-lookup path — then
restoring the real profile afterward. This is a legitimate substitute for a second live session (the RLS
read policy is `authenticated`-only, not viewer-specific), not a shortcut on what was actually being tested.

**Result: clean, all three modules.**

| Module | Sent | `email_send_history` row | Cross-machine read | Status line |
|---|---|---|---|---|
| Path Validator | 1 sent, 0 failed (real import_id=40, master recipient temp-added/restored) | `data_version="40"`, `sent_by`=real id, real timestamp | Visible, matches | "Last sent 3m ago by Dhairya Gautam" |
| Inventory | 1 sent, 0 failed (temp recipient added/restored) | `data_version="0"` | Visible, matches | "Last sent 3m ago by Dhairya Gautam" |
| Work Distribution | 3 of 168 real drafts sent, 0 failed (capped per standing test convention, not a full 168-recipient run) | `data_version="0"` | Visible, matches | "Last sent just now by Dhairya Gautam" |

One honest caveat, not glossed over: Inventory's and Work Distribution's `data_version` both read back as
`"0"` — meaning `get_data_version()` had never been bumped above its default on this dev database at test
time. This run proves the write/read/display plumbing end-to-end correctly (insert with the current
counter value, read it back, compare it, render it), but it does **not** by itself prove
`bump_data_version()` actually fires correctly inside a real `evaluate_replenishment()` /
`process_work_distribution_report()` run, because no new report was uploaded during this test to exercise
that path. That hook was verified by direct code reading (Section on file/line citations in the
conversation this doc summarizes), not by a live upload in this run — worth a real upload test before
fully trusting the counter in production.

Full test suite: 378 passed, 14 skipped, 0 failed, as of the last run before this verification.

---

## 8. Next actions, in order

1. **Run a real report upload** for Inventory and/or Work Distribution to confirm `bump_data_version()`
   fires correctly in the actual processing path (Section 7's caveat) — not yet done.
2. **Both email-authority phases, the login race-condition fix, and this file are all committed** on
   `development` (Section 1) — this is still a private, unpushed checkpoint, not merged to `main`, not
   released. Get explicit sign-off before pushing or merging.
3. **Re-upload the Review System's 12 real files** through the new sync path to actually seed Storage
   (Section 4) — until this happens, the sync system has nothing for a second machine to pull, regardless
   of how correct the code is.
4. **Decide Review System's email-authority scope** (Section 3) — Coverage-Summary-only, replace vs.
   coexist with the existing "Send Emails Now" button — before building anything for that module.
5. ~~Answer the Apple Silicon vs. Intel question~~ — resolved: Apple Silicon, confirmed directly by the
   user. See Section 6 for the current state of the macOS build pipeline.
6. Longer-term, unscheduled: address the historical-replay gap and findings-identity-stability problem
   (Section 5) before extending sync to Inventory/Payment/Work Distribution/Path Validator — none of them
   even retain source files yet, which has to come first regardless of the sync design chosen.
