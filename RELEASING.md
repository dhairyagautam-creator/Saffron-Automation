# Releasing a new version

End-to-end checklist for shipping a Saffron Automation release. This is the authoritative process
— README's "Releasing a new version" section is a short pointer here plus the raw build commands.

## 1. Bump the version

```powershell
python bump_version.py 3.1.0
```

Updates `app/version.py` (`APP_VERSION`, `BUILD_DATE`) and `installer/saffron_validator.iss`
(`MyAppVersion`) together. Also check by hand:
- `CHANNEL` in `app/version.py` and `MyChannel` in the `.iss` file match the release you're
  actually doing (`"production"` or `"development"`) — see
  [UPDATER_README.md](UPDATER_README.md#release-channels-version-20).
- For a Development build, `APP_VERSION` should carry a `-dev.X.Y` suffix; Production should not.

## 2. Draft the changelog

```powershell
python draft_changelog.py
```

Prints a categorized draft of commits since the last tag (sync systems / bug fixes / CI-build /
docs / other). **Review and rewrite it** — it's a starting point, not the final wording — then
add the result to the top of `CHANGELOG.md` under a new `## vX.Y.Z — <date>` heading.

## 3. Update docs/HANDOFF.md

At minimum, update its "Current release" line (version + date) and the CHANGELOG.md pointer at
the top of the file. If anything else in the file has gone stale this release, fix that too —
don't let it silently drift again.

## 4. Verify secrets are actually set (not just present)

See [SECRETS_CHECKLIST.md](SECRETS_CHECKLIST.md) (private, not in the repo). At minimum: a CI run
of `.github/workflows/build.yml` must pass its "Write .env" step, which runs `check_env.py` and
fails the job red if `SUPABASE_URL`/`SUPABASE_ANON_KEY` came out empty. A green build here is
proof the secrets are real, not just that the step executed.

## 5. Commit and push

Commit the version bump + changelog + HANDOFF update. Push to `main`. Per the working agreement:
commit and push each piece of approved work immediately — don't let it accumulate uncommitted
across sessions (see the CHANGELOG entry / retrospective this rule came from).

## 6. Confirm CI is green on `main`

Branch protection on `main` requires both `Windows build + installer` and `macOS build + dmg`
checks to pass (see below). Watch the run:

```bash
gh run watch
```

## 7. Tag and build the release artifacts

```bash
git tag v3.1.0
git push origin v3.1.0
```

Pushing a `v*` tag triggers `.github/workflows/build.yml`, which builds both the Windows
installer and the macOS `.dmg` as workflow artifacts (it does not create a GitHub Release itself
— download the artifacts from the run and attach them manually, or wire up
`softprops/action-gh-release` later if that becomes worth automating).

## 8. Publish

Create the GitHub Release for the tag, paste in the changelog section from step 2, and attach the
built installer/dmg artifacts downloaded from the tag's workflow run.

---

## Branch protection (reference, not a per-release step)

`main` requires the `Windows build + installer` and `macOS build + dmg` checks
(`.github/workflows/build.yml`, which now also triggers on push to `main`) before a commit is
considered protected. Configured via:

```bash
gh api -X PUT repos/dhairyagautam-creator/Saffron-Automation/branches/main/protection \
  --input protection.json
```

**Known limitation, read before you hit it:** GitHub only lets a commit reach a required-status-
checks-protected branch once that exact commit already has a passing status — normally obtained
by pushing to another branch/PR first and merging once green. This repo intentionally has no
feature-branch or PR workflow (single branch, direct pushes, solo developer — see the process
decisions this file's project history recorded). `enforce_admins` is currently `true`, which means
**the very next direct push to `main` may be rejected** until that commit has a recorded passing
status, which it can't get without first existing on GitHub. If you hit this: push the commit to a
throwaway branch, let CI go green there, then fast-forward `main` to it (`git push origin
<branch>:main`) — or set `enforce_admins` to `false` (repo Settings → Branches → main → uncheck
"Do not allow bypassing the above settings") so the admin account can push through, accepting that
this makes the protection non-binding for the owner in practice. This tradeoff was flagged, not
silently resolved — decide which you want.
