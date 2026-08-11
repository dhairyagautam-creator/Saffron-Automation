# Version 2.0 Migration Log

## Milestone 1 — Supabase Connectivity (2026-07-25)

**What was changed**
- Added `supabase` and `python-dotenv` to `requirements.txt`, installed into the project venv.
- Added `.env` (git-ignored, holds real values) and `.env.example` (template) with `SUPABASE_URL` / `SUPABASE_ANON_KEY`.
- Added `app/supabase_client.py` — standalone module exposing `get_supabase_client()` and `verify_connection()`. Not imported by `main.py` or any other existing app code; has zero effect on the running v1.2.1-equivalent application.
- Added `.env` to `.gitignore`.
- In Supabase: created a `connection_test` table (Table Editor, default columns) with one row, and ran `GRANT SELECT ON public.connection_test TO anon;` in the SQL Editor.

**Why**
- Prove the desktop app can reach the Supabase project — network, dependency, and credential wiring only — before building anything else on top of it.

**Files modified/created**
- `requirements.txt`
- `.gitignore`
- `.env` (not committed — contains real Project URL + anon key)
- `.env.example`
- `app/supabase_client.py`

**Verification**
```
python -m app.supabase_client
```
Returned `SUCCESS: connected to Supabase.` with `Rows returned from connection_test: []`.

**Migration considerations**
- The `.env` approach only covers running from source (`python main.py`). Not yet solved: how a PyInstaller-frozen `.exe` gets its Supabase URL/anon key — can't ship a plaintext `.env` inside the installer as-is. Needs a decision in a later milestone (bake at build time vs. per-machine config vs. something else).
- Creating a table does **not** expose it. Two separate gates had to be cleared to read even one row: (1) a Postgres `GRANT SELECT ... TO anon`, and (2) an RLS policy — which does not exist yet, so today's query succeeded but returned an empty set. Every future table will hit both gates and needs an explicit policy before any row is visible to the app.

**Risks**
- The anon key is meant to be embedded in the shipped app, and this project's GitHub repo is public (per the existing auto-update setup) — exposing the anon key is only safe as long as every table's RLS policies are correct before real data reaches Supabase. No real policies exist yet; that's fine today (no production data involved) but must be addressed before any real table is created.

**Recommended next step**
- Decide whether Milestone 2 is authentication or a first real RLS policy design pass — before any Validator data moves to the cloud.

## Milestone 1b — Cloud Foundation Hardening (2026-07-25)

**What was validated/changed**
- Verified the connection succeeds on a fresh process start (simulates a full app restart) and repeatedly within one process — not a one-off fluke.
- Refactored `app/supabase_client.py` into a module-level singleton: `get_supabase_client()` now returns the same `Client` instance on every call instead of constructing a new one, so every future module (Validator, Inventory, Payment Analysis, ...) shares one connection instead of each spinning up its own.
- Added a runtime guard (`_assert_not_service_role`) that decodes the JWT's `role` claim and refuses to start if `SUPABASE_ANON_KEY` is ever set to a `service_role` key. Verified it actually blocks a simulated service_role token.
- Replaced the raise-on-failure `verify_connection` with `check_connection() -> (ok, message)`, which never raises. It distinguishes: no internet / Supabase unreachable (`httpx.ConnectError`), timeout (`httpx.TimeoutException`), and rejected credentials (`APIError` with code 401/42501/PGRST301) — each with its own friendly message — versus a generic fallback for anything else.
- Found and fixed a real bug while testing this: `postgrest`'s `APIError.code` is sometimes an `int` (gateway-level errors like an invalid key) and sometimes a Postgres error string (`"42501"`) depending on where the rejection happens. The first version compared it only as a string, silently missed the int case, and fell through to a generic "unexpected error" message instead of the intended friendly one. Fixed by normalizing to `str(exc.code)`.
- Confirmed via grep that `supabase` appears only in `requirements.txt`, `.env`, `.env.example`, `app/supabase_client.py`, and this log — no other module constructs its own client or hardcodes a key, and no JWT-shaped string exists anywhere outside `.env`.
- Confirmed `import main` (the real app's entry point) still succeeds with the new dependencies installed — no import-time breakage introduced.

**Files modified**
- `app/supabase_client.py` (singleton, service_role guard, `check_connection`)

**Architecture decisions**
- One shared client, one module: every future module reads Supabase through `app.supabase_client.get_supabase_client()` rather than each owning its own client/config. This is the seam Validator/Inventory/Payment Analysis will plug into later.
- Failures are data, not exceptions, at the boundary meant for UI code: `check_connection()` returns `(bool, str)` specifically so that once this is wired into the UI, a failed connection becomes a message box, not a stack trace.
- The service_role guard is a misconfiguration safety net, not a security boundary — it reads an unverified JWT claim, so it catches "wrong key pasted into `.env`" but is not a substitute for simply never generating/handling the service_role key anywhere near this client.

**Health check — what's working**
- Connectivity: solid. URL + anon key wiring, singleton reuse, and restart-safety all verified against the real project.
- Secrets hygiene: clean. Only `.env` holds the real key, `.env` is gitignored, nothing hardcoded elsewhere.
- Failure handling: covers no-internet, Supabase-unreachable, and bad-credentials with user-facing messages, verified against real error responses (not assumed ones) — including the int/str bug above, which only surfaced under actual testing.

**What could become a problem later**
- Frozen `.exe` distribution: `.env`-based config only works running from source. A PyInstaller build needs a deliberate decision on how the anon key ships (bake at build time is fine for an anon key backed by correct RLS, but must be intentional, not a default) — needed before any real build goes out.
- RLS policies: `connection_test` has a `GRANT` but no `SELECT` policy, so it silently returns zero rows. Safe today (throwaway table, no real data), but every real table will need both a grant and a policy, not just one.
- No retry/backoff: `check_connection()` reports failure cleanly but doesn't retry a transient blip. Fine for a one-shot check; will matter once real sync depends on this path surviving brief network drops.
- No timeout tuning: relies on the client library's default `httpx` timeout, not yet adjusted for a slow or restrictive corporate network.

**Recommended next step**
- Same as before: decide whether Milestone 2 is authentication or the first real RLS policy design pass, now that the connection layer itself is solid enough to build on.

## Milestone 2 — Login Screen (UI/UX only, no authentication) (2026-07-25)

**What was built**
- `ui/login_window.py`: a new `LoginWindow(ctk.CTk)` shown before `MainWindow`. Built entirely from existing design tokens (`ui/theme.py` — Color/Font/Spacing/Radius) and components (`ui/components.py` — `Card`, `PrimaryButton`), so it matches the rest of the app rather than introducing a new visual style.
- Screen contents: Saffron logo (`assets/saffron_logo.png`, with a brand-color circular placeholder fallback if the asset is ever missing), title "Saffron Automation Suite", subtitle "Enterprise Operations Platform", User ID/Email field, Password field with a Show/Hide toggle, a Login button, a hidden error-message area, and a "Version 2.0 Development" corner label.
- Login button click: disables the form, shows a "Signing in..." loading state, waits ~600ms (simulated — no network call), then closes the login window and lets `main.py` proceed into the existing `MainWindow` exactly as before.
- Closing the window via the OS close button (rather than clicking Login) sets `login_confirmed = False`, and `main.py` exits instead of falling through to the app — closing without logging in must not be equivalent to logging in, even though today's Login always "succeeds."

**Files modified/created**
- `ui/login_window.py` (new)
- `main.py`: one import added, and 4 lines inserted immediately before `app = MainWindow()` to show the login screen first and check `login_confirmed`. No existing line was changed or removed.

**Architecture decisions (built for later, not implemented now)**
- `_complete_login()` is the single documented seam for real Supabase Auth: on success it will set a session before confirming; on failure it will call the already-built `_show_error(message)` and `_reset_form_state()` instead of confirming. Wiring real auth in later should not require touching layout code.
- `_show_error`/`_hide_error` and `_reset_form_state` are fully functional but unused today (nothing can fail yet) — they exist as the ready-made hook the requirements asked for ("keep the login screen completely modular").
- Nothing in `ui/login_window.py` imports `app.supabase_client` or any auth/session concept — this phase is UI/UX only, as instructed.

**Bugs found and fixed during testing**
- Window position bug: CTk's `geometry()` scales a combined `"WxH+X+Y"` string's width/height by the display's DPI factor but passes the `X+Y` position through unscaled. An initial version computed the centered X/Y in the same logical units as the (scaled) width/height, which would have under-offset the window — appearing off-center toward the top-left — on any display with scaling above 100%. Fixed by setting size first, rendering, then centering using the actual rendered *physical* pixel size against the *physical* screen size.
- Window-too-large bug: a fixed 440x620 request scales to ~660x930 physical pixels at 150% DPI scaling, which exceeds a smaller/lower-resolution screen entirely. Fixed by capping the requested size to 90% of the actual (DPI-reversed) screen dimensions, mirroring the approach `MainWindow._apply_responsive_geometry` already uses.
- Both were caught by an automated headless check (instantiate the window, force layout, read back `winfo_width/height/screenwidth/screenheight` — no manual clicking needed) before either could reach the running app.

**Verification performed**
- `python -c "import main"` — confirms the app's import chain still succeeds with the login screen wired in.
- Automated, non-interactive checks against a live (but never `mainloop()`-entered until intentionally) `LoginWindow` instance: builds without error; fits within the actual screen; password field masks real typed input (`show` is correctly `'*'` once text exists — briefly unmasked while only the placeholder is showing, which is `CTkEntry`'s own standard placeholder behavior, not a bug); Show/Hide toggle flips masking both directions; error banner is hidden by default and toggles on/off correctly; Login button enters a disabled "Signing in..." state and only sets `login_confirmed = True` after the simulated delay completes.
- **Not verified**: exact on-screen visual centering. The automated check environment reported an inconsistent window position even after the scaling fix (likely a quirk of that non-interactive test environment rather than the real desktop), and I have no way to visually inspect a native Windows GUI window from here. Please eyeball the actual position/appearance when you run `python main.py` yourself — if the window isn't centered on your real screen, that's the one thing left to adjust.

**Risks / what could become a problem later**
- The login window is a second `ctk.CTk` root, sequential with `MainWindow`'s (never simultaneous) — standard and safe, but worth remembering if a future requirement needs the login screen to reappear without a full app restart (e.g., session expiry), since that would need a different pattern (e.g., a re-login `CTkToplevel` over `MainWindow`, or restructuring around a single persistent root).

**Recommended next step**
- Visually confirm the login screen's appearance and centering on your real screen by running `python main.py`.
- Then decide Milestone 3: real Supabase authentication into `_complete_login()`, or the RLS policy pass still outstanding from Milestone 1.

## Milestone 2b — Login Moved Into the Main Window (2026-07-25)

**What changed and why**
- Replaced the separate-popup login approach with an embedded screen: Login is now just another entry in `MainWindow`'s existing screen registry (the same `self.screens[name] = Page(...)` + `tkraise()` pattern already used for Home / Path Validator / Inventory Monitoring / Payment Analytics), shown first instead of Home. This removes an entire class of window-management complexity (a second `Tk` root, its own geometry/DPI-centering math, its own icon/WM_DELETE_WINDOW handling) that the previous approach needed — centering a card *within* an existing frame via `.place(relx=0.5, rely=0.5, anchor="center")` has none of the DPI-scaling pitfalls a second root window's `geometry()` does.
- No authentication logic changed. `_complete_login()` still just calls a callback after a simulated delay — the callback is the only thing that changed (it used to `self.destroy()` a whole second window; now it calls `on_login_success()`, which `MainWindow` wires to `self.show_screen("Home")`).

**Files added**
- `ui/login_page.py` — `LoginPage(ctk.CTkFrame)`. Same visual content as before (logo, title, subtitle, fields, Show/Hide, error placeholder, dev-version corner label), rebuilt as an embedded frame instead of a window. Implements `on_show()`, the same optional hook other screens can implement (`MainWindow.show_screen` already calls it if present) — resets the form each time the screen becomes active, so a future logout -> back-to-Login transition never shows stale disabled/loading state.

**Files removed**
- `ui/login_window.py` — the standalone-root-window version from Milestone 2, superseded by `ui/login_page.py`. Deleted rather than left unused, since keeping it would directly contradict "no separate login dialog or popup window."

**Files modified**
- `ui/main_window.py`: added `LoginPage` import, registered `self.screens["Login"]` in `_build_screens()`, changed the initial `show_screen("Home")` call to `show_screen("Login")`. Docstrings updated to mention Login as part of the registry. No other logic touched — the first-run Setup Wizard gate, update checker, and exception safety net are all unchanged; Setup Wizard (for a brand-new install) still runs before anything else, then Login, then Home after login.
- `main.py`: removed the `LoginWindow` import and the 4-line pre-step from Milestone 2 — back to a single `app = MainWindow(); app.mainloop()`, since login is now part of `MainWindow` itself rather than a step before it.

**Verification performed**
- `python -c "import main"` — clean import with the new wiring.
- Headless instantiation of the real `MainWindow` (with `init_db()` etc. run first, matching what `main.py` actually does): confirmed `active_screen` starts as `"Login"`; confirmed calling the Login page's bypass (`_complete_login()`) flips `active_screen` to `"Home"` — all within one single `MainWindow` instance, never a second window. Window size measured at 1176x633 (this test machine's screen), i.e. `MainWindow`'s own existing responsive sizing — untouched and identical before/after login, since it's the same window throughout by construction.
- Not independently re-verified visually (same limitation as Milestone 2 — no way to see a native window from here). The architecture this time removes the specific risk that needed visual confirmation before (a second window's DPI-centering), since there is no second window anymore.

**Recommended next step**
- Close any previously-running instance of the app (started before this change, still showing the old separate popup) and run `python main.py` again to see the login screen embedded in the main window.
- Then: Milestone 3 — real Supabase authentication into `_complete_login()`, or the outstanding RLS policy pass from Milestone 1.

## Milestone 3 — Real Supabase Authentication (2026-07-25)

**What was built**
- `app/auth_service.py` (new): the single, isolated module for every Supabase Auth operation. Exposes `sign_in(email, password)`, `restore_session()`, `sign_out()`, all returning a small `AuthResult` dataclass (`success`, `user_email`, `error_kind`, `error_message`) so `ui/login_page.py` never touches the Supabase client, gotrue exceptions, or the session-storage mechanism directly. `AuthResult.user_email` is the only identity exposed today, deliberately — reading roles/claims later is meant to happen in this module, not the UI.
- Session persistence uses **`keyring`** (Windows Credential Manager on this machine, confirmed via `keyring.get_keyring()` → `WinVaultKeyring`), not a plaintext file — a refresh token is a long-lived credential equivalent to a saved password, so it gets the same OS-level protection a browser or password manager would give it, rather than living in a JSON file at `%LOCALAPPDATA%`.
- `ui/login_page.py` rewired: `_handle_login_click` now validates non-empty fields, then calls `auth_service.sign_in()` on a background thread (real network call — matches the existing `threading.Thread` + `self.after(0, ...)` pattern already used by `main_window.py`'s update checker, so the UI never freezes during the request). On success, navigates to Home; on failure, re-enables the form and shows the specific message from `AuthResult.error_message` in the existing error banner.
- Startup session check: `LoginPage.on_show()` now runs `auth_service.restore_session()` on a background thread the *first* time the screen is shown (an app-launch-only check — skipped on any later re-show, e.g. after a future logout, since logout already clears the stored session, making a re-check pointless). A new purpose-built overlay (indeterminate `CTkProgressBar`, not the existing percent-based `LoadingOverlay` — there's no meaningful "percent done" for an auth check) shows "Checking your saved session..." while this runs. If a valid session is restored, Home opens directly with the login form never becoming visible; if not, the login form appears (with a friendly message if there *was* a saved session that turned out to be expired/invalid).
- Logout: added a small "Log out" button to `ui/home_page.py` (top-right corner, `SecondaryButton` — the only existing module touched, and only to add this one control; no existing Home content moved or changed). Wired through a new `MainWindow._handle_logout()`, which calls `auth_service.sign_out()` on a background thread (clears the local session unconditionally, even if the server-side invalidation call fails or the network is down) then returns to the Login screen.

**Files added**
- `app/auth_service.py`

**Files modified**
- `ui/login_page.py` — real sign-in wiring, startup session-check overlay, no more simulated-delay bypass.
- `ui/main_window.py` — imports `auth_service`, passes `on_logout` to `HomePage`, adds `_handle_logout()`.
- `ui/home_page.py` — added the Log out button and its `on_logout` parameter.
- `requirements.txt` — added `keyring`.

**Verification performed (all against the real Supabase project, not mocks)**
- `restore_session()` with nothing saved → clean `AuthResult(success=False)`, no error shown (confirmed both directly and through the full UI/thread/mainloop path).
- `sign_in()` with wrong credentials → real `AuthApiError` (`code="invalid_credentials"`) correctly mapped to `"Invalid email or password."` — confirmed both directly and end-to-end through the actual UI: typed into the real entry widgets, clicked through `_handle_login_click`, background thread ran, error banner appeared with the exact right text, button and fields re-enabled, screen stayed on Login.
- `sign_in()` and `restore_session()` against an unreachable host → both correctly return `error_kind="network"` with the friendly connectivity message (the restore case required a syntactically-valid-but-expired fake JWT to actually reach the network-refresh code path rather than fail on local parsing first — a genuine quirk of the underlying `gotrue`/`supabase_auth` library's `set_session`, not a bug in this code).
- A malformed/garbage token during `restore_session()` → caught by the generic `except Exception`, returns a clean `"unexpected"` result and clears the bad stored session, rather than crashing.
- Keyring round trip (`_save_session` / `_load_session` / `_clear_session`) verified directly.
- `python -c "import main"` — clean import with all the new wiring.

**Success path — since verified (2026-07-25, same day):** a real test user (`dhairyagautam@andrewsosborne.com`) was created in Supabase and used to log in through the actual running app. Confirmed after the fact:
- A real session was persisted to the OS credential store (token presence/length checked, contents never printed).
- A separate fresh process calling `restore_session()` independently validated the saved session and correctly returned the real user's email — proving the "bypass login on restart" path works, not just the "no session" path tested earlier.
- `sign_out()` cleared the persisted session; a subsequent `restore_session()` correctly returned a clean failure, proving logout actually revokes the saved session rather than just visually returning to the Login screen.

**Risks / what could become a problem later**
- No "Remember me" distinction — every successful login persists a session indefinitely (until Supabase's own refresh-token expiry or an explicit logout). If a future requirement wants "stay signed in" to be optional, that's a small addition to `sign_in()`'s call site, not a restructure.
- `keyring`'s Windows backend is confirmed working in this dev environment; it hasn't been tested on a locked-down corporate machine where the Credential Manager API might be restricted by policy. Worth a real-world check before wide rollout.
- Frozen `.exe` note from Milestone 1 still applies here too: `python-dotenv`/`.env` loading is source-run only; how the anon key ships in a built installer is still an open decision.

**Recommended next step**
- Milestone 3 is complete and fully verified (success, restart-persistence, and logout all confirmed against the real project). Note: the manual `sign_out()` used to verify logout also cleared the real session — the next time the app is restarted, it will show the Login screen again, which is expected.
- Next: role-based access control (explicitly out of scope for this milestone) or the RLS policy pass still outstanding from Milestone 1.

## Milestone 4 — Role-Based Access Control: Database Foundation Only (2026-07-25)

**What was built**
- `supabase/migrations/0001_roles_and_profiles.sql` (new): a versioned, idempotent SQL migration creating the schema RBAC will read from later. Applied by the user directly in the Supabase SQL Editor — this app's client only ever holds the anon key (see Milestone 1's service_role guard), so DDL has to run through the dashboard, the same pattern as Milestone 1's `GRANT SELECT` step.
- `public.roles`: `id` (uuid, `gen_random_uuid()`), `name` (unique), four boolean module flags (`employee_module`, `inventory_module`, `payments_module`, `user_management`), `created_at`. Seeded with exactly the four roles specified — Admin (all four `true`), Inventory, Accounts, HR (each with only their one corresponding module `true`).
- `public.profiles`: `id` (uuid, references `auth.users(id) on delete cascade` — the standard Supabase 1:1-profile pattern, not a separate generated key), `full_name`, `role_id` (nullable FK to `roles.id`), `active` (default `true`), `created_at`. Indexed on `role_id` for future joins/filtering.
- Backfilled a `profiles` row for the existing test user (`dhairyagautam@andrewsosborne.com`, predates this table) assigned the Admin role, per explicit confirmation.
- RLS enabled on both new tables with **zero policies** — closed to the app's API keys entirely until a later milestone deliberately opens specific read access. No grants, no policies, nothing added beyond the tables and seed data themselves.

**Explicitly not done (by design, per instructions)**
- No permission-check logic anywhere in the app.
- No UI hide/show based on role.
- No changes to `auth_service.py`, `login_page.py`, or any other part of the auth flow built in Milestone 3.
- No RLS policies — the tables exist but are not yet queryable through the app's anon/authenticated keys.

**Verification performed**
- User ran the migration in the Supabase SQL Editor and confirmed via `select * from public.roles;` / `select * from public.profiles;`: 4 rows in `roles` (Admin/Accounts/HR/Inventory), 1 row in `profiles` (Dhairya, role_id -> Admin, active = true).
- Not verified by me directly via the anon-key client, by design — RLS with no policies means the app's API key cannot read these tables at all right now, which is the intended state for "database foundation only."

**Risks / what could become a problem later**
- Since RLS has no policies yet, when a future milestone actually implements permission checks, it will need at least: a policy letting an authenticated user read their own `profiles` row (`id = auth.uid()`), and a policy letting authenticated users read `roles` (a non-sensitive reference table). Neither exists yet — flagging now so it isn't forgotten.
- `role_id` is nullable and there's no trigger auto-creating a `profiles` row on signup — a brand-new user today would have an `auth.users` row but no `profiles` row until either a trigger is added or the app creates one explicitly. Deliberately left out of this migration since it would touch the authentication flow, which was out of scope here.

**Recommended next step**
- Decide whether the next milestone is: (a) RLS read policies for `roles`/`profiles`, (b) the app actually reading `role_id` after login to enforce/display permissions, or (c) the RLS policy pass still outstanding from Milestone 1's `connection_test` table.

## Milestone 5 — Role Loading After Authentication (2026-07-25)

**What was built**
- `supabase/migrations/0002_profiles_roles_read_policies.sql` (new, applied by the user): the RLS read policies Milestone 4 deliberately left out. Grants `SELECT` on `profiles`/`roles` to the `authenticated` Postgres role only (never `anon`), plus two policies -- a user can read their own `profiles` row (`auth.uid() = id`), and any signed-in user can read all of `roles` (a small, non-sensitive reference table).
- `app/rbac_state.py` (new): the centralized, in-memory "who is signed in and what's their role" store, structured like the existing `app/mode_state.py` pattern (a pure leaf module, process-wide state, simple getters). Named `rbac_state` rather than `session_state` specifically to avoid colliding with the pre-existing `app/session_state.py`, which tracks something unrelated (the active imported workbook for the Path Validator pipeline) -- caught before overwriting it. Holds frozen `Role`/`Profile` dataclasses; `current_role()` is the documented seam future permission-enforcement code will read from, but nothing calls it yet.
- `app/rbac_service.py` (new): `load_profile_and_role(user_id, user_email)` -- fetches `profiles` by id, then `roles` by the profile's `role_id`, using the same shared Supabase client as `auth_service.py` (so the queries run as the just-authenticated user, not the anon key). Fails closed: missing profile row, null `role_id`, or a `role_id` that doesn't resolve to a real role all return a clear, specific error message and never populate `rbac_state`. Logs the outcome either way -- one line naming the user, their role, and all four permission flags on success; a warning naming exactly what was missing on failure.
- `ui/login_page.py`: both entry points (fresh sign-in and startup session-restore) now converge on a new `_begin_profile_load()` step after Supabase auth succeeds, before `on_login_success()` fires. The existing "checking session" overlay was generalized (`_show_loading_overlay(message)`) to also show "Loading your profile and permissions..." during this step, rather than building a second overlay.
- `ui/main_window.py`: logout now also calls `rbac_state.clear_current_profile()` alongside the existing `auth_service.sign_out()`, so a logged-out state has no stale role data sitting in memory.

**Files added**
- `supabase/migrations/0002_profiles_roles_read_policies.sql`
- `app/rbac_state.py`
- `app/rbac_service.py`

**Files modified**
- `ui/login_page.py` -- profile/role loading step, generalized loading overlay.
- `ui/main_window.py` -- clears `rbac_state` on logout.

**Explicitly not done (by design, per instructions)**
- No permission enforcement, no UI hide/show based on role or module flags -- `rbac_state.current_role()` exists and is populated, but nothing reads it yet.
- No changes to what any module (Path Validator, Inventory, Payment Analytics) does or shows.

**Verification performed**
- `python -c "import main"` -- clean import.
- Confirmed against the real project, real user: app logs show `Session loaded: user='dhairyagautam@andrewsosborne.com' role='Admin' permissions={employee_module=True, inventory_module=True, payments_module=True, user_management=True}` immediately after login, followed by the Home screen opening -- the exact success path this milestone targets.
- Not separately re-verified: the three failure paths (no profile, no role assigned, role_id not found) added in `rbac_service.py`. Each returns a distinct, specific message and is structurally identical to the already-proven-working auth-service error paths (same try/except-over-a-network-call shape), but wasn't exercised against a real broken profile/role in this session.

**Risks / what could become a problem later**
- `rbac_state` is populated after login but nothing clears it if the *server-side* row changes later (e.g., an admin changes a user's role while they're already logged in) -- it's a point-in-time snapshot taken at login, not a live subscription. Worth deciding, once enforcement is built, whether stale-role-until-next-login is acceptable or needs a refresh mechanism.
- The three failure paths (missing profile/role) are implemented but not yet tested against a real broken account -- worth a deliberate test (e.g., temporarily null out the test user's `role_id`) before relying on them.

**Recommended next step**
- Actual permission enforcement: read `rbac_state.current_role()` to hide/disable modules a role doesn't grant, starting wherever makes sense first (e.g. Home page's module cards).

## Milestone 6 — Role-Based Module Access (2026-07-25)

**What was built**
- `app/permissions.py` (new): the single centralized place every permission question goes through -- `can_access_employee_module()`, `can_access_inventory_module()`, `can_access_payments_module()`, `can_access_user_management()`, plus a generic `can_access(module)` they're all built on. Fails closed: no signed-in user or no loaded role means every check returns False. Also owns `log_accessible_modules()`, called once from `rbac_service.load_profile_and_role()` right after a role loads successfully -- logs a clean "enabled: [...]" / "restricted: [...]" pair naming each module.
- `ui/home_page.py`: card visibility is now decided by `app/permissions.py`, not hardcoded. Cards are rebuilt in `on_show()` (called every time `MainWindow.show_screen("Home")` runs) rather than once in `__init__` -- `__init__` runs before login, when nothing is loaded yet, so building the cards there would always show zero. Added a 4th card, "User Management," gated by `user_management`. If a role grants nothing, `HomePage` shows the existing `EmptyState` component instead of an empty row.
- `ui/main_window.py`: added a `_MODULE_PERMISSION_CHECKS` mapping and one gate inside `show_screen()` itself -- the single chokepoint every screen transition already passes through. A screen requiring a permission the current role doesn't grant is refused there, with a real "Access Denied" message box and a warning log, regardless of what triggered the navigation attempt (a Home card today; anything else later). This is what satisfies "block direct access, not just hide the entry point" without duplicating the check anywhere else.
- Registered a new `"User Management"` screen using the existing `ui/placeholder_page.py` component (already used elsewhere for not-yet-built top-level modules) -- there is no real user-management feature yet (out of scope: "do not change the database schema/auth system"), only the access-controlled entry point this milestone asked for.

**Files added**
- `app/permissions.py`

**Files modified**
- `ui/home_page.py` -- permission-driven card rebuild, `on_show()` hook, User Management card, EmptyState fallback.
- `ui/main_window.py` -- centralized `show_screen()` gate, User Management screen registration.
- `app/rbac_service.py` -- calls `permissions.log_accessible_modules()` after a successful load.

**Explicitly not done (by design, per instructions)**
- No changes to `auth_service.py`, the login flow, or any Supabase/database schema.
- No real User Management functionality (user list, role assignment UI) -- only its access-gated entry point, per this milestone's scope being access control, not that feature.
- Existing module content (Path Validator, Inventory Monitoring, Payment Analytics internals) untouched -- only whether their entry screen can be reached changed.

**Verification performed**
- `python -c "import main"` -- clean import.
- Headless test against the real `MainWindow`/`HomePage` classes (not reimplemented/mocked logic), cycling through three simulated roles via `rbac_state.set_current_profile()`:
  - **Inventory-only role**: Home built exactly 1 card; `show_screen("Path Validator")` and `show_screen("User Management")` were both blocked (active screen unchanged, a real `messagebox.showerror` call captured); `show_screen("Inventory Monitoring")` succeeded.
  - **Admin role** (all four true): Home built all 4 cards; `show_screen("User Management")` succeeded.
  - **No role loaded**: Home showed the `EmptyState` placeholder (0 cards); `show_screen("Payment Analytics")` was blocked.
- Since verified for real the same day: a second Supabase Auth user (`dhairyagamer07@gmail.com`) was created and assigned the Inventory role (`supabase/migrations/0003_assign_inventory_test_user.sql`, same backfill pattern as migration 0001). Logging in as that user produced `role='Inventory' permissions={employee_module=False, inventory_module=True, payments_module=False, user_management=False}` and `Modules enabled: ['Inventory Monitoring']` / `Modules restricted: ['Path Validator', 'Payment Analytics', 'User Management']` -- confirming the restricted-role behavior through a real login, not just simulated `rbac_state`. Switching back to the Admin account in the same session correctly reloaded all four modules as enabled, confirming role state isn't stale/cached across logins.

**Files added (this session)**
- `supabase/migrations/0003_assign_inventory_test_user.sql`

**Recommended next step**
- Build real User Management functionality (currently just a gated placeholder), or move on to a different area of Version 2.0.

## Milestone 7 — User Management Dashboard (List Only) (2026-07-26)

**What was built**
- `supabase/migrations/0004_get_all_users_function.sql`: a `SECURITY DEFINER` Postgres function, `get_all_users()`, the one deliberate place `auth.users` (email, created date) is ever joined against `profiles`/`roles` -- it enforces its own `user_management = true` check internally (defense in depth beyond the UI gate), since `SECURITY DEFINER` bypasses RLS entirely.
- `app/user_management_service.py` (new): `list_users()` (via that RPC) and `list_roles()`, real from day one.
- `ui/user_table.py`, `ui/user_dialogs.py`, `ui/user_management_page.py` (new): a from-scratch CTk table (not the app's existing `ttk.Treeview` helper -- Treeview cells can't embed real per-row buttons, and this needed real Edit/Enable-Disable buttons), with client-side search/sort/pagination, and fully validated Add/Edit/Enable-Disable dialogs.
- Replaced the `PlaceholderPage` previously registered for "User Management" in `ui/main_window.py` with the real page -- the existing centralized permission gate from Milestone 6 needed no changes.
- Add/Edit/Enable-Disable were placeholder-only in this milestone by design: validated, correctly laid out, but not yet connected to Supabase (that was explicitly deferred to Milestone 8).

**Verification**: headless tests against the real page/dialog classes (data load, sort, search, pagination, both dialogs' validation) all passed. `python -c "import main"` clean.

## Milestone 8 — User Management Write Operations (2026-07-26)

**What was built**
- `app/user_validation.py` (new): shared field validation, used by both the dialogs and the service layer independently.
- `app.supabase_client.create_standalone_client()`: a second, independent Supabase client used only for `auth.sign_up()` during Add User -- calling `sign_up` on the shared singleton would silently replace the admin's own logged-in session with the new user's, since establishing a session is that call's side effect on whichever client makes it.
- `app/user_management_service.py`: `create_user()`, `update_user()`, `set_user_active()` -- all real, all logged step-by-step, all re-validated independently of the UI.
- `app/rbac_service.py`: disabled accounts are now blocked from logging in (`active` checked right after fetching the profile, before role loading), extending this file's pre-existing missing-profile/missing-role pattern rather than adding a new mechanism elsewhere.
- `ui/user_dialogs.py` / `ui/user_table.py`: dialogs no longer close immediately on submit -- they show "Saving.../Please wait...", stay open with the server's exact error on failure, and only close on confirmed success. An admin's own row has its Disable button disabled client-side (mirrors the DB-level block below).
- Three SQL migrations, in the order actually needed (see "Bugs found" below for why there are three, not one):
  - `0005_user_management_write_policies.sql` -- INSERT/UPDATE policies for `profiles`, admin-only, with the UPDATE policy's `WITH CHECK` blocking an admin from setting their own `active` to `false`.
  - `0006_admin_read_all_profiles.sql` -- the missing admin-can-SELECT-any-profile policy (see bug 1 below).
  - `0007_fix_profiles_policy_recursion.sql` -- moved the "is this caller an admin" check into a `SECURITY DEFINER` function (`is_user_management_admin()`), used by all three admin policies, replacing the inline correlated subquery that caused bug 2 below.

**Bugs found during real testing (not caught by earlier headless tests, since those never touched live RLS) -- root-caused and fixed, not papered over:**

1. **Disable silently affected 0 rows; a disabled user could still log in.** `UPDATE ... WHERE id = ...` returned success with zero rows changed, no error. Root cause: Postgres RLS requires a row to be visible under a *SELECT* policy before `UPDATE`/`DELETE` can even locate it -- and the only SELECT policy that existed (Milestone 5) was "read your own row." An admin had no way to see (and therefore update) anyone else's row. Fixed by `0006`. Separately, once a disabled account *is* correctly rejected at login, the code was refusing entry to the app but leaving Supabase's own session (both the in-memory client session and the persisted keyring one) still fully valid -- fixed by calling `auth_service.sign_out()` at the point of rejection in `rbac_service.py`, not just declining to populate `rbac_state`.
2. **Add User's profile-insert step failed with `new row violates row-level security policy`, even though the calling admin genuinely has `user_management = true`.** Confirmed directly (queried the admin's own role row) that the permission check itself was logically correct. Root cause was `0006`'s fix itself: with two self-referencing SELECT-shaped policies now on `profiles` (the original "own row" one and the new admin one), Postgres detected the two could require resolving each other and refused with `42P17 infinite recursion detected`, even though this specific case would have terminated. Fixed by `0007`, moving the check into a `SECURITY DEFINER` function -- its internal query bypasses RLS entirely (same pattern already used for `get_all_users()`), breaking the recursive dependency instead of avoiding it by luck.
3. **Supabase's built-in email service's send-rate-limit** was hit during repeated Add User testing (`over_email_send_rate_limit`) -- not a code bug, but the error message was made specific (mentioning the rate limit explicitly, and the "Confirm email" toggle / custom SMTP options) rather than showing Supabase's raw text.
4. Two accounts from mid-investigation testing needed real cleanup, done directly (not worked around): `dhairyagamer07@gmail.com`'s profile was correctly disabled once bug 1 was fixed (confirmed: 1 row affected, `active=false` in the returned row, and a direct `load_profile_and_role()` call for that user was correctly rejected with the account-disabled message). `rgautams@gmail.com`'s Auth account had been created successfully during the broken window but never got a profile (bug 2) -- initially patched by inserting its missing profile directly once INSERT was fixed, but that account turned out to be permanently stuck unconfirmed (created while "Confirm email" was still on, and turning that setting off afterward doesn't retroactively confirm existing accounts) -- resolved by deleting and recreating it through the real Add User dialog after the setting was disabled project-wide.

**Verification performed (all against the real project, real logins, not mocks):**
- `set_user_active` on a real user: 1 row affected, `active=false` confirmed in the returned row (previously 0 rows, silently).
- `load_profile_and_role()` called directly against that now-disabled account: correctly returned the "account disabled" result and left `rbac_state` empty.
- Full Add User flow through the real UI end-to-end: all 3 steps logged individually, user list auto-refreshed, and a fresh login as the newly created account (`rgautams@gmail.com`) succeeded immediately with the exact assigned role and permissions (`role='Accounts'`, only `payments_module` enabled).
- Edit User verified for real (renamed and reassigned `rgautams@gmail.com` through the dialog, confirmed via logs).
- Self-disable guard verified directly at the service layer (blocked for own id, not blocked for other ids).

**Risks / what's still open**
- Add User is still not atomic across Supabase Auth + Postgres, and cannot be made so from this app: if the profile insert fails after the Auth account is created, there is no way to delete that orphaned Auth account without the `service_role` key, which this app deliberately never holds (Milestone 1's guard). The error message names the orphaned account's id and email explicitly so manual cleanup (Supabase dashboard -> Authentication -> Users) is straightforward when it happens -- this is a permanent architectural tradeoff, not a bug to revisit.
- The project's "Confirm email" setting is currently off (changed during this session's testing) -- worth a deliberate decision before real users are onboarded this way: leave it off (immediate access, no verification step) or set up a custom SMTP provider and turn it back on (verified email, no shared rate limit).

**Recommended next step**
- Decide the "Confirm email" / custom SMTP question above before onboarding real users through Add User.

## Milestone 9 — Cloud Sync Architecture, Path Validator Parameters Only (2026-07-26)

**What was built**
- `supabase/migrations/0008_module_configurations.sql`: a generic table, `module_configurations` (`module_key`, `config jsonb`, `updated_at`, `updated_by`) -- one row per module, its ENTIRE configuration as a single JSON object, never individual settings as rows/columns. A trigger sets `updated_at`/`updated_by` server-side from `auth.uid()`, overwriting whatever the client sends, so "last synced" and "who synced it" are always trustworthy. Any authenticated user can read/write any module's row (consistent with the app's current model -- screen-level access, from Milestone 6, is the real gate; there's no per-module data-ownership concept yet).
- `app/sync_service.py` (new): `push_config(module_key, config)` / `pull_config(module_key)` -- the reusable Sync Service. Knows nothing about Path Validator, rule parameters, or any specific data shape; every function takes a plain string key and a plain dict. This is the piece Employee/Inventory/Payments modules reuse later, unchanged, just with their own `module_key` and their own local read/write functions.
- `app/rule_parameters.py`: added `MODULE_KEY`, `CLOUD_SYNCED_RULE_NAMES = ("SAME_LOCATION", "DASHBOARD")`, `get_full_configuration()` (assembles the whole synced config from the existing per-field local storage), and `apply_full_configuration(config)` (writes a whole config back into local storage). `HOSPITAL_SUPPRESSION` (Developer Mode only) is deliberately excluded from cloud sync -- Developer Mode is explicitly a per-machine, never-shared concept (see `app/mode_state.py`), so syncing it would leak one person's local experimental tuning into what every other laptop pulls down.
- `ui/parameters_page.py`: Save now runs validate -> push to Supabase -> only on success, write the local cache -> show a message, in exactly that order (so the local cache never reflects an edit the cloud rejected). Sections outside the cloud-synced set (Hospital Suppression) still save straight to the local cache exactly as before -- unchanged behavior for that Developer-Mode-only section. A new **Refresh** button downloads the latest cloud config, writes it to the local cache, and calls the page's existing `_load_values()` to re-render immediately -- no restart. A "Last synced" label (in the user's local timezone) updates after both a successful Save and a successful Refresh.
- Local SQLite storage was NOT changed -- the existing `rule_parameters` table now literally functions as the local cache the requirements asked for, rather than introducing a second, competing local storage mechanism.

**Files added**
- `supabase/migrations/0008_module_configurations.sql`
- `app/sync_service.py`

**Files modified**
- `app/rule_parameters.py`, `ui/parameters_page.py`

**Explicitly not done (by design, per instructions)**
- No automatic/background sync, no realtime updates -- Refresh is manual, exactly as specified.
- No changes to any other module (Employee/Inventory/Payments untouched; only Path Validator Parameters).
- No changes to Developer Mode's local-only behavior for Hospital Suppression.

**Bug found and fixed during implementation**
- The initial Refresh button used a Unicode arrow glyph (⟳) that crashed with `UnicodeEncodeError` under this environment's console encoding when exercised in an automated test. Rather than risk uncertain glyph rendering in the actual GUI too, replaced it with plain "Refresh" text, matching the app's existing convention (most buttons are plain text; the one existing arrow elsewhere, "← Back to Home," uses a much more universally-supported character).

**Verification performed (all against the real project, real login, not mocks)**
- Headless: `get_full_configuration()`/`apply_full_configuration()` round-tripped correctly locally; `HOSPITAL_SUPPRESSION` confirmed excluded from the synced config.
- Headless, mocked cloud layer: invalid bucket-edge input correctly blocked before any save; a mocked successful push correctly updated the local cache only after "success," updated the last-synced label, and restored the Save button; a mocked failed pull correctly left everything untouched and showed the specific error.
- **Real, end-to-end, in the running app**: changed `same_place_radius_meters` to 200 and clicked Save -- logs show the upload completing first, then the local cache being written (14 parameters, 2 sections) only after that. Directly queried the live `module_configurations` row afterward: `config` contains the correct value, `updated_at`/`updated_by` correctly set by the database trigger (matching the real signed-in user's id, not something the client sent). Clicking Refresh downloaded that same value back down and re-cached it -- confirming the actual Laptop-A-saves / Laptop-B-refreshes scenario the requirements describe, not just a simulated one.

**Risks / what's still open**
- `module_configurations` RLS currently allows any authenticated user to write any module's config (no restriction to, say, only Admins). Not requested this milestone and consistent with the app's current security model, but worth a deliberate decision once more modules are on this system and "who can change shared operational config" becomes a real question.
- No conflict resolution: if two laptops both edit and save before either refreshes, the second push simply overwrites the first with no merge or warning. Acceptable for a first architecture-validation step per the instructions ("this step is only to validate the cloud synchronization architecture"), but worth deciding on before this pattern is relied on for modules with more contention.

**Recommended next step**
- Decide the "Confirm email" / custom SMTP question from Milestone 8, still outstanding.
- Otherwise: apply this same Sync Service to the next module (Employee, Inventory, or Payments), or address the RLS/conflict-resolution questions above before doing so.
- Otherwise: User Management is functionally complete for this phase (list, add, edit, enable/disable, all real and verified) -- next could be permission enforcement inside individual modules (today's RBAC only gates which top-level screens are reachable), or a different area of Version 2.0.

## Milestone 10 — Development Release Channel (2026-07-26)

**What was built**
- `app/version.py`: `APP_VERSION = "1.3.1-dev.0.1"`, new `CHANNEL = "development"` field -- the single source of truth every other channel-aware piece reads from.
- `app/updater.py`: replaced `_parse_version()` (previously `int()`-per-segment, which would have crashed/silently degraded to `(0,)` on any `-dev.X.Y` suffix) with a parser that separates core version from an optional suffix and orders correctly (a final release beats any pre-release of the same core version; later dev builds beat earlier ones). Split the single `_get_latest_release()` into two independent paths: `_get_latest_stable_release()` (Production -- `/releases/latest`, GitHub's own latest-non-prerelease endpoint, plus a defensive explicit prerelease check) and `_get_latest_prerelease()` (Development -- the full `/releases` list, filtered to entries GitHub marked as a pre-release, highest version wins). `CHANNEL` selects which one runs. The two channels are structurally incapable of seeing each other's releases -- not a comparison rule, a fetch-path separation.
- `ui/main_window.py`: window title becomes `"Saffron Automation — v{APP_VERSION} (Development)"` for a Development build; Production's title is untouched.
- `ui/about_page.py`: added a "Channel" row (Development/Production) next to the existing Version/Build line.
- `installer/saffron_validator.iss`: added `#define MyChannel` driving `MyAppName`/`MyAppId` via an `#if` block -- Development gets its own name ("Saffron Automation (Development)") and a freshly-generated GUID, distinct from Production's, so a Development install never collides with or upgrades-over a Production one on the same machine (separate install directory, separate Add/Remove Programs entry, separate Start Menu group -- all derived automatically from the one `MyAppName` change).
- `README.md` / `UPDATER_README.md`: documented the channel system and, critically, the one manual step it depends on -- checking "Set as a pre-release" when publishing a Development build's GitHub release.

**Real bugs found during verification (not assumed -- a full build was actually run)**
1. **The frozen build crashed on startup with `ModuleNotFoundError: No module named 'httpx'`** the moment it was actually launched (not just imported from source). Root cause: `Saffron Automation.spec` had `hiddenimports=[]` and predates every Supabase-related dependency added across Milestones 1-9 -- PyInstaller's static analysis never saw `httpx`/`supabase`/`postgrest`/etc. as needed. Fixed by adding `collect_submodules()` for the full Supabase dependency tree (httpx, postgrest, supabase_auth, keyring, pydantic, cryptography, and their own sub-dependencies) to the spec file.
2. **That fix appeared to have zero effect on a rebuild** -- same exact crash, even after confirming `collect_submodules('httpx')` correctly returns 23 real modules when run directly. Root cause: `build_exe.ps1` invoked `.venv\Scripts\pyinstaller.exe`, a pip-generated console-script launcher that embeds an *absolute path* to whichever `python.exe` existed at the moment `pyinstaller` was originally installed -- baked into the executable itself, not read fresh from `pyvenv.cfg` the way `python.exe`/`pythonw.exe` do. Since this project's `.venv` folder was originally created at, and then copied from, a different path (`Saffron Employee Detector`, confirmed via `pyvenv.cfg`'s `command` field and confirmed empty of any Supabase packages), the launcher was silently running PyInstaller under that stale, pre-Supabase environment this entire time -- invisible to every `python.exe`-based test all Milestones 1-9 relied on, since those correctly self-locate. Fixed by invoking `python.exe -m PyInstaller` instead (in both `build_exe.ps1` and documented in README.md), which has no such indirection, plus `--clean` so a future dependency change can't hide behind a stale build cache the way this one initially did.
3. This second bug was **completely unrelated to release channels** -- it would have silently produced a broken installer for the *next Production release too*, whenever that next happens, if it hadn't been caught here. Fixed at the root (the build script itself) rather than worked around for this one release.

**Verification performed (the actual pipeline, not just code review)**
- `_parse_version` tested against 6 real comparison scenarios (dev-vs-dev ordering, dev-vs-final ordering both directions, v-prefix stripping, malformed input) -- all correct.
- `_get_latest_stable_release()` and `_get_latest_prerelease()` both called against the real, live GitHub API: stable correctly found the real published `v1.3.1`; prerelease correctly found nothing yet (none published) rather than falling back to the stable release.
- Full `check_for_updates()` run with this Development build's real version: confirms it does **not** offer the existing stable `1.3.1` release as an update, purely because the Development channel never asks the endpoint that could return it.
- Ran the actual, real build pipeline end-to-end, twice (once to find the PyInstaller bugs, once clean to confirm the fixes): `.\build_exe.ps1` -> `ISCC.exe installer\saffron_validator.iss` -> launched the resulting `dist\Saffron Automation\Saffron Automation.exe` directly. Confirmed via `Get-Process` it stayed running (not crashed), confirmed via its own log output that it initialized cleanly and correctly resolved its data directory to `%LOCALAPPDATA%\Saffron Validator\` (the real frozen-build path, not the source-run one).
- Inspected the compiled installer's actual Windows version resource (`Get-Item ... | .VersionInfo`): `ProductName: Saffron Automation (Development)`, `ProductVersion: 1.3.1-dev.0.1` -- confirms the `.iss` file's channel-conditional block genuinely took effect in the binary, not just that it compiled without a syntax error.
- Output file: `installer_output\Saffron Automation Setup v1.3.1-dev.0.1.exe`.

**Explicitly not done (by design, per instructions)**
- No changes to authentication, cloud sync, user management, or any other application functionality.
- No changes to the actual update-download/install mechanics (`download_installer`, `launch_installer`, `perform_update`) -- only which release gets selected changed.

**What's needed from the user to actually publish this**
- One manual step only the repo owner can do: go to https://github.com/dhairyagautam-creator/Saffron-Automation/releases, create a release tagged `v1.3.1-dev.0.1`, attach `installer_output\Saffron Automation Setup v1.3.1-dev.0.1.exe`, and check **"Set as a pre-release"** before publishing. Everything else (build, installer, channel logic) is already done and verified.

**Recommended next step**
- Publish the first Development pre-release (above), then confirm a second Development build (bump to `-dev.0.2`, leave `CHANNEL` alone) correctly shows as an available update to this one, closing the loop on "future Development builds require only changing the version number."

## Milestone 11a — Packaging Investigation: "Source File Is Corrupted" (2026-07-26)

A separately-reported install failure on a second machine ("An error occurred while trying to copy a file. The source file is corrupted", specifically for `Saffron Automation.exe`, consistently) was investigated end-to-end before assuming anything:
- Computed SHA256 of the local `v1.3.1-dev.0.1` build and of the exact asset downloaded fresh from GitHub -- **byte-for-byte identical** (same hash, same 52,260,267-byte size). Rules out corruption during build, ISCC compilation, or GitHub upload.
- Ran a real silent install (`/VERYSILENT /LOG=...`) of that exact file locally -- **exit code 0**, every file verified present on disk afterward at the correct size.
- Compared Production vs. Development `.iss` configuration line by line -- the only differences (`MyAppName`, `MyAppId`, and the install dir/group name derived from `MyAppName`) are pure identity metadata; the `[Files]`/compression settings are identical between channels.
- **Conclusion: no defect in the build, installer script, or channel implementation.** The installer itself is proven good. The failure is specific to that one download/machine -- most likely a corrupted/interrupted download, or antivirus modifying the file after download (consistent with it being a brand-new, zero-reputation file with no code-signing certificate). No code was changed as a result of this investigation, since none was warranted -- the second laptop's own retry, after this investigation, installed successfully.

## Milestone 11b — Real Root Cause: Packaged Build Had No Supabase Configuration At All (2026-07-26)

A second, genuinely critical issue: authentication worked on the original development machine but could not succeed at all on a second laptop running the same Development build. Investigated and fixed for real -- not a workaround.

**Root cause (two compounding defects, both real):**
1. **`.env` (holding `SUPABASE_URL`/`SUPABASE_ANON_KEY`) was never bundled into any packaged build.** It's correctly gitignored for security, but `Saffron Automation.spec`'s `datas` only ever listed `('assets', 'assets')` -- so any machine running *only* the installed application (no source checkout sitting next to it, which is exactly what "the original laptop" always had during all of Milestones 1-10's testing) had literally no Supabase credentials. `SUPABASE_URL`/`SUPABASE_ANON_KEY` were simply `None`.
2. **That failure was completely silent.** `get_supabase_client()` already raised a clear `RuntimeError` for missing config -- but `auth_service.sign_in()`, `auth_service.restore_session()`, and `rbac_service.load_profile_and_role()` all called it *outside* their `try/except` blocks. Since the real call happens on a background `threading.Thread` (see `ui/login_page.py`), an uncaught exception there is silently swallowed by the thread -- no callback ever fires, so the UI just sits on "Signing in..." forever with zero visible error. This is exactly why it looked like "cannot sign in" rather than "gets an error."

**What was fixed:**
- `Saffron Automation.spec`: added `('.env', '.')` to `datas` -- confirmed by direct testing that a frozen module's synthesized `__file__` makes `app/supabase_client.py`'s `Path(__file__).resolve().parent.parent` resolve to `_internal` itself, so `.env` needed to land at the root of `_internal`, not alongside `assets/`.
- `app/auth_service.py` / `app/rbac_service.py`: `get_supabase_client()` calls moved inside proper `try/except RuntimeError` handling in `sign_in()`, `restore_session()`, and `load_profile_and_role()` -- a missing/misconfigured Supabase client now returns a clear, specific `AuthResult`/`RbacResult` error instead of silently killing a background thread.
- Added step-by-step logging through the entire chain exactly as requested: client init, login request, login response (including the *exact* Supabase error code/message, e.g. `code='invalid_credentials' message='Invalid login credentials'` -- never a generic "login failed"), session persistence, profile lookup, role lookup, permission loading.
- `app/supabase_client.py`: added `log_config_status()` logging the resolved `.env` path, whether it was found, the non-secret URL, and a masked key -- but as a function called explicitly from `main()` *after* `configure_logging()` runs, not automatically at import time. A real bug was caught in this fix too: the module is imported transitively before `configure_logging()` ever runs (via `main.py`'s top-level imports), so logging at import time would have used loguru's unconfigured default handler (`stderr`) -- which is `None` on a windowed build, silently discarding the message entirely. Confirmed by testing: the log line was genuinely missing from the log file until this was corrected.

**Verification performed (against the real frozen build, not source):**
- Rebuilt, confirmed `.env` physically present at `_internal\.env` in the frozen output.
- Ran the actual frozen `.exe` and confirmed via its real log file: `Supabase config: ... found=True SUPABASE_URL=https://jnirrwlbbfelihloihds.supabase.co SUPABASE_ANON_KEY=eyJhbGciOiJI...(208 chars)`.
- Bumped to `1.3.1-dev.0.2` (the already-published `dev.0.1` asset on GitHub predates this fix and would still be broken -- a new version was necessary, not optional, to actually deliver the fix), rebuilt, recompiled the installer, and ran a **real, complete login** through the frozen build. Full step-by-step log confirmed: an intentionally-wrong first attempt correctly rejected with the exact Supabase error (`invalid_credentials` / `Invalid login credentials`), then a correct second attempt completing all 4 login steps, all 3 profile/role lookup steps, and permission loading, ending in `Session loaded: user='dhairyagautam@andrewsosborne.com' role='Admin' ...`.

**Files modified:** `Saffron Automation.spec`, `app/supabase_client.py`, `app/auth_service.py`, `app/rbac_service.py`, `main.py`, `app/version.py` (`1.3.1-dev.0.2`), `installer/saffron_validator.iss` (`MyAppVersion` to match).

**Output:** `installer_output\Saffron Automation Setup v1.3.1-dev.0.2.exe` (SHA256 `DC2E4D1F3A7D28E2256B1A3F2B600E69AB8675E509B6D2D0F6C4F5AD762580C9`, 52,261,797 bytes) -- ready to publish as a GitHub pre-release, superseding the broken `dev.0.1`.

**Recommended next step**
- ~~Publish `v1.3.1-dev.0.2`... confirm the second laptop can now install and successfully log in.~~ **Done and confirmed** -- `v1.3.1-dev.0.2` published, installed, and logged in successfully on the second laptop. This closes out the cross-machine authentication failure for real: the fix wasn't just verified locally, it was verified on the actual machine that originally couldn't authenticate at all.

## Milestones 12-17 — Full Path Validator Cloud Sync (2026-07-26)

Migrated the entire Path Validator module (Operations uploads, Organization Data uploads, findings/reviewer status, email/notification log, active session) to Supabase as the source of truth, with local SQLite as a rebuildable cache -- extending Milestone 9's config-sync pattern from "one JSON blob" to full row/file sync, and reusing the exact same generic-service philosophy.

**Key architecture decision:** `raw_visits`/`employee_hierarchy` have intentionally dynamic, per-upload schemas (see `database/models.py`'s own docstring) -- they are never mirrored row-by-row into Postgres. Instead, the original uploaded files go into Supabase Storage as the source of truth, and a laptop that doesn't have an import locally reconstructs it by downloading those files and re-running the existing, unchanged local pipeline (`save_import`, `calculate_metrics`) -- but deliberately never re-running rule evaluation for a remote-origin import, since findings/reviewer status are genuine decisions pulled from the cloud instead (two laptops independently re-evaluating the same import could diverge).

**What was built:**
- **Schema (Milestone 12):** `supabase/migrations/0009`-`0013` -- 2 Storage buckets (`path-validator-operations-uploads`, `path-validator-organization-data`) + 4 tables (`path_validator_imports`, `path_validator_active_session`, `path_validator_findings`, `path_validator_email_notifications`, `path_validator_organization_workbooks`), same audit-trigger/RLS shape as `0008_module_configurations.sql` (wide open to any `authenticated` user -- screen-level RBAC is the real gate, same precedent as Milestone 9, not silently tightened).
- **Generic Sync Service (`app/sync_service.py`):** added `push_rows`/`pull_rows`/`upload_file`/`download_file` alongside the untouched `push_config`/`pull_config` -- fully schema-agnostic (table/bucket name always caller-supplied), same three-tier exception handling. Confirmed clean of any Path-Validator-specific assumption.
- **Path-Validator sync wrappers (new):** `app/organization_data_sync_service.py`, `app/import_sync_service.py`, `app/findings_sync_service.py`, `app/email_sync_service.py` -- each owns its own cloud-shape knowledge, mirroring how `app/rule_parameters.py` wraps the generic config primitives.
- **Centralized poller (`app/sync_poller.py`, Milestone 17):** reuses `ui/email_center_page.py`'s self-rescheduling `.after()` timer pattern, but against Supabase (15s interval, much slower than that page's 1000ms since this is a speculative cross-machine check, not watching a known-in-progress send) instead of local in-memory state. Started once from `PathValidatorModule.__init__`; re-renders whichever page is currently visible via its own existing `on_show()` -- no new event-bus, none existed or was needed.
- **Local bookkeeping:** 5 new idempotent migrations in `database/migrations.py` (`cloud_id`/`synced_at`/etc. on `import_history`, `active_session`, `investigation_findings`, `email_notifications`, `workbook_connections`), mirrored on the ORM models in `database/models.py`.
- **UI wiring:** `ui/operations_page.py` (push import+findings+active session after a successful run; pull on `on_show`), `ui/organization_data_page.py` (push after Browse; pull on `on_show`), `ui/findings_page.py` (push a single finding's status after Mark Reviewed/Ignored/Reset). `app/notification_service.py` pushes email notifications (and findings, since sending mutates finding status) at the end of both `preview_email_batch` and `send_all_emails`.

**Bug found and fixed during testing (real root cause, not a workaround):** the first working version crashed on startup with `RuntimeError: main thread is not in main loop`, intermittently. Root cause: `OperationsPage.on_show()` (called once synchronously during `PathValidatorModule.__init__`, before `main.py` calls `mainloop()`) and `sync_poller.start()` (called right after) both spawned network-bound background threads immediately. If the Supabase round-trip happened to complete before `mainloop()` actually started, the thread's own `self.after(0, ...)` call to marshal its result back to the main thread failed, because Tk's event loop wasn't running yet to safely accept a cross-thread dispatch. Fixed by deferring the thread *spawn itself* (not just its result-handling) via `self.after(0, ...)`/`module.after(0, ...)` in `app/sync_poller.py`, `ui/operations_page.py`, and `ui/organization_data_page.py` -- guaranteeing the main loop is confirmed running before any network-bound thread starts. Confirmed fixed by running the full app construction + a live `mainloop()` window repeatedly with no recurrence.

**Explicitly not done (by design, per instructions):**
- No processing/send "claim" lock -- explicit user decision (small team, manual coordination on who runs Operations at a given time).
- `geocode_cache`/`hospital_lookup_cache` sync -- deferred; pure performance caches with no user-authored state, not in the required-sync list.
- No automatic backfill of pre-existing local-only data -- every row created before this shipped has `cloud_id = NULL` and won't appear on another laptop until a deliberate, manually-run backfill (not built; picking an arbitrary "whichever machine goes first" order isn't a decision this migration should make silently).
- "Generated reports" is mapped to the existing Email Notification Send Log data (`path_validator_email_notifications`) -- there is no separate report-export/PDF pipeline anywhere in this codebase to sync instead; confirmed with the user before implementing.

**Verification performed:**
- *Real, against the live Supabase project (not mocked):* the app's real anon-key client successfully round-tripped `pull_rows` calls to the new table names over the network (confirmed via log timestamps showing real ~1-2s round trips), correctly reporting `PGRST205` "table not found" since the SQL migrations haven't been applied to the Supabase project yet -- this is expected and non-fatal (caught, logged as a warning, app continues normally).
- *Local, real (not mocked):* ran `run_startup_migrations()` twice against the real Development database -- all 5 new column sets applied on the first run, zero new log lines on the second (idempotent, confirmed). All 5 touched ORM models query cleanly against the live schema.
- *Local, real:* constructed the full `MainWindow` and ran a live `mainloop()` repeatedly with no thread-safety errors after the fix above.
- **Not yet done -- requires your action:** the 5 SQL migration files (`0009`-`0013`) have not been run against the Supabase project (no dashboard/DDL access available to the assistant). **The actual two-laptop acceptance test from Milestone 17 -- leave Laptop B's Findings/Operations page open, perform a full import → analysis → status-change flow on Laptop A, confirm Laptop B updates within one poll interval with no restart -- has not been performed.** This is the same category of verification Milestone 9 and 11b insisted on before calling anything done; it genuinely requires two physical laptops and has not happened yet.

**Risks (named explicitly, matching this log's own convention):**
- RLS is wide-open-to-`authenticated` on findings/email data, which is more operationally sensitive than a config knob -- any logged-in user can rewrite any finding's status or fabricate a send-log entry directly via the API, not just through the app UI. Not tightened, per the existing `module_configurations` precedent -- worth a deliberate decision later.
- Re-running "Run Analysis" on an already-synced import deletes and recreates local `InvestigationFinding` rows (pre-existing behavior in `rules/same_location.py`, not introduced here) -- this orphans the previous `cloud_id`, so a re-run would push a duplicate cloud row rather than update the original. Not fixed in this pass; a real edge case beyond the core "process once, see it elsewhere" scenario.
- Storage never deletes anything (matches `import_history`'s own "never delete, never merge" design) -- unbounded growth over time, no retention/archival mechanism built.

**Files added:** `supabase/migrations/0009_path_validator_storage_buckets.sql` through `0013_path_validator_organization_workbooks.sql`, `app/organization_data_sync_service.py`, `app/import_sync_service.py`, `app/findings_sync_service.py`, `app/email_sync_service.py`, `app/sync_poller.py`.

**Files modified:** `app/sync_service.py`, `database/migrations.py`, `database/models.py`, `app/findings_service.py`, `app/notification_service.py`, `rules/same_location.py`, `ui/operations_page.py`, `ui/organization_data_page.py`, `ui/findings_page.py`, `ui/path_validator_module.py`.

**Recommended next step**
- Run the 5 new SQL migrations against the live Supabase project (Supabase SQL Editor, in numeric order), then perform the real two-laptop test described above before considering this migration actually done.

## Milestone 18 — Sync Service Generic Reusability Confirmation (2026-07-26)

Confirmed (not just assumed) that `app/sync_service.py`'s new `push_rows`/`pull_rows`/`upload_file`/`download_file` primitives added in Milestone 12 carry zero Path-Validator-specific knowledge -- table/bucket names, conflict columns, and filters are always caller-supplied, exactly like `push_config`/`pull_config` already were for Milestone 9. Verified directly: every one of Milestones 13-17's Path-Validator sync wrappers (`app/import_sync_service.py`, `app/findings_sync_service.py`, `app/email_sync_service.py`, `app/organization_data_sync_service.py`) supplies its own table/bucket names and row shapes into these generic functions; none of that shape knowledge leaked back into `app/sync_service.py` itself. No refactor was needed -- the module was already clean.

A future Inventory or Payments cloud-sync milestone can reuse these exact same primitives (`push_rows("inventory_thresholds", rows, on_conflict="cloud_id")`, etc.) with no new sync-service code, only a new thin wrapper module following the same pattern as `app/rule_parameters.py`/`app/import_sync_service.py`.

**Files modified:** none (confirmation pass only).

**Recommended next step:** none specific to this milestone -- see Milestone 17's recommended next step above, which gates the whole effort.

## Milestone 19 — Version Bump + Exe Build + Installer (2026-07-26)

**What was done:**
- Bumped `app/version.py`'s `APP_VERSION` to `1.3.1-dev.0.3` (CHANNEL stays `development`), and `installer/saffron_validator.iss`'s `MyAppVersion` to match.
- Ran the existing build checklist exactly as documented (`build_exe.ps1` → `python.exe -m PyInstaller --noconfirm --clean`, then Inno Setup's `ISCC.exe`) -- no changes needed to `Saffron Automation.spec`; confirmed `storage3` (the Supabase Storage client, needed by this migration's `upload_file`/`download_file`) was already covered by the existing `_SUPABASE_DEPENDENCY_PACKAGES` hidden-imports list from Milestone 10/11b.
- Confirmed `.env` is physically present at `dist\Saffron Automation\_internal\.env` in the frozen build (the exact landmine Milestone 11b root-caused) before compiling the installer.

**Output:** `installer_output\Saffron Automation Setup v1.3.1-dev.0.3.exe` (SHA256 `22A8DB86C692DB02800D1A80E4E202A2D5B874D7DBFA13A21988E3DB52E3A178`, 52,296,353 bytes).

**Explicitly not done:** the GitHub release itself was not created or published. This build bundles substantial new cloud-sync behavior that has not yet been verified end-to-end (the SQL migrations haven't been run against the live Supabase project, and the real two-laptop test from Milestone 17 hasn't been performed) -- publishing it, even as a pre-release, would ship unverified code to the Development channel's auto-updater. Per this project's own precedent (Milestone 11b: "a new version was necessary... to actually deliver the fix" was only claimed done after real verification), this build should not go out until that verification happens.

**Verification performed:** build and installer compile both completed successfully (exit code 0 on both); `.env` bundling confirmed present in the frozen output. The frozen build itself has not yet been launched and smoke-tested, nor installed/tested on a second machine.

**Recommended next step:**
1. Run the 5 SQL migrations (`0009`-`0013`) against the Supabase project.
2. Launch this frozen build and perform the real two-laptop test (Milestone 17's acceptance criteria).
3. Only after that passes: publish `v1.3.1-dev.0.3` as a GitHub pre-release, attaching `Saffron Automation Setup v1.3.1-dev.0.3.exe`, following the existing manual checklist (README.md / UPDATER_README.md).

## Milestone 20 — Module-Wide Refresh Button (2026-07-26)

The SQL migrations (`0009`-`0013`) were run against the live Supabase project between Milestone 19 and this one (confirmed: table-not-found errors changed to permission-denied-for-anon, i.e. the schema exists and RLS is correctly rejecting unauthenticated access, exactly as designed).

Moved "Refresh" from a Parameters-page-only button to a single, module-wide action, and used the opportunity to collapse three previously separate, duplicated pull code paths (the background poller's own pull calls, Operations page's on_show pull, Organization Data page's on_show pull) into one shared mechanism.

**What was built:**
- **`app/module_refresh_service.py`** (new) -- fully generic registry + async runner: `register_pull_operations(module_key, [...])`, `refresh_module_async(module_key, on_complete)`, `is_module_refreshing(module_key)`. Knows nothing about Path Validator -- a future Inventory/Payments module registers its own list under its own module_key with zero changes here, mirroring `app/sync_service.py`'s existing "generic layer, caller owns the shape" discipline.
- **`app/path_validator_refresh.py`** (new) -- the ONE place that defines what "Refresh" means for Path Validator: registers `[pull_and_apply_configuration, pull_new_imports, pull_active_session, pull_findings_and_emails_for_active_session, pull_workbooks]` against `module_refresh_service`, and exposes thin `refresh_now()`/`is_refreshing()` wrappers. Every caller (sidebar button, background poller) goes through this, never the individual pull functions directly.
- **`app/rule_parameters.py`**: added `pull_and_apply_configuration()` -- the same pull+apply combo `ui/parameters_page.py`'s old standalone Refresh button used to perform inline, now reusable.
- **`app/import_sync_service.py`**: added `pull_findings_and_emails_for_active_session()` -- closes a real gap the old poller had: if an import was *already* the shared active session on both machines, a reviewer status change or new send on one laptop had no trigger to reach the other (the old poller only pulled findings/emails as a side effect of the active session *changing*). Now pulled every refresh regardless.
- **`app/sync_poller.py`**: simplified to a thin trigger -- every tick just calls `path_validator_refresh.refresh_now()`, the exact same function the button calls, so there is one concurrency guard (inside `module_refresh_service`) shared by both, not two. If a manual click and a poller tick land at the same moment, the second one is a no-op, not an overlapping sync pass.
- **`ui/path_validator_module.py`**: added `_build_refresh_control()` -- one button + status label built into the sidebar shell (`_build_sidebar()`), so it's in the identical position regardless of which page is showing, exactly like the existing "← Back to Home" button. Click handler guards against concurrent runs (button disables + shows "Syncing…"; a click that lands while the poller is already mid-run shows "Already syncing…" rather than silently doing nothing), and on completion re-renders whichever page is currently visible via its own `on_show()` if anything actually changed.
- **Removed, not just hidden:** `ui/parameters_page.py`'s standalone Refresh button/`last_sync_label` and its `_on_refresh_clicked`/`_on_refresh_complete` methods (Save is untouched); `ui/operations_page.py`'s `_pull_cloud_changes_in_background`/`_spawn_cloud_pull_thread`/`_on_cloud_pull_complete`; `ui/organization_data_page.py`'s `_pull_workbooks_in_background`/`_spawn_pull_thread`/`_on_cloud_workbooks_changed`. Confirmed via grep: no page under `ui/` calls any individual `pull_*` function anymore -- only `app/path_validator_refresh.py` and `app/sync_poller.py` do.

**Explicitly not done:** the module-wide Refresh only pulls (matching "Refresh" semantics established by the original Parameters page button) -- it does not attempt to also push any locally pending, previously-failed pushes. Pushes remain tied to the specific user action that produced them (upload, save, status change), unchanged.

**Verification performed:**
- *Real, against the live Supabase project:* confirmed the 5 tables now exist (error changed from `PGRST205` table-not-found to `42501` permission-denied-for-anon).
- *Local, real:* ran the full app construction + a live `mainloop()` window; log confirms the poller's tick correctly invoked the shared `path_validator_refresh.refresh_now()`, ran all 5 registered operations in order, and completed cleanly with no thread-safety errors.
- *Not yet done:* clicking the actual button interactively (no GUI automation available in this environment) and the real two-laptop test remain outstanding, same gate as Milestone 17.

**Files added:** `app/module_refresh_service.py`, `app/path_validator_refresh.py`.

**Files modified:** `app/rule_parameters.py`, `app/import_sync_service.py`, `app/sync_poller.py`, `ui/path_validator_module.py`, `ui/parameters_page.py`, `ui/operations_page.py`, `ui/organization_data_page.py`.

**Recommended next step:** click the new Refresh button for real on both laptops as part of the still-outstanding two-laptop test; if that passes alongside Milestone 17's acceptance criteria, this and the prior milestones are ready for a new version bump + rebuild + GitHub pre-release.

## Milestone 21 — Version Bump + Rebuild for the Module-Wide Refresh Button (2026-07-26)

**What was done:**
- Bumped `app/version.py`'s `APP_VERSION` to `1.3.1-dev.0.3.1` (CHANNEL stays `development`), and `installer/saffron_validator.iss`'s `MyAppVersion` to match.
- Closed the previously-launched test instance of the v1.3.1-dev.0.3 frozen exe (would otherwise have locked the build output).
- Ran the same build checklist as Milestones 19/11b: `python.exe -m PyInstaller --noconfirm --clean`, then `ISCC.exe`. No spec/dependency changes needed -- this milestone was UI + a new pure-Python service module, nothing new to bundle.
- Confirmed `.env` present in the fresh `_internal\` output before compiling the installer.

**Output:** `installer_output\Saffron Automation Setup v1.3.1-dev.0.3.1.exe` (SHA256 `CFFB7D60F7D2B5807636D03477FADFC43F70704431D919505C8924753B9A7F2B`, 52,299,273 bytes).

**Explicitly not done:** GitHub release not created/published -- same reasoning as Milestone 19: the two-laptop test (now also covering the module-wide Refresh button itself) still hasn't happened, and this build should not reach the Development channel's auto-updater until it has.

**Verification performed:** build and installer compile both completed successfully; `.env` bundling confirmed. The frozen build has not yet been launched/smoke-tested post-rebuild, nor tested on a second machine.

**Recommended next step:** launch this specific build, click the new Refresh button for real, then perform the two-laptop test before publishing anything.

## Milestones 22-26 — Inventory Monitoring Cloud Sync (2026-07-26)

Confirmed by two-laptop testing: Path Validator cloud sync (Milestones 12-21) works end-to-end, including the module-wide Refresh button. This next phase extends the same architecture to Inventory Monitoring, per the explicit instruction: reuse the existing Sync Service, don't duplicate Path Validator's logic, keep one framework multiple modules register into.

**Analysis before implementing (as requested):** Inventory's data shape is fundamentally simpler than Path Validator's. `InventoryThreshold`/`InventoryReplenishment` are small, fixed-schema "current computed state" tables (not a growing history of past uploads like `import_history`), already upserted in place locally by the natural key `(branch_key, item_key)` -- no manufactured `cloud_id` needed, unlike Path Validator's autoincrement-ID tables. The original uploaded Excel files (Sales Report, Inventory Report) are never kept anywhere today, even locally -- parsed into a DataFrame and discarded -- so Inventory needs no Storage-bucket "sync the file, reparse elsewhere" design at all, only direct Postgres row sync. Confirmed no upload-history/audit-trail concept exists today (the Dashboard's "Recent Uploads"/"System Status" are hardcoded fake placeholders) -- explicitly left as out of scope, not silently built.

### Milestone 22 -- Generalize the sync poller + Refresh button (retrofit only, zero new capability)

Before adding Inventory, generalized the two pieces Milestone 20 had built specifically for Path Validator, so Inventory (and Payments later) reuse them verbatim instead of copy-pasting:
- **`app/module_sync_poller.py`** (new) -- module-agnostic version of the old `app/sync_poller.py` (deleted). Takes any module shell (duck-typed: needs `.winfo_exists()`, `.after()`, `.active_page`, `.pages`) + a `module_key`, calls the already-generic `app/module_refresh_service.py` directly -- no per-module wrapper function needed for the poller itself.
- **`ui/components.py`**: added `ModuleRefreshControl` -- the button+status-label+click-handler+completion-handler that used to be ~70 lines inline in `ui/path_validator_module.py`, now one reusable component taking `(module_shell, module_key)`.
- **`ui/path_validator_module.py`** retrofitted onto both, with zero behavior change -- verified via a live `mainloop()` test showing the poller correctly invoking the same shared mechanism as before.

### Milestone 23 -- Inventory schema + sync-wrapper modules

- **`supabase/migrations/0014_inventory_thresholds_sync.sql`**, **`0015_inventory_replenishment_sync.sql`** -- `(branch_key, item_key)` as the actual Postgres primary key (no separate id/cloud_id), same audit-trigger/RLS shape as every prior migration. Inventory Parameters need **no new table at all** -- they reuse the existing `module_configurations` table under a new `module_key="inventory_parameters"`, exactly like Path Validator Parameters do.
- **`app/inventory_sync_service.py`** (new) -- `push_thresholds`/`pull_thresholds`/`push_replenishment`/`pull_replenishment`, built on the generic `push_rows`/`pull_rows`. Full-table push/pull, not delta -- no `cloud_id`/`synced_at` bookkeeping columns needed anywhere, a deliberate simplification vs. Path Validator given the different data shape (confirmed with the user: mirror the existing upsert-only, never-delete-stale-rows local behavior exactly).
- **`app/inventory_parameters_service.py`** extended with `MODULE_KEY`, `get_full_configuration()` (flat dict, no `rule_name` nesting since Inventory has no rule sections), `apply_full_configuration()`, `pull_and_apply_configuration()` -- mirrors `app/rule_parameters.py`'s bridge almost exactly, actually simpler.
- **No local SQLite migration needed** -- `(branch_key, item_key)` was already the natural unique key on both tables before any of this work.
- No `is_developer_mode()` guards in the new sync code -- Inventory has no Developer Mode concept of its own (confirmed pre-existing in `database/models.py`'s docstrings), so it's simply irrelevant here, unlike every Path Validator sync module.

### Milestone 24 -- Wire pushes

- `ui/sales_upload_page.py`: pushes thresholds (inside the existing background-thread `work()`) right after `generate_thresholds_from_sales()` succeeds.
- `ui/inventory_upload_page.py`: pushes replenishment the same way, after `evaluate_replenishment()` succeeds.
- `ui/inventory_settings_page.py`: both Save buttons (multiplier and display mode are independent buttons sharing one cloud config blob) now push the combined configuration in a background thread after saving -- previously fully synchronous/local-only.

### Milestone 25 -- Wire the module-wide Refresh (manual only, per user decision)

- **`app/inventory_refresh.py`** (new) -- registers `[pull_and_apply_configuration, pull_thresholds, pull_replenishment]` under `module_key="inventory"`, mirrors `app/path_validator_refresh.py` exactly.
- **`ui/inventory_module.py`**: added the shared `ModuleRefreshControl` to the sidebar shell, same position convention as Path Validator. Deliberately **no** `module_sync_poller.start()` call -- Inventory gets the manual button only, confirmed with the user (Inventory is used less frequently than Path Validator; automatic polling wasn't wanted). Adding automatic polling later, if ever wanted, is a single added line -- no other change needed.

### Milestone 26 -- Verification

- *Local, real:* ran the full app construction + live `mainloop()` window with both modules present -- no thread-safety errors, no regressions.
- *Local, real, end-to-end:* directly invoked `app.inventory_refresh.refresh_now()` -- confirmed all 3 registered operations run in the correct order and the whole mechanism completes cleanly even when individual sub-pulls fail (expected: the new SQL migrations haven't been run against Supabase yet, so `inventory_thresholds`/`inventory_replenishment` correctly report "table not found," and `inventory_parameters` correctly reports permission-denied-for-anon since this test isn't logged in -- same non-fatal, logged-and-continue pattern as every other sync failure in this codebase).
- **Not yet done -- requires your action:** run `0014_inventory_thresholds_sync.sql` and `0015_inventory_replenishment_sync.sql` in the Supabase SQL Editor (same process as Milestone 12's files). The real two-laptop test for Inventory (upload a Sales Report + Inventory Report on Laptop A, confirm Laptop B sees the same thresholds/replenishment after clicking Refresh) has not been performed.

**Explicitly not done:** no upload-history/audit-trail feature was built for Inventory's Dashboard "Recent Uploads"/"System Status" widgets -- they remain the pre-existing hardcoded placeholders, out of scope for this cloud-sync effort.

**Files added:** `app/module_sync_poller.py`, `app/inventory_sync_service.py`, `app/inventory_refresh.py`, `supabase/migrations/0014_inventory_thresholds_sync.sql`, `0015_inventory_replenishment_sync.sql`.

**Files modified:** `ui/components.py` (`ModuleRefreshControl`), `ui/path_validator_module.py` (retrofit), `app/inventory_parameters_service.py`, `ui/sales_upload_page.py`, `ui/inventory_upload_page.py`, `ui/inventory_settings_page.py`, `ui/inventory_module.py`.

**Files removed:** `app/sync_poller.py` (superseded by `app/module_sync_poller.py`).

**Recommended next step:** run the 2 new SQL migrations, then test Inventory's upload → Refresh → see-it-on-the-other-laptop flow for real. Payments would follow this exact same pattern next -- register its own pull operations under `module_key="payments"`, no changes needed to `app/module_refresh_service.py`, `app/module_sync_poller.py`, or `ui.components.ModuleRefreshControl`.

## Milestone 27 — Version Bump + Rebuild for Inventory Cloud Sync (2026-07-26)

**What was done:**
- Bumped `app/version.py`'s `APP_VERSION` to `1.3.1-dev.0.4` (CHANNEL stays `development`), and `installer/saffron_validator.iss`'s `MyAppVersion` to match.
- Closed the previously-launched test instance of the frozen exe (would otherwise have locked the build output).
- Ran the same build checklist as every prior packaging milestone: `python.exe -m PyInstaller --noconfirm --clean`, then `ISCC.exe`. No spec/dependency changes needed -- Milestones 22-26 were pure Python + UI, nothing new to bundle.
- Confirmed `.env` present in the fresh `_internal\` output before compiling the installer.

**Output:** `installer_output\Saffron Automation Setup v1.3.1-dev.0.4.exe` (SHA256 `897F1E2C4D3B2DBB1807A297CE96C6C16CEEB85419E1B7C19A6236A5243F66DA`, 52,311,223 bytes).

**Explicitly not done:** GitHub release not created/published. This build bundles the new Inventory cloud sync (Milestones 22-26), which has not been verified end-to-end: the 2 new SQL migrations (`0014`/`0015`) haven't been run against the live Supabase project, and the real two-laptop test for Inventory (upload Sales/Inventory Report on one laptop, confirm the other sees updated thresholds/replenishment after clicking Refresh) hasn't been performed. Same standing rule as every prior packaging milestone: this should not reach the Development channel's auto-updater until verified.

**Verification performed:** build and installer compile both completed successfully; `.env` bundling confirmed. Not yet launched/smoke-tested post-rebuild, nor tested on a second machine.

**Recommended next step:** run the Inventory SQL migrations, launch this build, test Inventory's Refresh button and the Path Validator Refresh button both still work, then perform the two-laptop test for Inventory before publishing anything.

## Milestones 28-32 — Payment Analytics Cloud Sync (2026-07-26)

Confirmed by two-laptop testing: Inventory cloud sync works (manual Refresh, no automatic polling, as designed). This final phase extends the same architecture to Payment Analytics -- the third and last module in the original migration plan.

**Analysis before implementing (as requested):** Payments turned out more complex than Inventory -- three genuinely different sync shapes across four tables, not one:
- `payment_invoices`: no natural key (pure autoincrement `id`), append-only in the common case (Monthly upload) but wiped wholesale by a Historical Report re-run. Needs `cloud_id`/`updated_at`/`synced_at` bookkeeping, same shape as Path Validator's `investigation_findings`.
- `payment_active_months` / `payment_customer_profiles`: both already have natural keys, but unlike Inventory's upsert-only tables, both are genuinely delete-all-and-rebuilt locally on every upload (an evicted month, a customer who dropped out of the active window) -- upsert-only would leave orphans, so both sync as a full replace every time.
- `outstanding_invoices`: no natural key, and every upload is a full "Daily Refresh" (delete-all + reinsert) -- full replace on upload, plus a `cloud_id` added specifically so the `followed_up` checkbox toggle can address one row between uploads.
- No Supabase Storage needed -- confirmed the original uploaded reports are discarded after parsing here too, same as Inventory.
- Confirmed no upload-history/audit-trail concept exists (same "don't invent scope" precedent as Inventory) and RBAC (`payments_module`) already gates this module identically to the other two -- no new work needed there.

**Genuine new "special handling," not just reused framework:**
- **`app/sync_service.delete_rows(table, filters=None)`** (new, generic) -- every prior module only ever needed upsert (`push_rows`) or read (`pull_rows`); Payments is the first to need "clear this table," for the full-replace tables above. Verified via direct inspection of postgrest-py's source that a filter-less DELETE request matches every row RLS allows, before relying on it.
- **Historical Report cloud wipe requires an explicit user confirmation**, separate from local processing (which proceeds unconditionally, unchanged). Confirmed with the user: wiping shared cloud data is different from a normal upload, so `ui/payment_upload_page.py`'s `_HistoricalReportCard` now asks via `messagebox.askyesno` right after file selection (main thread, before the background work starts) -- declining skips only the cloud push, never the local replace.
- The 4 new Supabase migrations grant `delete` (new relative to every prior sync migration -- Path Validator/Inventory never needed it, since neither ever deletes cloud rows).

**What was reused directly, unmodified:**
- `app/sync_service.py`'s `push_rows`/`pull_rows`/`push_config`/`pull_config` -- no changes needed to any of them.
- `app/module_refresh_service.py` -- `register_pull_operations("payments", [...])`, same generic registry/lock/async-runner every module uses.
- `ui/components.ModuleRefreshControl` -- dropped into `ui/payment_analytics_module.py`'s sidebar verbatim, same as Inventory.
- The parameters cloud-sync pattern (`MODULE_KEY`/`get_full_configuration()`/`apply_full_configuration()`/`pull_and_apply_configuration()`) -- `app/payment_parameters_service.py`'s version is structurally closer to `app/rule_parameters.py`'s nested-by-rule-name shape than Inventory's flat version, since Payments also groups by rule name (Risk Scoring, Collections Ageing) -- just without the environment dimension Path Validator needs.

**What was newly added:**
- `supabase/migrations/0016_payment_invoices_sync.sql` through `0019_outstanding_invoices_sync.sql`.
- `app/payment_sync_service.py` (push/pull for all 4 tables, three different sync strategies as described above).
- `app/payment_refresh.py` (registration, mirrors `app/inventory_refresh.py`/`app/path_validator_refresh.py` exactly).
- Local SQLite: `cloud_id`/`updated_at`/`synced_at` on `PaymentInvoice`, `cloud_id`/`synced_at` on `OutstandingInvoice` (no local migration needed for `PaymentActiveMonth`/`PaymentCustomerProfile` -- their existing natural keys were already sufficient).
- UI wiring: `ui/payment_upload_page.py` (Historical -- gated push; Monthly -- automatic incremental push), `ui/payment_collections_page.py` (Outstanding Report full-replace push; follow-up toggle single-row push), `ui/payment_parameters_page.py` (push after Save), `ui/payment_analytics_module.py` (Refresh button, manual only -- no poller, matching Inventory and the user's explicit requirement).

**Explicitly not done / flagged as known limitations:**
- No automatic reconciliation if a Historical Report reset happens on one laptop while another still has the pre-reset invoices locally cached -- the next delta-pull won't remove stale local rows (flagged in `app/payment_sync_service.pull_invoices()`'s own docstring, same category of limitation as Path Validator's re-run-orphans-cloud_id gap).
- No upload-history/audit-trail feature built for "who uploaded what, when" -- none existed before this migration, none was added.

**Verification performed:**
- *Local, real:* ran the full app construction + live `mainloop()` window with all three modules (Path Validator, Inventory, Payments) present -- no thread-safety errors, no regressions.
- *Local, real, end-to-end:* directly invoked `app.payment_refresh.refresh_now()` -- confirmed all 5 registered operations run in the correct order and the whole mechanism completes cleanly even when every sub-pull fails (expected: the 4 new SQL migrations haven't been applied to Supabase yet, so every table correctly reports "table not found," non-fatal, logged and continued).
- **Not yet done -- requires your action:** run `0016`-`0019` in the Supabase SQL Editor. The real two-laptop test for Payments (upload a Monthly/Historical/Outstanding report on one laptop, toggle a follow-up checkbox, confirm the other laptop sees it after clicking Refresh) has not been performed.

**Files added:** `app/payment_sync_service.py`, `app/payment_refresh.py`, `supabase/migrations/0016_payment_invoices_sync.sql` through `0019_outstanding_invoices_sync.sql`.

**Files modified:** `app/sync_service.py` (`delete_rows`), `database/migrations.py`, `database/models.py`, `app/payment_parameters_service.py`, `ui/payment_upload_page.py`, `ui/payment_collections_page.py`, `ui/payment_parameters_page.py`, `ui/payment_analytics_module.py`.

**Recommended next step:** run the 4 new SQL migrations, then perform the two-laptop test for Payments. All three modules (Path Validator, Inventory, Payments) now share one synchronization framework end to end -- see the summary below.

---

## Summary: One Unified Synchronization Framework (all three modules)

**Reused across all three modules, unchanged:**
- `app/sync_service.py` -- `push_config`/`pull_config` (config), `push_rows`/`pull_rows` (rows), `upload_file`/`download_file` (files, Path Validator only), and now `delete_rows` (added for Payments, immediately usable by any future module too).
- `app/module_refresh_service.py` -- the single registry + concurrency lock + async runner every module's Refresh (button or poller) goes through. Three `module_key`s registered: `"path_validator"`, `"inventory"`, `"payments"`.
- `ui/components.ModuleRefreshControl` -- the one Refresh button+status-label implementation, used verbatim by all three module shells.
- `app/module_sync_poller.py` -- the one generic ticking-poller implementation. Path Validator uses it; Inventory and Payments deliberately don't (manual-only, per explicit requirement both times) -- enabling it later for either is a single added line, no new code.

**Genuinely new, module-specific work each time (by necessity, not duplication):**
- Path Validator needed Supabase Storage (`upload_file`/`download_file`) because `raw_visits`/`employee_hierarchy` have dynamic, per-upload schemas -- no other module needed this.
- Inventory needed nothing beyond the generic primitives -- its tables already had natural keys and pure upsert-only local behavior.
- Payments needed the new `delete_rows()` primitive -- the first module whose local behavior genuinely deletes rows (evicted months, dropped customers, daily-refreshed outstanding invoices) rather than only ever adding/updating them.

**Is there a single unified framework? Yes.** Every module-specific sync file (`app/import_sync_service.py`, `app/organization_data_sync_service.py`, `app/inventory_sync_service.py`, `app/payment_sync_service.py`) is a thin wrapper that only ever calls the same handful of generic primitives in `app/sync_service.py` and registers into the same `app/module_refresh_service.py` registry. None of the three modules' UI code talks to Supabase directly, and none of the generic framework files contain any Path-Validator/Inventory/Payments-specific knowledge -- confirmed by grep at each milestone (Milestone 18 did this explicitly for the first two modules; the same holds for Payments' new `delete_rows()`, which is just as module-agnostic as everything else in that file).

## Milestone 33 — Version Bump + Rebuild for Payments Cloud Sync (2026-07-26)

The 4 Payments SQL migrations (`0016`-`0019`) were run against the live Supabase project between Milestone 32 and this one (confirmed by the user).

**What was done:**
- Bumped `app/version.py`'s `APP_VERSION` to `1.3.1-dev.0.5` (CHANNEL stays `development`), and `installer/saffron_validator.iss`'s `MyAppVersion` to match.
- Confirmed no running instance of the frozen exe (none was open, no need to close anything this time).
- Ran the same build checklist as every prior packaging milestone: `python.exe -m PyInstaller --noconfirm --clean`, then `ISCC.exe`. No spec/dependency changes needed -- Milestones 28-32 were pure Python + UI, nothing new to bundle.
- Confirmed `.env` present in the fresh `_internal\` output before compiling the installer.

**Output:** `installer_output\Saffron Automation Setup v1.3.1-dev.0.5.exe` (SHA256 `5BA93CCF91E5C242D022767E39106DD1E16DEE22508B04F2E2470D6DC637F379`, 52,320,276 bytes).

**Explicitly not done:** GitHub release not created/published. This build bundles the new Payments cloud sync (Milestones 28-32), which has not been verified end-to-end: the real two-laptop test for Payments (upload a Monthly/Historical/Outstanding report on one laptop, toggle a follow-up checkbox, confirm the other laptop sees it after clicking Refresh) hasn't been performed. Same standing rule as every prior packaging milestone.

**Verification performed:** build and installer compile both completed successfully; `.env` bundling confirmed. Not yet launched/smoke-tested post-rebuild, nor tested on a second machine.

**Recommended next step:** launch this build, smoke-test all three modules' Refresh buttons (Path Validator, Inventory, Payments) plus the Historical Report cloud-wipe confirmation dialog, then perform the two-laptop test for Payments before publishing anything. Once that passes, this is the first build carrying complete cloud sync for all three modules -- Path Validator, Inventory, and Payments -- on one shared framework.

## Milestones 34-38 — Single App-Wide Last-Modified-Wins Rule (2026-07-27)

Per explicit instruction: the five different per-table sync strategies built up across Milestones 12-33 (delta-pull-only, full-table-replace-with-delete, natural-key upsert-only, file-based metadata reconstruction) were replaced with ONE bidirectional rule, centralized in the shared Sync Service, applied identically everywhere. Assumption stated by the user and relied on throughout: only one person edits a given module at a time during this phase.

### Milestone 34 -- The centralized reconciler

**`app/sync_service.reconcile_rows(table, local_rows, key_columns, updated_at_column="updated_at", filters=None)`** -- the one function every module's sync code now calls. Pulls the cloud table itself (optionally filtered), matches against the caller's local rows by `key_columns`, and decides per row:
- Cloud-only -> pull
- Local-only -> push
- Both exist, cloud's timestamp strictly newer -> pull
- Both exist, local's timestamp newer or equal (or either side unparseable) -> push

Returns a plan only (`to_pull`/`to_push`) -- never touches local SQLite or issues a push itself; every module applies the plan in its own way (its own ORM model, its own column names).

**A real prerequisite this surfaced:** two tables didn't have a genuine local "last modified" timestamp and needed one added before the rule could work correctly:
- `WorkbookConnection` (Organization Data) -- added `updated_at`, bumped in `app/workbook_connections.set_connection()`.
- `OutstandingInvoice` (Collections) -- added `updated_at`, bumped both at Outstanding Report upload time and on every follow-up checkbox toggle (`app/collections_service.set_follow_up()`). This one mattered functionally, not just cosmetically: without it, a follow-up toggle would have looked no newer than the cloud copy and could have been silently overwritten on the next Refresh.

Every other table already had a usable field: `ImportHistory.imported_at`, `ActiveSession.activated_at`, `InvestigationFinding.updated_at`, `EmailNotification.updated_at`, `InventoryThreshold`/`InventoryReplenishment.last_updated`, `PaymentInvoice.updated_at`, `PaymentActiveMonth.added_at`, `PaymentCustomerProfile.last_updated` -- all immutable-once-written or already-bumped-on-mutation, so they double as a reliable comparison key with no schema change needed.

### Milestones 35-37 -- Migrating every module

Every module-specific sync file was rewritten from a push_X()/pull_X() pair (or, for Payments, a delete_rows()-then-push_rows() full replace) into a single `sync_X()` function that calls `reconcile_rows()` and applies the resulting plan:
- **Path Validator**: `app/import_sync_service.py` (`sync_imports`, `sync_active_session`), `app/organization_data_sync_service.py` (`sync_workbooks`), `app/findings_sync_service.py` (`sync_findings_for_import`), `app/email_sync_service.py` (`sync_email_notifications_for_import`).
- **Inventory**: `app/inventory_sync_service.py` (`sync_thresholds`, `sync_replenishment`) -- previously "always push the full table" / "always pull the full table," which had no real conflict resolution at all (whichever action ran most recently silently won even if stale); now genuinely compares per row.
- **Payments**: `app/payment_sync_service.py` (`sync_invoices`, `sync_active_months`, `sync_customer_profiles`, `sync_outstanding_invoices`) -- the `delete_rows()`-based full-replace design and the Historical Report's cloud-wipe confirmation dialog were both removed entirely (see below).

**Immediate pushes right after a local action (upload, save, status/follow-up toggle) were kept, not removed** -- these are consistent with Last-Modified-Wins, not a deviation from it: a record just edited locally is unambiguously the newest copy at that instant, so pushing it immediately is the same decision the rule would make anyway, just made eagerly instead of waiting for the next Refresh click. Only the Refresh button's/poller's operations needed to change from one-directional pull to full bidirectional reconcile.

**What was removed as conflicting with the unified rule (per explicit instruction):**
- The Historical Payment Report's cloud-wipe confirmation dialog (`ui/payment_upload_page.py`) -- gone entirely; both Historical and Monthly uploads now call the exact same `sync_invoices()`/`sync_active_months()`/`sync_customer_profiles()` as everything else.
- `app/payment_sync_service.push_all_invoices_with_cloud_wipe()` and every `delete_rows()`-based full-table-replace call site.

### Known, explicitly-flagged trade-off: this rule has no concept of deletion

This is the one consequence worth being direct about, not just implementing quietly: **Last-Modified-Wins only ever pushes or pulls -- it never deletes.** A record removed locally (an evicted month in `payment_active_months`, a customer who dropped out of the active window in `payment_customer_profiles`, a previous Outstanding Report's rows, or every `payment_invoices` row from before a Historical Report reset) has no local counterpart on the next reconcile. If its cloud row still exists, **it will be pulled back**, effectively undoing the local removal.

This is a real behavior change from the previous design (which used `delete_rows()` specifically to prevent it) and is accepted here because it was explicitly requested and is consistent with the stated single-editor assumption. If this turns out to matter in practice (e.g., evicted months reappearing), the fix is a deliberate follow-up, not something this rule does automatically -- genuinely deleting a cloud row still requires an explicit `app.sync_service.delete_rows()` call, which no longer happens as part of any routine sync path.

### Verification performed

- *Local, real:* full app construction + live `mainloop()` window with all three modules present after the refactor -- no thread-safety errors, no regressions.
- *Local, real, end-to-end:* directly invoked `refresh_now()` for all three modules (`path_validator`, `inventory`, `payments`) -- confirmed all 13 registered operations (5 + 3 + 5) run through the new `reconcile_rows()` path and fail gracefully against the live Supabase project (expected: this test isn't authenticated, so every table correctly reports permission-denied-for-anon, non-fatal, logged and continued -- same pattern as every prior verification pass in this log).
- **Confirmed 2026-07-27, real two-laptop test:** Path Validator and Inventory synced correctly out of the box on v1.3.1-dev.0.6. Payment Analytics did not sync on first test -- diagnosed and fixed, see below.

**Files added:** none (Milestone 34's `reconcile_rows()` lives in the existing `app/sync_service.py`).

**Files modified:** `app/sync_service.py`, `database/migrations.py`, `database/models.py`, `app/workbook_connections.py`, `app/collections_service.py`, `app/import_sync_service.py`, `app/organization_data_sync_service.py`, `app/findings_sync_service.py`, `app/email_sync_service.py`, `app/notification_service.py`, `app/inventory_sync_service.py`, `app/inventory_refresh.py`, `app/payment_sync_service.py`, `app/payment_refresh.py`, `app/path_validator_refresh.py`, `ui/operations_page.py`, `ui/sales_upload_page.py`, `ui/inventory_upload_page.py`, `ui/payment_upload_page.py`, `ui/payment_collections_page.py`.

### v1.3.1-dev.0.6 -- two-laptop test and a pre-existing Payments data-loss bug found in the process

Built and tested on real hardware (not just the single-machine `refresh_now()` invocation above). Path Validator and Inventory both confirmed working end to end across two devices on the first try.

Payment Analytics initially showed as **not synced at all** -- diagnosed via `saffron_validator.log` (`%LOCALAPPDATA%\Saffron Validator\logs\`), not a bug in this milestone's new code:

1. On 2026-07-26, the Historical Payment Report was processed on the **previous build** (pre-Milestone-35/37, still running the old per-module sync design), building 199 customer profiles and an active-months window locally.
2. That old build's push to cloud failed immediately: Supabase rejected `delete_rows()`'s unqualified `DELETE` with `21000: DELETE requires a WHERE clause` -- a latent bug in the old delete-then-push design, unrelated to this milestone.
3. ~30 seconds later, that same old build's Refresh ran its old "**replace local entirely with whatever the cloud has**" pull for `payment_active_months`/`payment_customer_profiles`/`outstanding_invoices`. Since the cloud side was still empty (step 2 failed), this wiped the local 199 profiles and the active-months window down to zero -- silently, with no warning, exactly the class of bug this whole migration (Milestones 34-38) was undertaken to eliminate.
4. `payment_invoices` survived because it never used a replace-from-cloud pull design (push-only, immutable rows).

By the time v1.3.1-dev.0.6 (carrying the actual fix) was built and tested, the local damage from step 3 had already happened -- the new `reconcile_rows()` code correctly found nothing left locally to push for those two tables. This was not a flaw in the new sync logic; it was old data damage the new code couldn't retroactively repair, since the corresponding cloud data never existed either (blocked by the same step-2 failure).

**Resolution:** cleared `payment_invoices`, `payment_active_months`, `payment_customer_profiles`, and `outstanding_invoices` in Supabase (manual SQL, run directly by the user in the Supabase SQL Editor -- bulk cloud deletion is outside what this assistant will execute directly), then re-uploaded the Historical Payment Report on v1.3.1-dev.0.6. This rebuilt all four tables locally from scratch and pushed them cleanly via `sync_invoices()`/`sync_active_months()`/`sync_customer_profiles()` with no wipe and no confirmation dialog. **Confirmed working** on the second attempt, including across the two-laptop test.

**Recommended next step:** all three modules (Path Validator, Inventory, Payments) are now verified end-to-end on v1.3.1-dev.0.6 across two physical devices under the unified Last-Modified-Wins rule. Ready to publish this build to GitHub as a pre-release (Development channel) whenever desired.

## Milestone 39 -- Centralized hierarchy/fallback service (v1.3.1-dev.0.7)

Replaced the Organization Data table's separate ABM/RBM columns with one centralized reporting-hierarchy resolution service, `app/hierarchy_service.py`. Root causes fixed:

- **The Organization Data table never ran any fallback at all** -- it displayed the raw `abm_name`/`rbm_name` columns the parser wrote, which were hardcoded blank for every designation except BM/ABM/RBM (an RBM, Senior RBM, SM, AGM, or GM row always showed SQL `NULL`, which `pandas.read_sql_table()` turns into literal "NaN" on screen).
- **A vacant-position bug in `app/hierarchy_parser.py`**: a vacant ABM/RBM row was skipped without resetting the tracker used to assign ABM/RBM to the employees below it, so a BM under a vacant ABM silently inherited the wrong manager from the previous section instead of escalating to the RBM.
- The email notification system (`app/notification_service.py`) had a second, separate 5-rung fallback implementation, computed fresh per email and never shared with the table.

`app/hierarchy_service.compute_seniors()` now runs once per Organization Data refresh, resolving every employee's final Senior (BM → ABM → RBM → Senior RBM → SM → AGM → GM, skipping vacant/missing/no-email rungs) and storing it as `senior_code`/`senior_name`/`senior_email`/`senior_designation`. The Organization Data table and the email notification system both read these same stored columns -- neither re-derives the fallback independently, so they cannot disagree. A GM (nothing above them) shows "Top Level"; a genuine gap shows blank -- never `None`, so it can never round-trip into "NaN".

Added `tests/test_hierarchy_service.py` (15 tests, all passing) covering every fallback scenario plus the vacant-reset parser fix. Verified end-to-end with a synthetic workbook through `refresh_hierarchy()` and a live Organization Data page construction -- zero NaN, correct Senior at every level. Did not touch authentication, cloud sync architecture, Inventory, or Payments.

**Files added:** `app/hierarchy_service.py`, `tests/test_hierarchy_service.py`.

**Files modified:** `app/hierarchy_parser.py`, `app/notification_service.py`, `ui/organization_data_page.py`.

## Version 2.0.0 -- Production release

Per explicit direction: the approved v1.3.1-dev.0.7 build (Milestones 12-39, all three modules on cloud sync with the unified Last-Modified-Wins rule, plus the hierarchy centralization above) was promoted to the first Version 2.0.0 production release.

**"Main" project created:** since this was never a git repository, there was no branch to merge. A new sibling folder, `Saffron Automation - Main`, was created as a full copy of `Saffron Automation v2.0 - Development` (excluding regenerable build artifacts: `dist/`, `build/`, `installer_output/`, `__pycache__/`) -- confirmed to be a complete, independently-working copy (its own `.venv` verified functional from the new path). This is now the source of truth for production builds; `Saffron Automation v2.0 - Development` remains the ongoing working copy for future development.

**Version references updated:** `app/version.py` -- `APP_VERSION = "2.0.0"`, `CHANNEL = "production"`. `installer/saffron_validator.iss` -- `MyChannel = "production"`, `MyAppVersion = "2.0.0"`. The Production channel's App ID (`{3DF5DFE8-4CE9-4E88-BF6C-7BA966CC22CC}`) and install directory (`{localappdata}\Programs\Saffron Automation`) were left unchanged from prior Production releases (v1.0.0) specifically so Windows treats this as an in-place upgrade, not a new application -- existing users' local data (`%LOCALAPPDATA%\Saffron Validator`, entirely separate from the install directory) is untouched by the upgrade.

**A real bug found during final validation, not present in this milestone's own work:** installing and running the freshly-built v2.0.0 production package against the live Supabase project (real data: 578 employees, 142 findings) surfaced `TypeError('Object of type datetime is not JSON serializable')` when pushing `path_validator_findings` and `path_validator_email_notifications`. Root cause: `app/findings_sync_service.py:122` and `app/email_sync_service.py:88` (both from the Milestone 35 rewrite) set `row["updated_at"]` to a raw Python `datetime` instead of running it through the file's own existing `_serialize()` helper -- a one-line oversight per file, not present in Inventory's or Payments' sync services. Not caught earlier because every prior verification pass ran against an empty local database, so the buggy line was never exercised with real data. Fixed in both `Saffron Automation - Main` and `Saffron Automation v2.0 - Development` (so the same bug can't resurface in the next development build); rebuilt, reinstalled, and confirmed the fix is JSON-serializable via direct code-level verification (re-authenticating to re-test live against Supabase was not performed -- the local session had separately expired, an unrelated login/auth state issue, not a sync defect).

**Verification performed:** full PyInstaller build succeeded; Inno Setup production installer compiled and silently installed correctly to the expected upgrade-compatible location; `tests/test_hierarchy_service.py` (15/15) re-run clean in Main after the fix; About page confirmed to read `APP_VERSION`/`CHANNEL` dynamically (no hardcoded version strings found elsewhere); scanned for leftover debug `print()`/`TODO`/`FIXME` markers in application source -- none found outside one documented CLI diagnostic entry point (`app/supabase_client.py`'s `verify_connection()`, not part of the packaged GUI runtime).

**Not performed (explicitly out of scope for this assistant):** publishing the GitHub release / updating the live auto-update feed. The built installer and SHA256 are ready; the user will publish it themselves.

**Deliverables:** `installer_output\Saffron Automation Setup v2.0.0.exe`, `RELEASE_NOTES_v2.0.0.md`.

## Milestone 40 — New Development Copy Created From Main; Inventory Upgrade Phase 1: Exclude Ahmedabad CWH from CFA Calculations (2026-07-27)

**Housekeeping first, per explicit user instruction:** the old `Saffron Automation v2.0 - Development` folder had drifted from `Saffron Automation - Main` -- it predates Milestone 39 (hierarchy/fallback service) and the Version 2.0.0 production-release work above, so it was stale relative to the real, current codebase. A **new** sibling folder, `2.0 dev`, was created as a full copy of `Saffron Automation - Main` (excluding regenerable build artifacts: `dist/`, `build/`, `installer_output/`, `__pycache__/`; `.venv` copied as-is and confirmed functional from the new path, matching the same pattern used when `Main` itself was created). **`2.0 dev` is now the designated ongoing working copy for all future development** -- the old `Saffron Automation v2.0 - Development` folder should be treated as superseded/deprecated going forward, not used for new work.

The Inventory upgrade Phase 1 change below (Ahmedabad CWH exclusion) was first implemented and validated against the old, stale `Development` folder before this housekeeping was done; once the new `2.0 dev` copy existed, the same two edited files were copied over verbatim (confirmed via `diff` that `Main`'s and the old `Development` folder's pre-edit versions of both files were byte-identical, so no logic was lost or silently altered in the move) and the full regression suite was re-run against `2.0 dev` from scratch -- all checks passed there too.

Ahmedabad CWH (Central Warehouse) is the parent warehouse that feeds every actual CFA -- it was previously being treated like a normal CFA in threshold and replenishment calculations, which is incorrect. Per explicit instruction, this phase **only** excludes it from CFA-level calculation logic; it does not delete it from the imported dataset, does not build the future dedicated warehouse module, and does not touch UI, reports, or exports.

**Files modified (in `2.0 dev`)**
- `app/threshold_service.py`
- `app/replenishment_service.py`

**Logic changed**
- Added `is_ahmedabad_cwh_branch(value)` to `app/threshold_service.py` -- matches a raw CFA/BranchLocation cell against Ahmedabad CWH. Checks the **raw** value (before `normalize_branch_match_key`'s "text before first `(`" truncation) so that if CWH turns out to be encoded as a parenthetical location code (mirroring the confirmed "AHMEDABAD (C&F) ( SFP )" pattern), it isn't silently collapsed to the same key as a real Ahmedabad CFA row. Matches "cwh" as a whole word or "central warehouse", case-insensitively, anywhere after "ahmedabad" -- covers "Ahmedabad CWH", "AHMEDABAD (CWH)", "Ahmedabad (Central Warehouse)". Does not match "AHMEDABAD (C&F)" or "AHMEDABAD (HEAD OFFICE)", which remain normal CFAs.
- `generate_thresholds_from_sales()`: rows identified as Ahmedabad CWH are dropped from the working DataFrame **before** grouping/summing, so CWH sales never inflate any real CFA's `previous_month_sales` and no threshold row is ever created for it. Also deletes any pre-existing `InventoryThreshold` row matching Ahmedabad CWH at the start of every run, so a threshold captured by an upload that predates this fix doesn't linger. Returns a new `excluded_cwh_rows` count in its stats dict (additive -- existing keys unchanged).
- `evaluate_replenishment()`: rows identified as Ahmedabad CWH are dropped from the working DataFrame **before** any threshold lookup or demand/comparison logic runs, so CWH never factors into replenishment decisions. Also deletes any pre-existing `InventoryReplenishment` row matching Ahmedabad CWH at the start of every run. Returns the same new `excluded_cwh_rows` count, additively.
- The Inventory Dashboard's KPIs (`ui/inventory_dashboard_page.py`) and the Thresholds/Replenishment pages read exclusively from `get_replenishment_summary()`, `get_all_thresholds()`, and `get_replenishment_required()` -- all backed by the same two tables -- so excluding CWH at the calculation layer automatically excludes it everywhere downstream with no UI changes needed.

**Validation performed**
- Wrote a standalone regression script (not part of the committed test suite; `pytest` is not installed in this project's venv) that pointed `app.config.DATABASE_PATH` at an isolated scratch DB and exercised the real `generate_thresholds_from_sales()` / `evaluate_replenishment()` functions directly:
  - A normal CFA (RAIPUR) produces the **identical** `previous_month_sales` (150) whether or not Ahmedabad CWH rows are present in the same upload.
  - A real Ahmedabad CFA ("AHMEDABAD (C&F)") is **not** excluded and its threshold is **not** inflated by a co-uploaded "Ahmedabad CWH" row with deliberately large Sales (9999) -- stayed at 200.
  - Rows spelled "Ahmedabad CWH" and "AHMEDABAD (CWH)" are both excluded (2/2), produce zero threshold rows, and never appear in `get_all_thresholds()`.
  - An Inventory Report mixing real CFA rows with "Ahmedabad CWH" / "AHMEDABAD (CWH)" rows evaluates only the real rows (2 evaluated, 0 skipped-no-threshold, 0 CWH persisted to `InventoryReplenishment`), and `get_replenishment_summary()["total_evaluated"]` reflects only the real rows.
  - A manually pre-seeded stale `InventoryThreshold` row for "Ahmedabad CWH" (simulating data from before this fix) was confirmed purged on the next `generate_thresholds_from_sales()` call.
  - Re-ran this exact suite twice: once against the old (now-deprecated) `Development` folder, once against the new `2.0 dev` folder after the file copy -- both runs passed identically.
  - Also re-ran the existing `tests/test_inventory_module_shell.py` structural test (module builds, all pages cycle) manually against a fresh scratch DB -- passed, confirming no regression to the Inventory module shell.
  - All checks passed; script and scratch DB were scratch-only and have been deleted, not committed.

**Risks identified**
- **The exact literal spelling Ahmedabad CWH takes in a real uploaded report has not been confirmed against production data** -- no CWH-labeled sample file was available in this project at implementation time. The matcher in `is_ahmedabad_cwh_branch()` is a best-effort pattern built from the name given in the task ("Ahmedabad CWH (Central Warehouse)") and the one confirmed real parenthetical-suffix convention in this codebase ("AHMEDABAD (C&F) ( SFP )"). **Action needed:** re-check this pattern against the very first real report that actually contains an Ahmedabad CWH row, and adjust it if the real spelling differs.
- If the real Sales Report's CFA column and/or Inventory Report's BranchLocation ever represent Ahmedabad CWH with a value that fails this pattern, CWH rows would silently continue to be treated as a normal CFA (same failure mode the bug already had) -- this is a false-negative risk, not a risk of over-excluding a real CFA.
- The old `Saffron Automation v2.0 - Development` folder still exists on disk with its own (now out-of-sync) copy of this fix. It has not been deleted -- only superseded. No further work should target it; if it's confirmed unneeded, deleting it is a decision for the user to make explicitly, not something done automatically here.

## Milestone 41 — Inventory Upgrade Phase 2: Central Warehouse (CWH) Page (2026-07-27)

Ahmedabad CWH now has its own dedicated warehouse inventory dashboard, entirely separate from the CFA-facing Thresholds/Replenishment pages. Phase 1's exclusion (Milestone 40) is completely unchanged -- this phase only reads from it and adds new, independent storage/logic alongside it.

**New page:** "Central Warehouse (CWH)" -- added to the Inventory Monitoring sidebar between Replenishment and Settings. One row per SKU: Item Name, Division, SKU / Item Code, Physical Stock (CWH), Total Previous Month Sales (All CFAs), CWH Threshold, Surplus/Deficit, Status. Filters: Division dropdown, a Product/SKU search box, and a separate Item Name search box (three filters as specified, kept as two distinct search boxes rather than merged since the task listed "Product Search" and "Item Name Search" separately).

**Files added**
- `database/models.py` -- new `CwhStock` model (table `cwh_stock`), one row per `item_key` (there is exactly one CWH, so no branch-level key is needed, unlike `InventoryThreshold`/`InventoryReplenishment`). Stores `closing_stock`/`transit_stock` from the Inventory Report's Ahmedabad CWH rows, plus `item_code`/`item_name`/`last_updated`. New table only -- picked up automatically by `database.connection.init_db()`'s existing `Base.metadata.create_all()`, no migration script needed.
- `app/cwh_service.py` -- `evaluate_cwh_stock(df)` captures physical stock from a validated Inventory Report DataFrame (mirror image of `evaluate_replenishment()`: keeps exactly the rows that function excludes, via the same `app.threshold_service.is_ahmedabad_cwh_branch`, summing across spelling variants of CWH for the same item). `get_cwh_overview()` is the page's read model: total demand per item is summed live from `InventoryThreshold` across every `branch_key` sharing that `item_key` -- since Phase 1 guarantees `InventoryThreshold` never contains an Ahmedabad CWH row, this sum is already, by construction, every real CFA and only real CFAs, with no extra filtering required. `get_cwh_summary()` backs the page's empty-state logic. `CWH_THRESHOLD_MULTIPLIER = 2` is a hardcoded module constant, per explicit instruction -- not wired into Settings/`app/inventory_parameters_service.py` in this phase.
- `ui/inventory_cwh_page.py` -- the new page, built with the exact same components/patterns as `ui/inventory_thresholds_page.py`/`ui/inventory_replenishment_page.py` (`Card`, `SectionHeader`, `styled_treeview`, `EmptyState`, reload-on-`on_show()`), so it looks and behaves like a native part of the module rather than a bolt-on.
- New `icon_warehouse()` in `ui/icons.py` (a distinct building/warehouse glyph, not the existing crate-shaped Inventory Monitoring icon), registered for the `"Central Warehouse (CWH)"` nav key.

**Files modified**
- `ui/inventory_module.py` -- added `"Central Warehouse (CWH)"` to `BASE_PAGES` (between `"Replenishment"` and `"Settings"`) and registered `InventoryCwhPage` in `_build_pages()`. `ui/inventory_thresholds_page.py` and `ui/inventory_replenishment_page.py` themselves were **not** touched, per instruction.
- `ui/inventory_upload_page.py` -- one additive, guarded hook: right after `evaluate_replenishment(load_result["df"])`, now also calls `evaluate_cwh_stock(load_result["df"])` inside its own `try/except` (mirroring the existing `sync_replenishment()` guard immediately below it), so a bug in the new CWH capture path can never break the existing replenishment result the user is waiting on. No change to `validate_inventory_report`, `evaluate_replenishment`, the existing success/error banner logic, or the returned `replenishment_stats` shape. The Previous Month Sales Upload page (`ui/sales_upload_page.py`) needed **no** change at all -- "Total Previous Month Sales, all CFAs combined" is computed live from the already-stored `InventoryThreshold` table at read time, not captured at upload time, so there was nothing to hook there.

**Threshold logic**
- For every item: `total_previous_month_sales` = sum of `InventoryThreshold.previous_month_sales` across every CFA's `branch_key` for that `item_key` (Ahmedabad CWH is never in this set, by Phase 1 construction -- no extra exclusion logic was needed here).
- `cwh_threshold = total_previous_month_sales × 2` (hardcoded multiplier, per instruction).
- `surplus_deficit = physical_stock − cwh_threshold` (physical_stock = Ahmedabad CWH's `closing_stock` only; `transit_stock` is captured and stored but deliberately excluded from "Physical Stock" -- stock in transit to CWH isn't physically there yet, mirroring why `InventoryReplenishment` keeps closing/transit/effective-available as three distinct values rather than blending them). Displayed as a signed number (e.g. `"180"`, `"-45"`, `"0"`), matching the task's "Positive / Negative / Zero" requirement literally, since neither existing page had an established convention for showing genuinely negative values (Replenishment's own `stock_deficit` is only ever shown for already-filtered "Replenishment Required" rows, where it's always ≥ 0).
- Status: **Healthy** (green) if `cwh_threshold <= 0` (no CFA demand at all -- nothing to fall short of) or `physical_stock >= cwh_threshold`; **Low Stock** (orange) if the shortfall is ≤ 25% of the threshold; **Critical Shortage** (red) beyond that. The 25% cutoff is an explicit assumption (see below), not specified numerically in the task.
- An item with CFA demand but no CWH stock uploaded yet (or vice versa) still appears, with 0 on the missing side, rather than being silently dropped -- confirmed by a dedicated test case.

**Validation performed** (all against an isolated scratch DB, never the real `2.0 dev` database, via `app.config.DATABASE_PATH` override -- same pattern as Milestone 40's regression script; scripts and scratch DBs deleted afterward, not committed)
- ✓ Ahmedabad CWH still fully excluded from `InventoryThreshold`/`InventoryReplenishment`: re-ran Milestone 40's full regression suite unchanged -- all checks passed identically, confirming zero regression to Phase 1.
- ✓ `evaluate_cwh_stock()` correctly isolates only the Ahmedabad CWH rows from a mixed Inventory Report upload (3 CWH rows out of 4 total correctly identified and grouped into 2 unique items; the 1 real CFA row correctly left for `evaluate_replenishment()` alone) and sums stock across two different CWH spelling variants ("Ahmedabad CWH" + "AHMEDABAD (CWH)") for the same item into one row.
- ✓ Every SKU receives a correctly calculated warehouse threshold: verified `total_previous_month_sales` (sum of a synthetic 3-CFA sales split: 40+25+35=100, matching the task's own worked example), `cwh_threshold` (100 × 2 = 200), `surplus_deficit`, and status at multiple boundary points (`stock == threshold`, `stock` at exactly 75%/just-under-75% of threshold, `threshold == 0`).
- ✓ Total Previous Month Sales equals the sum of every CFA except Ahmedabad CWH: verified both synthetically and against the real, already-loaded production data in `2.0 dev` (103 real SKUs) -- **read-only**, no writes -- confirmed BENULIV's total across all 9 real CFAs it appears under (AHMEDABAD, AMBALA CANTT, GHAZIABAD, INDORE, JAIPUR, LUCKNOW, PATNA, RAIPUR, THRISSUR) sums to exactly 4870, producing a threshold of 9740, matching manual arithmetic.
- ✓ Physical Stock is read correctly: verified closing_stock sums correctly across spelling variants and that transit_stock is captured but excluded from the "Physical Stock" figure.
- ✓ Deficit/Surplus calculations are correct at every tested boundary (see above).
- ✓ Existing Inventory functionality remains unchanged: re-ran `tests/test_inventory_module_shell.py` (module construction + full page-cycle, now including the new page) against a fresh scratch DB -- passed; re-ran Milestone 40's Phase 1 suite -- passed; confirmed `ui/inventory_thresholds_page.py`, `ui/inventory_replenishment_page.py`, `app/threshold_service.py`, and `app/replenishment_service.py` were not modified by this milestone (only read by the new code, in the case of the two service modules).

**Assumptions made**
- The 25%-of-threshold cutoff between "Low Stock" and "Critical Shortage" (`CWH_CRITICAL_DEFICIT_RATIO = 0.25` in `app/cwh_service.py`) is not specified in the task -- chosen as a reasonable, commonly-used default. Easy to re-tune (one constant) once a real business rule is confirmed.
- "Physical Stock" = closing stock only, excluding transit stock in transit to CWH. `transit_stock` is still stored on `CwhStock` for future use/display.
- Division, for filtering purposes, is taken from the first non-blank `InventoryThreshold.division` found for that item across any CFA (same "first non-blank" convention `generate_thresholds_from_sales()` already uses) -- the Inventory Report itself carries no Division column.
- No pack-size rounding is applied to `cwh_threshold` (unlike `InventoryThreshold.packed_threshold`) -- the task's formula (`Total Sales × 2`) doesn't mention packing, so it's applied literally, raw.
- Designed with future cloud sync in mind per standing instruction: `CwhStock` is keyed by a single natural key (`item_key`, unique-constrained) and carries a `last_updated` timestamp bumped on every upsert, mirroring `InventoryThreshold`/`InventoryReplenishment` exactly, so a future `sync_cwh_stock()` could reuse `app.sync_service.reconcile_rows()` the same way `app/inventory_sync_service.py` already does for the other two tables -- **no sync was actually wired up in this phase**, per instruction (Settings/multiplier configurability also explicitly deferred).

Phase 3 (configurable multiplier in Settings, and any further CWH-specific features) is explicitly **not** started, per instruction.

## Milestone 42 — Inventory Upgrade Phase 3: Fix Real CWH Stock Reading (Issue 1) + Configurable CWH Multiplier (Issue 2) (2026-07-27)

Two confirmed fixes to the Central Warehouse (CWH) page from Milestone 41, per explicit follow-up instruction. Phase 1's exclusion mechanism and Phase 2's page/UI structure are otherwise unchanged.

### Issue 1 — Current Stock was never being captured, root cause found and fixed

**Investigation:** the real Current Inventory upload (`STOCKSTATEMENT_160726_05_06(Sheet1).csv`, confirmed against the file in `C:\Users\Hp\Downloads\`, matching exactly what's already loaded in `2.0 dev`'s live database) has 15 branch locations. Ahmedabad is the **only** city with two, both sharing the same branch code `SFP` (every other city has exactly one location with its own unique code) -- `AHMEDABAD (C&F) ( SFP )` and `AHMEDABAD (HEAD OFFICE) ( SFP )`. Milestone 40/41's `is_ahmedabad_cwh_branch()` matched neither (it only looked for "cwh"/"central warehouse"), so `evaluate_cwh_stock()` found 0 CWH rows in every real upload -- Current Stock was always 0, exactly the reported symptom. **User confirmed** (asked directly, since guessing wrong here risked either leaving the bug in place or wrongly reclassifying a real CFA): `AHMEDABAD (HEAD OFFICE) ( SFP )` **is** the Central Warehouse; `AHMEDABAD (C&F) ( SFP )` is the real Ahmedabad CFA.

**This also reveals and fixes a Phase-1 bug, not just a Phase-2 one:** `AHMEDABAD (HEAD OFFICE)` was being silently merged into the Ahmedabad CFA's own replenishment comparison this whole time (since Milestone 40), alongside `AHMEDABAD (C&F)` -- both collapsed to the same `"ahmedabad"` CFA-level threshold via `normalize_branch_match_key`. That was always wrong; it's corrected here as a direct consequence of correctly identifying the CWH row (the same `is_ahmedabad_cwh_branch()` function drives both the CFA-exclusion and the CWH-capture side).

**Fix:** `app/threshold_service._AHMEDABAD_CWH_PATTERN` extended to also match `ahmedabad.*\bhead\s+office\b` -- requires "ahmedabad" AND "head office" together, so it does **not** match any other city's own "(HEAD OFFICE)" row (every other city's sole real CFA location), only Ahmedabad's specifically. Verified this scopes correctly against RAIPUR/CHENNAI/every other city's "(HEAD OFFICE)" row (still real CFAs, unaffected) and against `AHMEDABAD (C&F)` (still the real Ahmedabad CFA, unaffected).

**Current Stock is read directly, never derived:** `evaluate_cwh_stock()` (unchanged in this respect from Milestone 41) takes `closing_stock` straight from the matched Ahmedabad CWH row(s)' own `TotalQty` column -- no estimation, no calculation from other values.

**Self-healing on the live database:** Phase 1's existing stale-row purge (inside `evaluate_replenishment()`/`generate_thresholds_from_sales()`, already present since Milestone 40) automatically removed 98 incorrectly-stored `AHMEDABAD (HEAD OFFICE)` rows from `inventory_replenishment` the moment the real file was reprocessed -- no manual cleanup code was needed, the existing mechanism just needed the corrected classification to act on.

### Issue 2 — CWH Threshold Multiplier is now a configurable Setting, deferred to the next processing run

**New setting:** "CWH Threshold Multiplier" on the Inventory Settings page, default `2.0`, free-form decimal entry (not a slider like the CFA multiplier -- the task's own examples, `1.5, 2, 2.25, 3`, include a value a 0.1-step slider can't land on exactly). Validated on save (must parse as a positive number).

**Deferred-effect behavior, matching the existing CFA Threshold Multiplier's own established convention exactly** (`app/inventory_parameters_service.py`'s module docstring already documented this pattern for the CFA multiplier before this milestone): saving a new CWH multiplier does **not** retroactively touch any already-generated `CwhStock` row. It is only read the next time `app.cwh_service.evaluate_cwh_stock()` runs (i.e. the next Inventory Report upload) and applies to that run's full re-evaluation, going forward, only.

**Architecture change required to make this correct -- not just "read the setting instead of a constant":** Milestone 41's `get_cwh_overview()` computed `cwh_threshold` **live**, every time the page was read, from whatever the multiplier happened to be at that instant -- so simply swapping the hardcoded `2` for a live setting read would have made changing the multiplier retroactively alter every already-displayed result immediately, the opposite of what was asked. Fixed by moving the entire calculation into `evaluate_cwh_stock()` itself (the one "CWH processing run", triggered only by an Inventory Report upload) and storing the results:
- `database/models.py`'s `CwhStock` gained `total_previous_month_sales`, `cwh_threshold`, `surplus_deficit`, `status` -- all now **stored**, computed once per processing run, never recalculated at read time.
- `evaluate_cwh_stock()` now reads the multiplier **once** at the top of each run, re-sums `total_previous_month_sales` fresh from `InventoryThreshold` for **every** item currently known from any Sales Report (not just items present in that run's CWH rows -- so an item with CFA demand but no CWH stock yet still gets a stored row), and computes/stores `cwh_threshold`/`surplus_deficit`/`status` for each. Items not mentioned in a given upload's CWH section keep their previously-stored stock (upsert-only, same no-silent-zeroing convention `InventoryReplenishment` already follows).
- `get_cwh_overview()` is now a **pure read** of `CwhStock` -- no calculation happens there anymore. Division is the one exception (still joined live from `InventoryThreshold` for display/filtering, since it's not part of the multiplier calculation the task is protecting).

**Cloud-sync-ready by construction, per standing instruction:** the new parameter (`cwh_threshold_multiplier`, default `"2.0"`) was added to `app/inventory_parameters_service.py`'s `get_full_configuration()`/`apply_full_configuration()` -- the exact same flat dict the existing CFA multiplier already uses. This means it rides the **existing** push (Settings page's Save button → `sync_service.push_config`) and pull (`app/inventory_refresh.py`'s `pull_and_apply_configuration`, already part of the module-wide Refresh cycle) with **zero new sync code** -- not "designed for later," actually wired into the existing cloud config mechanism now.

**Files modified**
- `app/threshold_service.py` -- `_AHMEDABAD_CWH_PATTERN` extended to match "ahmedabad" + "head office"; `is_ahmedabad_cwh_branch()` docstring rewritten to document the confirmed real convention (replacing the old "unconfirmed" risk note from Milestone 40).
- `app/inventory_parameters_service.py` -- added `CWH_THRESHOLD_MULTIPLIER`/`DEFAULT_CWH_THRESHOLD_MULTIPLIER` ("2.0"), `get_cwh_threshold_multiplier()`/`set_cwh_threshold_multiplier()` (mirroring the existing CFA multiplier functions exactly), wired into `ensure_defaults()`, `get_full_configuration()`, `apply_full_configuration()`.
- `database/models.py` -- `CwhStock` gained `total_previous_month_sales`, `cwh_threshold`, `surplus_deficit`, `status` columns; docstring rewritten to explain the snapshot-at-processing-time design.
- `database/migrations.py` -- new `ensure_cwh_stock_threshold_columns()` (adds the four new columns to an existing `cwh_stock` table from Milestone 41, defaulting existing rows to `0.0`/`"Healthy"` -- harmless since every row gets fully recomputed on the next real processing run anyway); registered in `run_startup_migrations()`. Applied to the live `2.0 dev` database directly (verified via `PRAGMA table_info`).
- `app/cwh_service.py` -- `evaluate_cwh_stock()` rewritten per the architecture above; `get_cwh_overview()`/`get_cwh_summary()` simplified to pure reads; removed the hardcoded `CWH_THRESHOLD_MULTIPLIER = 2` constant entirely (now sourced from Settings). `CWH_CRITICAL_DEFICIT_RATIO` (the Low Stock/Critical Shortage cutoff) is unchanged, still hardcoded -- not part of this instruction.
- `ui/inventory_settings_page.py` -- new "CWH Threshold Multiplier" card (free-form entry + Save/Reset + a deferred-effect warning message mirroring the CFA multiplier's own), wired into `on_show()`. `ui/inventory_cwh_page.py` (the page itself) needed no change -- it already only calls `get_cwh_overview()`/`get_cwh_summary()`.

**Files NOT modified, per instruction:** `ui/inventory_thresholds_page.py`, `ui/inventory_replenishment_page.py`, `app/replenishment_service.py`'s and `app/threshold_service.py`'s CFA-facing calculation functions (`generate_thresholds_from_sales()`, `evaluate_replenishment()` bodies) -- only the shared `is_ahmedabad_cwh_branch()` matcher they both call was corrected, which is exactly the fix Issue 1 required.

**Validation performed**
- ✓ Current Stock read directly from the Current Inventory upload: re-processed the real `STOCKSTATEMENT_160726_05_06(Sheet1).csv` (105 real products, 1575 unpivoted rows) end-to-end. `evaluate_cwh_stock` found and captured all 105 real Ahmedabad CWH rows (previously 0). Spot-checked BENULIV: `physical_stock = 14947` (read directly from its real CWH row's `TotalQty`), `total_previous_month_sales = 4870` (sum across its 9 real CFAs, unchanged from Milestone 41's read-only check), `cwh_threshold = 9740` (4870 × 2.0), `surplus_deficit = 5207`, `status = "Healthy"` -- all correct.
- ✓ Every SKU has correct Ahmedabad CWH stock: all 105 real products in the upload produced a `CwhStock` row (`cwh_rows_found=105`, `unique_items_in_upload=105`, `total_items_evaluated=105`).
- ✓ Deficit/Surplus and status colors now calculate correctly against real numbers (verified BENULIV above; also spot-checked BENUVO, BENULIV SYRUP 200 ML, BENUVO INJ DispoPack -- all correctly flagged "Critical Shortage" given real stock well below 2× their real combined CFA demand).
- ✓ Settings page contains the new configurable multiplier: added, follows the exact save/reset/validate pattern of the existing CFA multiplier card.
- ✓ Multiplier applies correctly after a new processing run, and only then: against the real data, confirmed BENULIV's stored `cwh_threshold` stayed at `9740` after changing the setting to `3.0` alone (no reprocess), then updated to `14610` (4870 × 3.0) only after re-running `evaluate_cwh_stock()` against the same real file. Multiplier then restored to the default `2.0` and the real file reprocessed once more, leaving the live database in its correct, real, default-multiplier state (`9740` for BENULIV) -- no lingering test value left behind.
- ✓ Existing CFA calculations remain completely unchanged: re-ran Milestone 40's full Phase-1 regression suite (updated for the one intentional behavior change: `AHMEDABAD (HEAD OFFICE)` now correctly matches as CWH, verified as a fix rather than a regression) -- all other checks passed identically. A dedicated new regression script additionally confirmed, using the real branch-naming convention: RAIPUR/CHENNAI/every other CFA's threshold and replenishment untouched; `AHMEDABAD (C&F)`'s replenishment row untouched; only `AHMEDABAD (HEAD OFFICE)` moved from (incorrectly) being evaluated as CFA replenishment to (correctly) feeding the CWH page.
- ✓ Ran the full `tests/test_inventory_module_shell.py` structural test against a fresh scratch DB (module builds, all 7 pages including the now-updated Settings page cycle correctly) -- passed.
- All scratch-DB regression scripts deleted after use, not committed. Real-database checks were read-only except for the two intentional, fully-restored multiplier round-trips described above.

**Assumptions made**
- No new assumption was needed for the CWH row's identity -- this was directly confirmed by the user rather than guessed, specifically because guessing wrong here risked breaking "existing CFA calculations remain completely unchanged."
- The `CWH_CRITICAL_DEFICIT_RATIO = 0.25` assumption from Milestone 41 is unchanged and still not user-configurable -- only the multiplier was in scope for this instruction.
- `get_cwh_summary()`'s `has_any_data` flag now simply checks whether any `CwhStock` row exists (since the page can no longer show a sales-only item with no processing run at all -- every item shown has been through at least one real Inventory Report processing run). This is a minor, low-risk behavior refinement of Milestone 41's empty-state logic, a natural consequence of the storage-at-processing-time redesign, not a separate feature.

Do not begin making the multiplier configurable further (e.g. per-item overrides) or building any other new feature -- stopping here, per instruction.

## Milestone 43 — CRITICAL: Validator Email Routing Engine Redesign (BM → RBM, not ABM; fixed fallback chain) (2026-07-28)

**High-priority bug fix, ready for merge/publish review.** Path Validator's manager-notification routing was sending BM validation emails to the ABM instead of the RBM, contradicting the Version 1.0 rule. Per explicit instruction, this was fixed with a proper redesign of the routing engine's internal model, not a one-line patch -- both because the underlying architecture (a single shared level list, sliced per designation) was itself the root cause, not just BM's specific entry, and because the same fragile pattern could silently reintroduce an equivalent bug for any future designation.

### Root cause

`app/hierarchy_service.py`'s `LEVELS = ["BM", "ABM", "RBM", "SRRBM", "SM", "AGM", "GM"]` plus `levels_above(designation)` derived each designation's escalation path by slicing this ONE shared list starting one rung above the employee. That happened to be correct for every designation except BM: `levels_above("BM")` returned `["ABM", "RBM", "SRRBM", "SM", "AGM", "GM"]`, so a BM's validation would try the ABM FIRST (routing there if the ABM had a name and email on file), only reaching the RBM if the ABM was unavailable. The general org-chart level ordering and the actual email-escalation business rule look like the same thing but are not -- deriving one from the other via a list slice is exactly what let this hide undetected.

### Redesign: an explicit routing table, not a derived slice

`app/hierarchy_service.py` now defines `FALLBACK_CHAINS: dict[str, list[str]]` -- the "internal routing model" the task asked for, expressed as a plain Python data structure (no flowchart needed in the UI, per instruction):

```
FALLBACK_CHAINS = {
    "BM":    ["RBM", "SRRBM", "SM", "AGM", "GM"],   # ABM deliberately absent
    "ABM":   ["RBM", "SRRBM", "SM", "AGM", "GM"],
    "RBM":   ["SRRBM", "SM", "AGM", "GM"],
    "SRRBM": ["SM", "AGM", "GM"],
    "SM":    ["AGM", "GM"],
    "AGM":   ["GM"],
    "GM":    [],
}
```

Diagram form (module docstring, internal only, matching the task's own example):

```
BM
 |
 v
RBM  ---------- (skips ABM entirely, by design)
 |
 v
Senior RBM (SRRBM)
 |
 v
SM
 |
 v
AGM
 |
 v
GM
 |
 v
(stop -- no further escalation)
```

Each entry is independently, explicitly listed -- not computed from another designation's chain -- so BM's fix couldn't accidentally affect (or be affected by) any other designation's chain, and no future designation's chain can be silently miscomputed by deriving it from a shared ordering again. `levels_above()` is replaced by `fallback_chain_for(designation)`, a direct table lookup (falls back to BM's own chain -- the safest, still-ABM-free default -- for a blank/unrecognized designation, same safety net the old code had).

**This is a fixed sequence, walked in order, not a recursive/tree search** -- exactly as instructed: at each named rung, if that specific person is unavailable (`is_valid_recipient()` -- vacant, missing, null, blank, or the row simply doesn't exist -- all treated identically, unchanged from before), move to the NEXT NAMED rung in the table; never skip ahead speculatively, never search sideways, never invent a rung not in the table.

**What did NOT change:** `DIRECTLY_ASSIGNED_LEVELS = {"ABM", "RBM"}` (how a rung's data is looked up -- still a direct field vs. a division+sheet designation lookup) is unchanged; `is_valid_recipient()` (the vacant/missing/null/blank check) is unchanged; every OTHER designation's own chain (ABM, RBM, SRRBM, SM, AGM) is byte-identical to what slicing produced before -- only BM's chain actually changed (ABM removed). `app/hierarchy_parser.py`'s actual parsing logic is completely unchanged -- RBM is already tracked as a direct per-BM-row assignment (`rbm_code`/`rbm_name`, set independently of ABM, confirmed by reading `_parse_sheet`), so no parser change was needed to make RBM resolvable first; only three docstring/comment mentions of the old chain ordering were corrected for accuracy.

### The Xandra HQ override (Muzaffarnagar/Saharanpur/Dehradun) -- untouched, confirmed unaffected

`app/notification_service.py`'s `_xandra_override_applies()`/`_xandra_override_recipient()` were **not modified** -- confirmed by reading `build_email_batch()` that this override is fully decoupled from `hierarchy_service`'s chain: it checks `finding.division`/HQ directly and, when it applies, looks up "Abhishek Sharma" by name and bypasses `senior_name`/`senior_email` (and therefore this fix) entirely. Verified this is still true and still works correctly (see Validation below).

### Files modified

- `app/hierarchy_service.py` -- `LEVELS`/`levels_above()` replaced with `FALLBACK_CHAINS`/`fallback_chain_for()`; module docstring rewritten to document the routing model, the bug, and the fix. `compute_seniors()`, `resolve_senior_from_maps()`, `resolve_senior()` updated to call `fallback_chain_for()` instead of `levels_above()` -- same call signatures, same callers, no changes needed anywhere else in the app.
- `app/hierarchy_parser.py` -- three docstring/comment mentions of the old "BM -> ABM -> RBM -> ..." chain corrected to describe the fixed table (no functional/parsing changes -- `_parse_sheet`'s RBM/ABM row-order tracking is untouched).
- `tests/test_hierarchy_service.py` -- `test_normal_bm_abm_reporting` (which asserted a BM's senior WAS the ABM -- the bug, encoded as a passing test) replaced with `test_bm_never_routes_to_abm_even_when_abm_is_valid` and `test_bm_with_only_abm_on_file_has_no_senior_abm_is_never_tried`; added `test_bm_routes_to_rbm_when_rbm_exists` and one test per validation scenario in the task (RBM vacant → SRRBM; RBM+SRRBM vacant → SM; RBM+SRRBM+SM vacant → AGM; RBM+SRRBM+SM+AGM vacant → GM); added `test_fallback_chain_for_bm_never_includes_abm`, `test_fallback_chain_for_other_designations_unchanged`, and `test_fallback_chain_for_unrecognized_designation_defaults_safely` testing the routing table directly. `test_rung_with_no_email_is_skipped_like_vacant` repurposed to exercise ABM's own escalation (RBM → SRRBM) instead of BM's, since its original ABM-based premise no longer affects BM's outcome at all. Every other pre-existing test (RBM→SRRBM→SM→AGM→GM's own escalation, division/sheet scoping, "Top Level", vacant-reset parser fix) is unchanged and still passes -- none of those designations' chains changed.

**Files NOT modified, per instruction:** `app/email_template.py`, `app/notification_service.py` (the general routing/send logic and the Xandra override -- both read-only consumers of the fixed `senior_name`/`senior_email` columns), `app/hospital_service.py`, `app/region_suppression.py`, anything under Inventory or Authentication.

### Validation performed

- ✓ Ran all 22 tests in `tests/test_hierarchy_service.py` manually (`pytest` is not installed in this project's venv, same as prior milestones) -- **all 22 passed**, including every scenario the task listed explicitly:
  - RBM exists → RBM (`test_bm_routes_to_rbm_when_rbm_exists`)
  - RBM vacant → Senior RBM (`test_bm_with_rbm_vacant_falls_to_srrbm`)
  - RBM + Senior RBM vacant → SM (`test_bm_with_rbm_and_srrbm_vacant_falls_to_sm`)
  - RBM + Senior RBM + SM vacant → AGM (`test_bm_with_rbm_srrbm_and_sm_vacant_falls_to_agm`)
  - RBM + Senior RBM + SM + AGM vacant → GM (`test_bm_with_rbm_srrbm_sm_and_agm_vacant_falls_to_gm`)
  - BM emails NEVER go to ABM, even when the ABM is present and fully valid (`test_bm_never_routes_to_abm_even_when_abm_is_valid` -- the direct regression test for the reported bug)
- ✓ Xandra override edge case verified with a standalone scratch-DB script (deleted after use, not committed): `_xandra_override_applies()` correctly matches Xandra + {Muzaffarnagar, Saharanpur, Dehradun} (case-insensitively) and correctly rejects every other division/HQ combination; `_xandra_override_recipient()` correctly resolves a real configured email and correctly returns `(None, None)` (Unresolved, no further fallback) when Abhishek Sharma's email is missing -- confirming the override still works and is untouched by this fix.
- ✓ **Real-data validation** (read-only, against the actual currently-connected Organization Data workbooks in `2.0 dev\cloud_cache\organization_data\{Onyx,Guardians,Xandra}.xlsx`): parsed and ran `compute_seniors()` over all 406 real BM rows across all three divisions. **Zero** resolved to an ABM as their senior (verified directly against `senior_designation`) -- confirmed distribution `{RBM: 373, SM: 33}`, every single one landing on a named rung from the fixed chain. Spot-checked several real BMs who have BOTH a valid ABM AND a valid RBM on file (e.g. "Vishnu Vijayan", abm="Abhijith R Nair", rbm="Pratheesh Thampan") -- all correctly resolve to their RBM, not their ABM. Confirmed "Abhishek Sharma" is a real Xandra employee (SRRBM designation, `abhisaffron30@gmail.com`) with a valid, currently-configured email, so the override would resolve correctly against real data too.
- ✓ Confirmed no other file in the codebase references `hierarchy_service.LEVELS`/`levels_above`/`DIRECTLY_ASSIGNED_LEVELS` by name (grepped the whole app/ui/rules/tests tree) -- safe to redesign internally with zero external call-site changes.
- ✓ Re-imported `app.hierarchy_service`, `app.hierarchy_parser`, `app.notification_service`, and `ui.organization_data_page` fresh -- all import cleanly, no breakage.
- All scratch DBs and regression scripts deleted after use, not committed; the real workbook check was entirely read-only (no database writes).

### Edge cases verified

- A BM whose ONLY upward assignment is an ABM (no RBM/SRRBM/SM/AGM/GM anywhere) now correctly shows a genuine blank Senior, not the ABM -- ABM is simply never in BM's routing sequence, so this is treated as a real data gap, not "nothing else exists, use the ABM."
- A BM whose `rbm_code`/`rbm_name` point at a code that doesn't exist in the hierarchy at all (vacant at parse time) still correctly falls through to Senior RBM -- unaffected by whether ABM happens to be valid.
- Every non-BM designation's own escalation (ABM→RBM, RBM→SRRBM, SRRBM→SM, SM→AGM, AGM→GM) and the division/source_sheet scoping for SRRBM/SM/AGM/GM lookups are confirmed unchanged.
- The Xandra HQ override's own internal "email not configured → Unresolved, do not fall back further" rule (a deliberate design choice, unrelated to this bug) is confirmed still intact.

### Live database refreshed so the Organization Data table reflects the fix immediately

The Organization Data table (`ui/organization_data_page.py`) does a straight `pd.read_sql_table` of the `employee_hierarchy` table's `senior_name`/`senior_designation` columns with zero client-side recomputation -- confirmed by reading `_load_hierarchy_from_db()`. This means the table was ALREADY guaranteed to reflect the fix with no UI code changes needed, but only once `employee_hierarchy` itself is regenerated (it's a `to_sql(..., if_exists="replace")` snapshot, not something that updates itself).

The `2.0 dev` app was already running with the pre-fix `employee_hierarchy` data still loaded, computed under the old buggy `compute_seniors()`. Checked before touching it: **376 of the live 406 real BM rows had `senior_designation='ABM'`** -- the bug, live, in the real database (e.g. "Vishnu Vijayan" showed senior "Abhijith R Nair" (ABM) instead of his real RBM "Pratheesh Thampan"). Called `app.hierarchy_parser.refresh_hierarchy()` for real against the live database (the exact same function the "Refresh Organization Data" button calls, using the already-connected real Onyx/Guardians/Xandra workbooks -- no new data, no schema change, just a re-run with the fixed engine). Confirmed after: **0 of 406 BM rows now show `senior_designation='ABM'`** (distribution `{RBM: 373, SM: 33}`); Vishnu Vijayan now correctly shows senior "Pratheesh Thampan" (RBM). The Organization Data table and the email notification system will both show these corrected values from this point on, with no further action needed -- they read the exact same regenerated column.

**Ready for merge/publish review, per instruction priority** -- this milestone does not itself merge into Main or publish; that remains a separate, explicit user action.

## Milestone 44 — CRITICAL: Simplify BM/ABM Routing to RBM-Only, No Fallback (2026-07-28)

Follow-up to Milestone 43, per explicit instruction: "Ignore the fallback system for now... simplify the logic completely... completely disable all fallback logic for now." Restores the most basic, verifiable version of the routing rule before trusting any fallback-beyond-RBM logic again.

**New rule (replacing Milestone 43's multi-rung BM/ABM chain):**
1. BM validation emails go ONLY to the RBM directly assigned to that BM.
2. ABM validation emails go ONLY to the RBM directly assigned to that ABM.
3. If that RBM is vacant, missing, null, or has no email on file, NO ONE ELSE is tried -- the finding is Unresolved. Logged and reported as **"RBM is Vacant. Email not sent."** Does NOT search upward to Senior RBM, SM, AGM, or GM.

In practice this is the ONLY resolution that ever mattered for actual emails even under Milestone 43's fuller chain: `rules/same_location.py`'s `ANALYZABLE_DESIGNATIONS = {"BM", "ABM"}` means no other designation is ever flagged by the rule engine, so RBM/SRRBM/SM/AGM's own escalation chains were never consulted for a real notification regardless -- only for the Organization Data table's Senior column on those rows. Those chains are therefore left completely unchanged here, per "do not make any other changes."

**Files modified**
- `app/hierarchy_service.py` -- `FALLBACK_CHAINS["BM"]` and `FALLBACK_CHAINS["ABM"]` changed from `["RBM", "SRRBM", "SM", "AGM", "GM"]` to `["RBM"]`. `_DEFAULT_CHAIN` (used for a blank/unrecognized designation) follows automatically, since it's defined as `FALLBACK_CHAINS["BM"]`. `RBM`/`SRRBM`/`SM`/`AGM`/`GM`'s own entries are untouched. Module docstring and `fallback_chain_for()`'s docstring rewritten to describe the simplified model and explicitly note this is a deliberate, temporary narrowing -- restoring the fuller chain later is a one-line table edit, not a redesign.
- `app/notification_service.py`:
  - Added a `logger.warning("RBM is Vacant. Email not sent. (Employee: ... Designation: ...)")` at the exact point a BM/ABM's hierarchy entry exists but no recipient could be resolved (scoped to skip this specific message when the Xandra HQ override applies instead, since that's a different, unrelated reason for the same code path).
  - Updated the existing "Unresolved findings -- manual review needed" report's per-finding reason text: was `"every rung of the reporting chain above this employee (ABM/RBM/Senior RBM/SM/AGM/GM, as applicable) is vacant, missing, or has no email on file"`, now exactly `"RBM is Vacant. Email not sent."` (accurate under the new rule -- there is only one rung to check, so this is always the reason when a hierarchy entry exists but resolution failed).
  - Module docstring and the Xandra-override block comment updated to describe the simplified routing rule instead of the old multi-rung chain.
- `tests/test_hierarchy_service.py` -- updated every test that assumed BM/ABM could fall through past RBM (these encoded the now-superseded Milestone 43 behavior): `test_fallback_chain_for_bm_never_includes_abm` → `test_fallback_chain_for_bm_is_rbm_only_no_fallback` (and a new `..._abm_is_rbm_only...` counterpart); the four "RBM+SRRBM(+SM)(+AGM) vacant falls to X" tests replaced with `test_bm_with_rbm_vacant_has_no_senior_no_fallback`, `test_abm_with_rbm_vacant_has_no_senior_no_fallback`, and `test_bm_with_no_rbm_at_all_has_no_senior_gm_is_not_used_as_fallback` (all now assert blank, not escalation); `test_full_chain_bm_to_gm` removed (contradicted the new rule outright); `test_rung_with_no_email_is_skipped_like_vacant` and `test_srrbm_sm_agm_gm_are_scoped_by_division_and_sheet` re-pointed to exercise RBM's own escalation instead of ABM's/BM's, since those two tests' original point (multi-rung fallback, division/sheet scoping) no longer applies to BM/ABM at all but is still real and still correct for RBM/SRRBM/SM/AGM. Added `test_abm_routes_to_rbm_when_rbm_exists` (the ABM-side counterpart of the existing BM test) to directly cover validation scenario 2.

**Validation performed**
- ✓ All 22 tests in `tests/test_hierarchy_service.py` pass, covering the three exact scenarios requested: BM+valid RBM → RBM; ABM+valid RBM → RBM; BM/ABM+vacant RBM → blank Senior, no fallback to SRRBM/SM/AGM/GM even when all four exist and are valid in the test data.
- ✓ **End-to-end integration check** (standalone scratch-DB script, not committed) exercising the REAL `app.notification_service.build_email_batch()` -- not just `hierarchy_service` in isolation -- with 4 synthetic Open findings (a BM and an ABM sharing a valid RBM; a BM and an ABM sharing a vacant-RBM). Confirmed: (a) both valid-RBM findings correctly grouped into one consolidated draft addressed to that RBM; (b) both vacant-RBM findings correctly landed in the "Unresolved findings — manual review needed" draft, whose body contains the exact required text "RBM is Vacant. Email not sent." for each; (c) the corresponding `logger.warning` fired exactly twice with that same exact message.
- ✓ Re-verified the Xandra HQ override (Muzaffarnagar/Saharanpur/Dehradun) is completely unaffected -- re-ran Milestone 43's dedicated override regression script unchanged, all checks passed (the override bypasses `senior_name`/`senior_email` entirely, so it was never touched by this simplification).
- ✓ Re-ran the full Inventory regression suite (Phase 1 CWH exclusion, Phase 3 real-stock/configurable-multiplier) and the Inventory module UI structural test -- all passed, confirming "do not make any other changes" held.
- ✓ **Real-data refresh**: re-ran `app.hierarchy_parser.refresh_hierarchy()` against the live `2.0 dev` database's already-connected real Onyx/Guardians/Xandra workbooks. Before: 484 real BM/ABM rows resolved to an RBM, 43 resolved to an SM (via the Milestone-43 fallback). After: the same 484 still correctly resolve to their RBM (unaffected, they already had a valid one); the 43 that previously fell through to SM now correctly show a blank Senior (`""`) instead -- exactly the expected effect of removing the SRRBM/SM/AGM/GM fallback for BM/ABM. The Organization Data table and email system both reflect this immediately, no further action needed.
- All scratch DBs and regression scripts deleted after use, not committed.

**Assumptions made**
- The task's numbered rules only specify BM and ABM's own routing; RBM/SRRBM/SM/AGM/GM's own escalation chains were left completely unchanged rather than also flattened, since (a) it wasn't asked for, (b) "do not make any other changes" argues against it, and (c) it has zero effect on real email routing regardless (those designations are never flagged findings).
- The exact log/report message uses the user-specified wording verbatim ("RBM is Vacant. Email not sent.") for every case where a BM/ABM's hierarchy entry exists but no valid RBM was found -- covering all four "vacant/missing/null/empty" cases named in the instruction, since they already all collapse to the same "no valid candidate" signal via the existing `is_valid_recipient()`/parser-level vacant-skip logic (unchanged, confirmed still correct).

This is a deliberate, temporary narrowing, not a permanent design decision -- re-enabling Senior RBM/SM/AGM/GM as BM/ABM fallback rungs later is a one-line edit to `FALLBACK_CHAINS` in `app/hierarchy_service.py`, not a redesign.

## Milestone 45 — Inventory Replenishment Page: Pack-Rounded Deficit, Previous Month Sales Column, Removed Threshold/Status, Raw/Packs Toggle (2026-07-28)

Four scoped Inventory Monitoring display/calculation improvements to the Replenishment page, per explicit instruction. `2.0 dev` only -- not merged to Main, not published, per this instruction's own explicit scope.

### Change 1 — Deficit rounds UP to the nearest pack, exactly like Threshold already does

`InventoryReplenishment.stock_deficit` (`packed_threshold − effective_available_stock`) is a raw subtraction of two numbers -- `packed_threshold` is already a multiple of `packing` (see `app/threshold_service._packed_threshold`), but `effective_available_stock` is not, so the *difference* routinely isn't a clean multiple of packing either (the task's own worked example: packing 30, calculated deficit 175 → must display 180). New `app.threshold_service.format_deficit_display(stock_deficit, packing)`, an exact structural mirror of the existing `format_threshold_display()`: reuses `_packed_threshold()` (the same "round UP to nearest multiple, never down" helper Threshold already uses) to round the deficit, then formats it per the current Threshold Display Mode. **Display-only** -- the stored `stock_deficit` value and the `replenishment_required`/`healthy` decision it's derived from are completely unchanged; only what's shown is rounded, computed fresh on every read, never persisted.

Verified against the task's own exact example: `format_deficit_display(175, 30) == "180"`.

### Change 2 — Previous Month Sales column added

`app.replenishment_service.get_replenishment_required()` now also looks up each row's `previous_month_sales` from `app.threshold_service.get_thresholds_lookup()` -- the exact same dataset `generate_thresholds_from_sales()` populates and `evaluate_replenishment()` itself already reads for the threshold comparison -- via the identical `(cfa_key, item_key)` key `evaluate_replenishment()` uses to find this row's threshold in the first place. No new column stored; joined fresh at read time (no schema change, no migration needed), matching the "read-only, display-time" pattern already used for `threshold_display`/`format_threshold_display()`.

### Change 3 — Threshold and Status columns removed from the Replenishment table

`ui/inventory_replenishment_page.py`'s `COLUMNS`/`HEADINGS`/`WIDTHS` no longer include `threshold_display` or `status`. `get_replenishment_required()` no longer computes/returns `threshold_display` at all (nothing reads it anymore); `status` is still included in the returned dict (still the row's own filter value, `STATUS_REPLENISHMENT_REQUIRED` -- every row on this page has the same one, and `get_replenishment_summary()` still uses status elsewhere), simply no longer rendered as a table column.

### Change 4 — Raw Quantity / Packs toggle extended to the Replenishment page

The existing Threshold Display Mode setting (`app/inventory_parameters_service.py`, unchanged) now also drives the Replenishment page's Deficit column, via the same `format_deficit_display()` from Change 1 -- no new setting, no new toggle, the existing one now has a second consumer. In Raw Quantity mode the already-pack-rounded number displays plainly (e.g. `"180"`); in Packs mode it additionally shows the pack count (e.g. `"180 (6 Packs)"`). Previous Month Sales (Change 2) is deliberately NOT pack-rounded or mode-aware -- it's a sales quantity, not a threshold/deficit, and the task didn't ask for pack rounding there; formatted with a small local `_format_quantity()` helper in `app/replenishment_service.py` (mirroring the same convention already used locally in `app/threshold_service.py` and `app/cwh_service.py`, rather than importing a private helper across modules).

**Files modified**
- `app/threshold_service.py` -- new `format_deficit_display()`.
- `app/replenishment_service.py` -- `get_replenishment_required()` rewritten: joins `previous_month_sales` from `get_thresholds_lookup()`; adds `previous_month_sales`/`previous_month_sales_display`/`deficit_display`; no longer computes `threshold_display`. New local `_format_quantity()` helper. `evaluate_replenishment()` itself (the actual threshold-comparison/replenishment-decision logic) is **completely unchanged**.
- `ui/inventory_replenishment_page.py` -- `COLUMNS`/`HEADINGS`/`WIDTHS` updated: `previous_month_sales_display` and `deficit_display` added; `threshold_display` and `status` removed. Module docstring updated.

**Files NOT modified:** `app/threshold_service.generate_thresholds_from_sales()`, `app/replenishment_service.evaluate_replenishment()` (the actual calculation/decision logic), `ui/inventory_thresholds_page.py` (Thresholds page keeps its own Threshold column, unaffected), `database/models.py` (no schema change -- both new display values are computed at read time, not stored).

**Validation performed** (isolated scratch DB, `app.config.DATABASE_PATH` override; script deleted after use, not committed)
- ✓ Deficit always rounds UP to the nearest pack: unit-tested the task's exact example (`175`/packing `30` → `"180"`); integration-tested a full row (`packed_threshold=150`, `effective_stock=5` → raw `stock_deficit=145`, not a multiple of 30 → `deficit_display="150"`, the correctly-rounded value, in Raw mode).
- ✓ Replenishment (i.e. the same Deficit value, per the interpretation below) always rounds UP -- same check as above; there is one shared numeric field for this page, not two independent ones (see Assumptions).
- ✓ Previous Month Sales column displays correct values: verified `100` (matching the exact sales figure that generated the row's own threshold) both raw (`previous_month_sales=100.0`) and formatted (`"100"`), unaffected by the Raw/Packs toggle either way.
- ✓ Threshold and Status columns removed: confirmed `"threshold_display"` key is absent from every returned row; confirmed `"status"` is still present in the dict (still used internally) but is no longer part of the page's displayed `COLUMNS`.
- ✓ Raw/Packs toggle updates the Replenishment table correctly: same row's `deficit_display` verified as `"150"` in Raw mode and `"150 (5 Packs)"` in Packs mode, toggled back and forth via `set_threshold_display_mode()`; `previous_month_sales_display` confirmed unaffected by the toggle in either mode.
- ✓ Existing Inventory calculations remain unchanged: `replenishment_required`/`healthy` counts confirmed identical (1 requiring replenishment, 0 healthy) before/after: exercised via `get_replenishment_summary()`. Re-ran the full Milestone 40/42 Phase 1 and Phase 3 CWH regression suites, all 22 `tests/test_hierarchy_service.py` tests, and the Inventory module UI structural test (constructs the real `InventoryReplenishmentPage` with the new columns and cycles all 7 pages) -- all passed, confirming this change didn't disturb anything else.
- ✓ **Real-data spot check** (read-only) against `2.0 dev`'s live database (365 real replenishment-required rows): confirmed real rows correctly show pack-rounded deficits with real packing values (e.g. `"360 (5 Packs)"`), real Previous Month Sales figures, no `threshold_display` key on any row, and `status` still present but unused for display.

**Assumptions made**
- The task's Change 1 names both "Deficit" and "Replenishment Quantity" as things to round, and Change 4 lists both "Deficit" and "Replenishment" as things the toggle affects. The current schema/UI has exactly ONE such field (`stock_deficit` / the "Stock Deficit" column, now relabeled "Deficit" per the task's own wording) -- there is no second, distinct "Replenishment Quantity" value anywhere in the codebase to round separately. Treated "Deficit" and "Replenishment Quantity" as the same value referred to by two names (this IS the quantity that needs to be replenished), rather than inventing a second column. If a genuinely separate "Replenishment Quantity" (e.g. distinct from the shortfall, incorporating a safety margin or different rounding rule) was intended, that needs to be clarified -- easy to add as a second column following the exact same `format_deficit_display()` pattern once the distinct calculation rule is known.
- Change 4's "Display: Threshold (where applicable) ... as raw units" (Raw Quantity mode) was read as a general restatement of the toggle's existing, unchanged behavior on the Thresholds page ("where applicable" = there, not here) rather than a contradiction of Change 3's explicit instruction to remove the Threshold column from the Replenishment page specifically.
- The pack-rounding rule always applies (in both Raw and Packs display modes) -- "Raw Quantity" mode means "show the plain rounded number without the pack-count annotation," not "show the unrounded raw difference." This mirrors exactly how Threshold's own existing Raw/Packs toggle already behaves (`packed_threshold`, itself already rounded, is what's shown either way; the mode only adds/omits the "(N Packs)" suffix).

## Milestone 46 — Replenishment Page: Ahmedabad CWH Availability Shortage Highlight (2026-07-28)

Prevents replenishment requests from silently exceeding what's physically available at Ahmedabad CWH -- a pure visual warning, per explicit instruction. `2.0 dev` only -- not merged to Main, not published, per this instruction's own explicit scope.

### What

For every row in the Replenishment table, its Required Replenishment Quantity (the same pack-rounded Deficit figure introduced in Milestone 45) is now compared against the Current Physical Stock of that SAME SKU at Ahmedabad CWH (already extracted from the Current Inventory upload -- see Milestone 44's `app.cwh_service.evaluate_cwh_stock()`). If CWH stock is less than the required quantity, the row's Deficit cell -- and ONLY that cell -- is highlighted in a bright red. No quantity is modified, no recalculation happens, and the replenishment amount itself is never reduced; this is strictly a visual availability warning layered on top of the existing, unchanged calculation.

Per the task's explicit "DO NOT ADD" list, this change deliberately does **not**: add any new column, change any export, touch the CWH page/calculations (`get_cwh_overview()`, `get_cwh_summary()`, `evaluate_cwh_stock()` are all untouched), compute CWH surplus/deficit, or introduce any warehouse-planning logic. It only reads CWH's already-stored physical stock number and compares it, per-SKU, against a number the Replenishment table already calculates.

**Matching:** comparison is keyed on `item_key` (the same normalized item-matching key every other Inventory comparison in this codebase uses) -- every SKU is compared only against that exact SKU's own Ahmedabad CWH stock, never a different product's. An item with no CWH stock record at all (never uploaded at CWH) is treated as 0 physical stock available -- a real shortage, not silently skipped.

### Files modified

- `app/cwh_service.py` -- new `get_cwh_stock_lookup() -> dict[str, float]`: a lean `{item_key: closing_stock}` read of `CwhStock`, for callers that only need the raw physical-stock number, not the full per-item overview `get_cwh_overview()` builds. Read-only; does not touch `evaluate_cwh_stock()` or any CWH threshold/surplus/deficit/status calculation.
- `app/threshold_service.py` -- new public `round_up_to_pack(value, packing) -> float`: the same "round UP to the nearest multiple of packing, never down" rule already used internally by `_packed_threshold()`, exposed as a reusable public function since both `format_deficit_display()` (Milestone 45) and this milestone's shortage comparison need the same *numeric* rounded value, not just a formatted string. `format_deficit_display()` refactored to call it instead of the private `_packed_threshold()` (zero behavior change -- same rounding rule, same inputs). `_packed_threshold()` itself, and every other threshold calculation, is untouched.
- `app/replenishment_service.py` -- `get_replenishment_required()` now also computes, per row: `required_quantity = round_up_to_pack(row.stock_deficit, row.packing)` (the number, not just the display string), looks up `cwh_physical_stock = get_cwh_stock_lookup().get(row.item_key, 0.0)`, and sets `cwh_shortage = cwh_physical_stock < required_quantity`. Both `cwh_physical_stock` and `cwh_shortage` are added to the returned dict; every existing field (`stock_deficit`, `deficit_display`, `status`, etc.) and the replenishment decision itself (`evaluate_replenishment()`) are completely unchanged.
- `ui/theme.py` -- new `Color.CRITICAL_RED` (`#FF0000`) / `Color.CRITICAL_RED_TEXT` (`#FFFFFF`), a more vivid red than the existing muted `Color.ERROR`, used only for this cell highlight.
- `ui/inventory_replenishment_page.py` -- per-cell highlight, since `ttk.Treeview` (via `styled_treeview()`, unmodified) only supports row-level `tag_configure()` coloring, not per-cell: for every row where `cwh_shortage` is True, an overlay `tk.Label` is placed directly over that row's Deficit cell using `tree.bbox(item_id, "deficit_display")` pixel coordinates (`_position_shortage_overlays()`), parented directly to the `tree` widget so bbox coordinates apply directly and the overlay is destroyed automatically whenever the tree itself is rebuilt/destroyed. `_wire_shortage_overlay()` re-points the tree's `yscrollcommand`/`xscrollcommand` to wrapper functions that call the original scrollbar `.set` (keeping scrollbar thumbs correct) and then reposition overlays, plus a `<Configure>` binding for window/column resizes -- so the highlight correctly tracks the cell through scrolling and resizing instead of drifting. No changes to `styled_treeview()` itself, to any other page using it, or to the existing row-level `WARNING_SOFT` tinting already applied to every row on this page.

**Files NOT modified:** `app/cwh_service.evaluate_cwh_stock()`, `get_cwh_overview()`, `get_cwh_summary()` (CWH's own calculations/page, per the explicit "do NOT modify the CWH page" instruction); `ui/components.styled_treeview()`; `database/models.py` (no schema change -- `cwh_physical_stock`/`cwh_shortage` are computed at read time only, never stored); no new columns added to `COLUMNS`/`HEADINGS`/`WIDTHS`; no export logic touched anywhere.

### New comparison logic

For each Replenishment row: `required_quantity = round_up_to_pack(stock_deficit, packing)` (same number `deficit_display` already renders) compared against `cwh_stock_lookup.get(item_key, 0.0)`. `cwh_shortage = cwh_physical_stock < required_quantity` -- a strict less-than, so a CWH stock EXACTLY equal to the required quantity is treated as sufficient (per the task's own rule: "If CWH Physical Stock >= Required Replenishment Quantity -> no changes"). Computed fresh on every read inside `get_replenishment_required()`, never persisted -- consistent with every other display-only computation this page already does (Milestone 45's `deficit_display`/`previous_month_sales`).

### Validation completed

(Isolated scratch DB, `app.config.DATABASE_PATH` override; scripts deleted after use, not committed)

- ✓ **Per-SKU independence**: three distinct items across three different CFAs (RAIPUR, BHOPAL, INDORE), each requiring the identical replenishment quantity (150), given three different CWH situations -- abundant CWH stock (200), short CWH stock (80), and no CWH record at all. Confirmed each row's `cwh_shortage` reflects ONLY its own item's CWH number (`False`, `True`, `True` respectively) -- the abundant item's large CWH stock does not bleed into or mask the other two items' shortages, confirming the comparison is genuinely per-`item_key`, not a pooled or shared warehouse number.
- ✓ **Boundary rule**: CWH stock >= required quantity -> `cwh_shortage` is `False`, no other field affected (`stock_deficit`/`deficit_display` byte-for-byte identical to Milestone 45 behavior).
- ✓ **Shortage rule**: CWH stock < required quantity -> `cwh_shortage` is `True`.
- ✓ **Missing CWH record**: an item_key never uploaded at Ahmedabad CWH at all correctly resolves to `cwh_physical_stock == 0.0` and `cwh_shortage == True` (a real shortage, not skipped or errored).
- ✓ **Cross-CFA reuse of the same CWH number**: a second CFA (NAGPUR) requiring the SAME item already flagged short at another CFA (BHOPAL) correctly sees the identical `cwh_physical_stock` (80) and the identical `cwh_shortage` (`True`) -- confirms the CWH-side number is a single shared per-item snapshot correctly reused across every CFA requesting that item, exactly as `get_cwh_stock_lookup()` is designed.
- ✓ **Existing replenishment calculations unchanged**: re-verified `stock_deficit`/`deficit_display` values are identical to what Milestone 45 produced for the same inputs; re-ran Milestone 45's own regression script unchanged (pack-rounding, Previous Month Sales, removed Threshold/Status columns, Raw/Packs toggle) -- all checks still pass, confirming this milestone only ADDS fields and never alters existing ones.
- ✓ **Existing threshold calculations unchanged**: `generate_thresholds_from_sales()`/`_packed_threshold()` untouched; `round_up_to_pack()` is a new public wrapper around the identical rounding rule, not a new rule -- `format_deficit_display()`'s refactor to use it was verified to produce identical output to before (same "175/packing 30 -> 180" check still passes).
- ✓ **Adequately-stocked products remain unchanged**: healthy (non-replenishment-required) items continue to never appear in `get_replenishment_required()` at all, regardless of their CWH stock situation -- unaffected by this change.
- ✓ **Real-data spot check** (read-only) against `2.0 dev`'s live database: of 365 real replenishment-required rows, 142 correctly flagged `cwh_shortage=True` (CWH stock at or near 0, or well below the required quantity) and 223 correctly flagged `False` (CWH stock in the thousands, far exceeding the requirement) -- spot-checked sample rows on both sides by hand, all consistent with the rule.
- ✓ Full Inventory regression suite (Milestone 40/42 Phase 1 CWH exclusion, Phase 3 real-stock/configurable-multiplier, Milestone 45 display changes) and the Inventory module UI structural test re-run -- all passed, confirming no regressions elsewhere.

### Assumptions made

- "Very bright red" was interpreted as a new, more saturated color (`#FF0000`, pure red) distinct from the existing, more muted `Color.ERROR` (`#D64545`) already used elsewhere for softer error states -- since the task specifically asked for something visually louder than the app's existing error styling.
- The boundary case (CWH stock exactly equal to the required quantity) was treated as "sufficient, no highlight" (strict `<`, not `<=`), matching the task's own literal wording ("If CWH Physical Stock >= Required Replenishment Quantity -> no changes, behaves normally").
- An item with no Ahmedabad CWH record at all is treated as 0 physical stock (i.e., always a shortage if replenishment is required at all), rather than being silently excluded from the check -- since "no stock has ever been recorded there" is a stronger, not weaker, availability signal than "some stock recorded, but not enough."

### Revision (same day, 2026-07-28) -- muted full-row highlight instead of a bright per-cell overlay

Immediate user feedback on the first pass: the bright `#FF0000` cell overlay "looks cartoonish," and the highlight should cover the whole row rather than just the Deficit cell. Implemented as a straight simplification:

- `ui/theme.py` -- `Color.CRITICAL_RED`/`CRITICAL_RED_TEXT` replaced with `Color.CRITICAL_ROW_BG` (`#FFC7CE`) / `Color.CRITICAL_ROW_TEXT` (`#9C0006`) -- a muted, Excel "Bad"-cell-style light red fill with dark red text, instead of a saturated pure red.
- `ui/inventory_replenishment_page.py` -- the entire overlay-label mechanism (`_wire_shortage_overlay`, `_position_shortage_overlays`, the `tk.Label`/bbox/scrollbar-wrapping machinery, and the `tkinter`/`ttk` imports it needed) was removed entirely. Replaced with a second native `ttk.Treeview` tag: `tree.tag_configure("cwh_shortage", background=Color.CRITICAL_ROW_BG, foreground=Color.CRITICAL_ROW_TEXT)`. Each row now gets exactly ONE tag -- `"cwh_shortage"` if `row["cwh_shortage"]` is True, otherwise the existing `"requires_replenishment"` (soft yellow) -- avoiding any ambiguity about which of two tags "wins" on a given row, since a row never carries both.
- Net effect: simpler code (no overlay positioning/scroll-tracking needed at all, since `ttk.Treeview` already colors whole rows natively) and a less jarring, easier-to-read highlight.
- Re-verified: all Milestone 46 data-layer regression checks still pass unchanged (this was a pure UI/presentation change, `get_replenishment_required()` and its `cwh_shortage`/`cwh_physical_stock` fields were not touched). Re-checked against the real database: all 142 real shortage rows correctly receive the `cwh_shortage` tag, the remaining 223 correctly keep `requires_replenishment`.

## Milestone 47 — Root Cause Fix: CFA Threshold Multiplier Ignored for Non-0.1-Aligned Values (2026-07-28)

Investigated a reported critical bug: "regardless of the value configured in Settings, the CFA Threshold calculation always behaves as if the multiplier is 2." `2.0 dev` only -- not merged to Main, not published. Explicit instruction: do not modify the Ahmedabad CWH Threshold logic at all -- confirmed untouched (see Validation below).

### Root cause

**Not in the calculation pipeline at all -- in the Settings UI's input widget.** `ui/inventory_settings_page.py`'s CFA "Inventory Threshold Multiplier" control was a `ctk.CTkSlider` quantized to fixed steps:

```python
MULTIPLIER_MIN = 1.0
MULTIPLIER_MAX = 3.0
MULTIPLIER_STEP = 0.1
steps = round((MULTIPLIER_MAX - MULTIPLIER_MIN) / MULTIPLIER_STEP)  # 20
self.slider = ctk.CTkSlider(..., number_of_steps=steps, ...)
```

A `CTkSlider` with `number_of_steps=20` over range 1.0-3.0 can only ever land on one of 21 fixed positions: 1.0, 1.1, 1.2, ..., 2.9, 3.0. It is **physically incapable** of representing 1.25, 2.25, or 2.75 -- there is no slider position for them. `_on_multiplier_save_clicked()` then saved whatever `self.slider.get()` returned (already snapped to the nearest 0.1 by the widget itself), so any value the user actually wanted outside that 21-value grid was silently replaced before `set_threshold_multiplier()` was ever called -- the "overwrite" happened in the widget, not in `app/threshold_service.py` or `app/inventory_parameters_service.py`.

Confirmed by direct reproduction (isolated scratch DB, `ui.inventory_settings_page.InventorySettingsPage` instantiated headlessly): setting the DB value directly to `1.25` and rebuilding the page, the slider-driven UI could only display/re-save `1.2`; `2.75` could only round-trip as `2.8`. `1.5` and `2` (both exact 0.1-grid multiples) happened to round-trip correctly, which is why the bug wasn't "literally always 2" in every case, but any value off that grid was silently corrupted -- and 2.0 sitting at the exact midpoint of the 1.0-3.0 slider track made it the easiest position to land on/near by accident, consistent with the bug being perceived as "always ×2."

The actual calculation code was, and remains, completely correct:
- `app.inventory_parameters_service.get_threshold_multiplier()`/`set_threshold_multiplier()` -- round-trip any float exactly (verified directly: set `1.25` → get `1.25`, no rounding at this layer).
- `app.threshold_service.generate_thresholds_from_sales()` -- reads `multiplier = get_threshold_multiplier()` once per run and computes `raw_threshold = previous_month_sales * multiplier` correctly for any value.
- **Confirmed against the real, live `2.0 dev` database**: the stored `threshold_multiplier` parameter is `1.5`, and every single real `InventoryThreshold` row's `raw_threshold / previous_month_sales` ratio is exactly `1.5` -- not `2` -- proving the backend has never miscalculated using the real, already-configured value. The bug only manifests for a *newly attempted* value that the slider itself cannot represent.

The CWH Threshold Multiplier card, by contrast, was already a free-form `ctk.CTkEntry` (see its own pre-existing code comment: *"the task's own examples (1.5, 2, 2.25, 3) include a value a 0.1-step slider can't land on exactly, so free-form decimal entry is used instead"*) -- it was built correctly from the start and was never affected. This asymmetry -- CWH using a precise text entry, CFA using a coarse slider -- is exactly why "Ahmedabad CWH correctly respects the configured multiplier while the CFA Threshold does not."

### Files modified

- `ui/inventory_settings_page.py` -- the CFA Threshold Multiplier's `ctk.CTkSlider` (plus its paired value label, `_on_slider_moved`, and the `MULTIPLIER_MIN`/`MULTIPLIER_MAX`/`MULTIPLIER_STEP` constants) replaced with a `ctk.CTkEntry` free-form numeric field, structurally identical to the CWH card's own existing entry: `_load_multiplier()` now populates the entry via `_format_multiplier()` (already-shared helper); `_on_multiplier_save_clicked()` now parses the entry's text, validates it's a positive number (same validation the CWH card already used), and calls `set_threshold_multiplier()` with the exact value -- no rounding, no quantization, no fixed range.

**Files NOT modified (per explicit instruction):** `app/cwh_service.py` (untouched -- CWH's `evaluate_cwh_stock()`, `_cwh_status()`, `get_cwh_overview()`, `get_cwh_summary()` all exactly as before); `app/inventory_parameters_service.py`'s `get_cwh_threshold_multiplier()`/`set_cwh_threshold_multiplier()` (untouched); the CWH Threshold Multiplier card in `ui/inventory_settings_page.py` (`_build_cwh_multiplier_card`/`_load_cwh_multiplier`/`_on_cwh_multiplier_save_clicked`, byte-for-byte identical to before); `app/threshold_service.py` (the calculation logic itself needed no change -- it was already correct); `database/models.py` (no schema change).

### Why the bug occurred

The CFA multiplier control was originally built as a slider for a nicer drag-to-adjust UX, without anticipating that the business would need to configure precise, non-0.1-aligned decimal values (1.25, 2.25, 2.75). When the CWH Threshold Multiplier was added later (Phase 3), its author correctly recognized a slider couldn't represent such values and used a free-text entry instead -- but the CFA card was never revisited to apply the same fix, leaving the original, narrower control in place.

### Why this fix permanently resolves it

A free-form `CTkEntry` has no quantization step and no fixed numeric ceiling -- it accepts and stores any positive decimal exactly as typed, with the exact same validation/parsing code already proven correct on the CWH card (which has never exhibited this bug). There is no remaining code path in the CFA multiplier pipeline that rounds, clamps, or re-derives the value from anything other than what the user typed; `set_threshold_multiplier()` is the single source of truth on save, `get_threshold_multiplier()` the single source of truth on read, exactly mirroring the CWH multiplier's already-correct pattern.

### Validation performed

(Isolated scratch DB, `app.config.DATABASE_PATH` override; scripts deleted after use, not committed)

- ✓ **UI round-trip precision**: for each of `1.25`, `1.5`, `2`, `2.75` -- set directly in the DB, rebuilt `InventorySettingsPage` headlessly, confirmed `multiplier_entry.get()` displays the value with exact precision (previously the slider showed `1.2`/`2.8` for the first/last of these).
- ✓ **End-to-end via the real UI Save button** (not the backend setter directly) for all four values: typed each into `multiplier_entry`, invoked `_on_multiplier_save_clicked()`, then ran a full Sales Report → Inventory Report reprocessing cycle (`generate_thresholds_from_sales()` + `evaluate_replenishment()` + `evaluate_cwh_stock()`). Confirmed CFA `raw_threshold` exactly equals `previous_month_sales × <the typed value>` for all four (125, 150, 200, 275 for a sales figure of 100) -- zero precision loss end to end.
- ✓ **Replenishment updates correctly after reprocessing**: for each multiplier, the resulting `stock_deficit` on the reprocessed replenishment row correctly reflects the new `packed_threshold` for that run.
- ✓ **Ahmedabad CWH Threshold remains unchanged and correct**: CWH's own stored multiplier (`2.0`, untouched throughout) and its resulting `cwh_threshold` were re-checked after every single CFA-multiplier change above and never varied -- confirming the CFA fix has zero effect on CWH's own independent calculation, satisfying the "must remain completely unchanged" requirement.
- ✓ **No existing Inventory functionality broke**: re-ran the Milestone 46 (CWH-availability shortage highlight) and Milestone 45 (Replenishment display) regression suites in full -- all checks still pass unchanged.
- ✓ **Real-data confirmation** (read-only, before the fix): the live `2.0 dev` database's stored `threshold_multiplier` (`1.5`) and every real `InventoryThreshold` row's `raw_threshold / previous_month_sales` ratio (`1.5` for every sampled row) were inspected directly, proving the backend calculation was never actually stuck at `×2` for the already-configured, already-processed real data -- the bug was specifically an inability to SET a new non-0.1-aligned value going forward, not a corruption of past results.

### Assumptions made

- No upper bound was re-imposed on the CFA multiplier entry (the old slider capped it at `3.0`) -- validation now only requires a positive number, mirroring the CWH multiplier field's own validation exactly (which has never had an upper bound). If a specific maximum is required, it should be added identically to both fields' validation, not just one.

## Milestone 48 — Follow-Up Trace: CFA Threshold Multiplier "Still Not Updating" (2026-07-28)

After Milestone 47's fix, the user reported the CFA Threshold Multiplier still didn't appear to be taking effect, and asked for a full end-to-end trace (Settings -> read multiplier -> calculation -> storage -> Threshold page's dataset -> rendering -> displayed values) rather than another guess, explicitly floating the possibility that the calculation is correct but the Threshold page is showing stale/cached data. `2.0 dev` only -- no merge, no publish. Ahmedabad CWH logic untouched throughout.

### Method -- verified every stage against real data instead of guessing

1. **Settings -> read multiplier**: re-confirmed `app.inventory_parameters_service.get_threshold_multiplier()`/`set_threshold_multiplier()` round-trip exactly (already verified in Milestone 47).
2. **Calculation**: re-confirmed `generate_thresholds_from_sales()` computes `raw_threshold = previous_month_sales * multiplier` correctly for arbitrary values.
3. **Storage / cloud cache**: queried the REAL, live Supabase cloud tables directly (read-only) to test the "is a cloud sync silently pulling stale data back down" hypothesis. Result: `SELECT * FROM inventory_thresholds` returns **`permission denied for table inventory_thresholds` (Postgres error 42501)** -- the anon API key has no grant on this table at all. This means `app.inventory_sync_service.sync_thresholds()`'s pull step (`reconcile_rows()` -> `pull_rows()`) fails immediately and returns `success=False` on every single call, for both `2.0 dev` and the installed Main build alike -- `_sync_table()` bails out before ever touching local SQLite. **The cloud sync layer cannot silently overwrite local threshold data with stale values; it is a hard no-op due to a pre-existing Supabase grant misconfiguration**, unrelated to this task and out of scope to fix here (would need `GRANT SELECT, INSERT, UPDATE ON public.inventory_thresholds TO anon;`, same family of issue already seen on `module_configurations`).
4. **Dataset / model used by the Threshold page**: read `ui/inventory_thresholds_page.py` -- `on_show()` calls `get_all_thresholds()` fresh from the local DB on every visit; `_render_table()` does no caching of its own. No stale-dataset bug found in this page.
5. **Displayed values, against real production data**: directly inspected the LIVE `2.0 dev` local database (`database/saffron_validator.db`) with a raw SQL query, sorted by `last_updated`. Found:
   - 1000 `InventoryThreshold` rows at `raw_threshold / previous_month_sales == 1.5` (the multiplier active for their last upload).
   - 152 rows -- all for CFA `GHAZIABAD`, `last_updated` a few minutes newer than the rest -- at ratio `== 2.0` (the current Settings value, `2`, at the time of that upload).
   
   Also inspected the installed **Main** app's own, completely separate local database (`%LOCALAPPDATA%\Saffron Validator\database\saffron_validator.db` -- a different file, different install, per `app/config.py`'s frozen-build path resolution) as a control: its stored multiplier is also `1.5`, and every one of its 1152 rows has ratio exactly `1.5` -- fully internally consistent with its own setting, no `×2` anywhere.

### Root cause: no bug in Calculation, Storage, Refresh, UI, or Cache -- the observation matches `generate_thresholds_from_sales()`'s documented upsert-only behavior exactly

`generate_thresholds_from_sales()` only creates/updates `InventoryThreshold` rows for the `(CFA, Item Name)` combinations **actually present in the uploaded Sales Report** -- this is deliberate, existing, documented behavior (see the module's own docstring and `app/replenishment_service.py`'s identical upsert convention: "does NOT silently zero a value the current upload simply didn't report"). A CFA/item combination absent from a given upload is left completely untouched, still carrying whichever multiplier was in effect the last time *that specific combination* was uploaded.

Reproduced exactly, on demand, in an isolated scratch DB: uploaded a 2-CFA Sales Report at multiplier `1.5` (both CFAs land at ratio `1.5`), then changed the multiplier to `2` and re-uploaded a **partial** Sales Report containing only one of the two CFAs. Result: the re-uploaded CFA correctly shows ratio `2.0`; the CFA absent from that second upload stays at `1.5`, completely unchanged -- byte-for-byte the same mixed pattern found in the real database (1000 rows at `1.5`, 152 at `2.0`, split exactly along which CFA was in the most recent upload).

This means: the most recent live test used a **Sales Report scoped to only one CFA (GHAZIABAD)** while the multiplier was set to `2`. That CFA's thresholds correctly updated to `2`. Every other CFA's thresholds -- the vast majority of the table (1000 of 1152 rows) -- were never touched by that upload and still show `1.5` from an earlier, full company-wide run. Scrolling the Thresholds table and seeing mostly-unchanged values (or filtering to a CFA that wasn't in the latest test upload) reads exactly like "the multiplier isn't updating," when every row shown is individually correct for its own most recent upload.

### Component actually responsible

**Other** -- specifically, upload scope / testing methodology, not a defect in Calculation, Storage, Refresh, UI, or Cache. Every one of those five components was independently verified correct against real data:
- Calculation: correct for every value tested (Milestone 47 + this milestone).
- Storage: `InventoryThreshold` rows persist exactly what `generate_thresholds_from_sales()` computed; no corruption found.
- Refresh/Cache: the module-wide cloud Refresh (`app/inventory_refresh.py`) cannot have caused this either -- Inventory has no automatic background poller (manual button only, by prior deliberate design), and the one cloud sync path that touches threshold data (`sync_thresholds()`) is a proven hard no-op due to the `inventory_thresholds` permission error above.
- UI: `InventoryThresholdsPage.on_show()` reloads fresh from the DB on every visit, verified by direct code read; no caching layer exists to go stale.

### Why the previous fix (Milestone 47) did not appear to resolve the issue

It did resolve the actual bug it targeted (the CFA multiplier slider's inability to represent 1.25/2.25/2.75 exactly) -- confirmed again in this milestone: the 152 `GHAZIABAD` rows generated after the fix show an exact `2.0` ratio with no precision loss, proving the fixed Settings entry -> `generate_thresholds_from_sales()` pipeline works correctly end to end in the real running app, not just in isolated tests. What it could not fix -- because it isn't a bug -- is that reprocessing with a **partial** (single-CFA) Sales Report only ever updates that CFA's rows, by design. The next full company-wide re-upload will bring every remaining row up to whatever multiplier is active at that time.

### Files modified (diagnostics only -- no behavior change)

- `app/threshold_service.py` -- `generate_thresholds_from_sales()`'s existing completion log line now also states the multiplier used for that run and explicitly notes that only the combinations present in that upload were touched (previously logged rows-processed/created/updated counts without naming the multiplier or this scoping caveat). Purely additive to the log message string; no logic changed.
- `ui/inventory_thresholds_page.py` -- `on_show()` now emits one `logger.debug()` line listing every distinct `raw_threshold/previous_month_sales` ratio currently present across all loaded rows, with a note that more than one distinct value means some rows are simply awaiting their next reprocessing -- so this exact situation is immediately visible in the log on any future page visit, without needing another manual SQL trace. Also purely additive.

**Files NOT modified:** `app/cwh_service.py` and every CWH-related function (untouched, per explicit instruction); `app/threshold_service.py`'s actual calculation logic (unchanged, already correct); `app/inventory_sync_service.py`/`app/sync_service.py` (the permission error is a Supabase-side grant issue, not application code, and is out of scope for this task).

### Validation performed

- ✓ Reproduced the exact real-database mixed-ratio pattern (some rows at the old multiplier, some at the new one) on demand, in an isolated scratch DB, via a partial re-upload -- confirms the mechanism precisely.
- ✓ Confirmed, by direct read-only query, that the cloud sync path for `inventory_thresholds` is a hard no-op (`permission denied`) -- ruling out cloud-pull data corruption as a contributing factor.
- ✓ Confirmed the installed Main app's own, entirely separate local database is also internally consistent with its own multiplier setting (`1.5` everywhere) -- ruling out any cross-instance data contamination between Main and `2.0 dev` (they don't even share a writable database file, only a Supabase project that the permission error blocks from mattering here).
- ✓ Re-ran the Milestone 45 display-changes regression suite after the logging additions -- all checks still pass, confirming the new log lines changed no behavior.
- ✓ Relaunched `2.0 dev` with the new diagnostic logging in place, ready for the next live re-test.

### Recommendation for the next test

Re-upload the SAME full, company-wide Previous Month Sales Report (not a partial/single-CFA test file) immediately after each multiplier change, then check the Thresholds page -- every row should update to the new multiplier in that single pass. If a partial upload is intentionally used for a quick spot-check, only the CFAs/items actually included in that file will reflect the new value; this is expected, not a defect.

## Milestone 49 — CFA Threshold Recalculation Architecture Matched to Ahmedabad CWH's "Gold Standard" (2026-07-28)

Milestone 48 explained the "stuck" appearance (a partial re-upload only touches its own CFAs, by design) but the user, correctly, didn't accept "that's expected" as a resolution -- they asked for a structural comparison against the Ahmedabad CWH module (confirmed working exactly as expected) and for the CFA module's architecture to be brought in line with it wherever CWH's pattern is better. This milestone is that comparison and the resulting fix. `2.0 dev` only -- no merge, no publish. Ahmedabad CWH's own code (`app/cwh_service.py`) is completely unmodified throughout.

### Full architectural comparison (the 12 points requested)

| Stage | Ahmedabad CWH (`app/cwh_service.py`) | CFA Threshold (`app/threshold_service.py`, BEFORE this milestone) | Match? |
|---|---|---|---|
| 1. Settings value retrieval | `get_cwh_threshold_multiplier()` -- fresh DB read, once per run | `get_threshold_multiplier()` -- fresh DB read, once per run | Identical |
| 2. Parameter propagation | `multiplier` read once at top of `evaluate_cwh_stock()`, used for every item in that run | `multiplier` read once at top of `generate_thresholds_from_sales()`, used for every item in that run | Identical |
| 3. Threshold calculation | `cwh_threshold = total_previous_month_sales * multiplier` | `raw_threshold = previous_month_sales * multiplier` | Identical formula shape |
| 4. Data model updates | Upserts `CwhStock`, one row per `item_key` | Upserts `InventoryThreshold`, one row per `(branch_key, item_key)` | Identical mechanism |
| 5. State management | No page-level or module-level caching of computed values anywhere | No page-level or module-level caching of computed values anywhere | Identical |
| 6. Dataset refresh | `InventoryCwhPage.on_show()` calls `get_cwh_overview()` fresh from the DB on every visit | `InventoryThresholdsPage.on_show()` calls `get_all_thresholds()` fresh from the DB on every visit | Identical |
| 7. Cache invalidation | N/A -- nothing is ever cached in memory to invalidate | N/A -- nothing is ever cached in memory to invalidate | Identical |
| 8. UI binding | `styled_treeview()` + `tree.insert()` per row, rebuilt on every `_render_table()` | `styled_treeview()` + `tree.insert()` per row, rebuilt on every `_render_table()` | Identical |
| 9. Rendering | Same `ui/components.py` helper, same page-lifecycle pattern | Same `ui/components.py` helper, same page-lifecycle pattern | Identical |
| 10. **Reprocessing workflow (recalculation SCOPE)** | `evaluate_cwh_stock()` iterates `all_item_keys = set(stock_by_item) \| set(sales_by_item) \| set(existing_by_item)` -- **every item currently known, from any prior run, is recalculated every time**, regardless of whether it appears in the current upload | `generate_thresholds_from_sales()` iterated only `grouped` -- **only the (CFA, Item) combinations physically present in the current Sales Report DataFrame** were recalculated; every other existing row was left completely untouched | **THE DIVERGENCE** |
| 11. Helper methods | `_cwh_status()` (CWH-specific status derivation, not applicable to Thresholds) | `_packed_threshold()`/`round_up_to_pack()` (pack-rounding, CWH has no packing dimension) -- each side has its own domain-specific helpers, no shared logic expected here | Not applicable / no divergence |
| 12. Event listeners / refresh triggers | Triggered by the Inventory Report upload (`ui/inventory_upload_page.py`), manual button only, no background poller | Triggered by the Sales Report upload (`ui/sales_upload_page.py`), manual button only, no background poller | Identical |

**The one real architectural difference was #10** -- CWH's recalculation scope is "every known item, always," while CFA Threshold's was "only what's in this specific upload." Every other stage was already structurally identical (confirmed by direct code reading, not assumption) -- this is why Milestones 47/48's calculation-layer and UI-layer fixes/checks kept coming back clean: they were looking at stages that were never actually different.

### The fix

`app/threshold_service.py`'s `generate_thresholds_from_sales()` now follows CWH's exact pattern: builds `fresh_by_key` (this upload's own data, the analog of CWH's `stock_by_item`), reads `existing_by_key` (every `InventoryThreshold` row currently in the table), and iterates `all_keys = set(fresh_by_key) | set(existing_by_key)` -- literally the same set-union pattern `evaluate_cwh_stock()` already uses. For a key present in this upload, its `branch_location`/`item_name`/`division`/`packing`/`previous_month_sales` are refreshed from the upload (a genuine new data point, same as before). For a key absent from this upload, its descriptive/sales data is left untouched (still upsert-only for that part, matching CWH's own treatment of `closing_stock` when an item isn't in the current CWH rows) -- but its `raw_threshold`/`packed_threshold` are recalculated regardless, using the CURRENT multiplier, exactly mirroring CWH always recomputing `cwh_threshold` for every known item on every run.

Net effect: changing the CFA Threshold Multiplier and reprocessing **any** Sales Report -- even a single-CFA test file -- now immediately refreshes every existing threshold row to the new multiplier, not just the rows the uploaded file happens to mention. This is now indistinguishable, in behavior, from how CWH already worked.

### Files modified

- `app/threshold_service.py` -- `generate_thresholds_from_sales()` rewritten to the full-refresh, CWH-parity pattern described above. Return dict gains one new key, `recalculated_only` (count of existing rows refreshed but not present in this upload) -- `unique_combinations`/`created`/`updated`/`excluded_cwh_rows` keep their exact prior meaning, so `ui/sales_upload_page.py`'s existing success message (which only reads `unique_combinations`) needed no change. Log line updated to report the multiplier used and both counts. Module docstring updated to describe the new architecture and cite `evaluate_cwh_stock()` as its reference.

**Files NOT modified:** `app/cwh_service.py` (byte-for-byte unchanged, per explicit instruction); `app/replenishment_service.py` (needed no change -- `get_replenishment_required()` already reads `InventoryThreshold` fresh via `get_thresholds_lookup()` at display time, so it automatically benefits from thresholds now being fully refreshed; `evaluate_replenishment()`'s own snapshot-per-run behavior for `stock_deficit`/`status` already exactly mirrors `CwhStock`'s own "computed once per processing run" convention -- no divergence there to fix); `ui/inventory_thresholds_page.py`/`ui/inventory_replenishment_page.py` (no UI change needed -- stage 6-9 were already identical to CWH); `database/models.py` (no schema change).

### Why this permanently resolves the issue

The prior "only touch what's in this upload" scope meant a partial or scoped re-upload could ever leave the majority of the Thresholds table showing an old multiplier indefinitely -- there was no way to "catch up" the rest of the table short of a full company-wide re-upload. With the fix, EVERY reprocessing run -- regardless of how many CFAs or items it actually contains -- sweeps every existing threshold row through the current multiplier. There is no longer any code path where an existing `InventoryThreshold` row's `raw_threshold`/`packed_threshold` can go stale after a reprocessing run happens; the only way to see an old value is to not reprocess at all (which both this and the CWH page already communicate explicitly in their Settings warning text).

### Validation performed

(Isolated scratch DB, `app.config.DATABASE_PATH` override, plus a live real-database test; scripts deleted after use, not committed)

- ✓ **The exact original bug reproduced AND fixed**: 3-CFA full upload at multiplier `1.5` (all three land at ratio 1.5), then multiplier changed to `2` and a **partial** (single-CFA) re-upload performed. Before this fix this would have left 2 of the 3 CFAs at the old `1.5`; with the fix, all 3 CFAs -- including the 2 NOT present in that partial upload -- correctly show ratio `2.0`.
- ✓ **All four required multiplier values via full re-uploads** (`1.25`, `1.5`, `2`, `2.75`): every CFA's ratio exactly matches the configured multiplier each time.
- ✓ **All four required multiplier values via PARTIAL (single-CFA) re-uploads** -- the exact scenario that used to fail: every CFA, including the two never mentioned in that partial file, correctly updates to each new multiplier every time.
- ✓ **Replenishment updates correctly after reprocessing**: re-ran a full Sales Report + Inventory Report cycle at multiplier `2.75`; the resulting `stock_deficit` on the replenishment row correctly reflects the new threshold (`275 - 5 = 270`).
- ✓ **Ahmedabad CWH remains completely unaffected**: CWH's own stored multiplier (`2.0`) and its own computed `cwh_threshold` for the same shared item were rechecked after every single CFA-multiplier change above and never varied.
- ✓ **Live fix confirmed against the REAL production database** (not just an isolated scratch DB): before this milestone's fix, the real `2.0 dev` database held 1000 `InventoryThreshold` rows at ratio `1.5` and 152 at ratio `2.0` (the exact split Milestone 48 diagnosed, left over from the earlier partial GHAZIABAD test). Ran the new `generate_thresholds_from_sales()` once against the real database with a single harmless synthetic test row (a fake CFA/item guaranteed not to collide with any real data, used only to trigger a run) -- all 1152 real rows, plus the synthetic one, came back reporting a single uniform ratio of `2.0` (the currently-configured multiplier), fully healing the pre-existing inconsistency. The synthetic test row was deleted immediately afterward; no real data was altered beyond the intended threshold recalculation.
- ✓ Re-ran the Milestone 45 (Replenishment display) and Milestone 46 (CWH-availability shortage) regression suites in full -- all checks still pass, confirming no regressions.

### Assumptions made

- Matching CWH's "recalculate everyone, every run" scope means every reprocessing run's `last_updated` timestamp advances for every existing threshold row, even ones whose `previous_month_sales` didn't actually change in that run -- exactly mirroring how `evaluate_cwh_stock()` already bumps `CwhStock.last_updated` for every known item on every run, not just ones with fresh stock data. This is treated as the correct, intended parity rather than a side effect to avoid.
- A (branch_key, item_key) combination that has never appeared in ANY upload has no existing row to recalculate and is correctly never invented out of thin air -- `all_keys` is still built from real data on at least one side (this upload or a prior one), exactly like CWH's own `all_item_keys` union.

## Milestone 50 — Packing Parser Fix: Last Number, Not Multiplied Together (2026-07-28)

Separate, explicitly reported bug: the Packing field's "N*M" notation (e.g. `10*10`, `1*10`, `5×12`, `20x15`) was being interpreted as a multiplication, producing a wildly inflated pack size (`10*10` -> `100`) instead of the correct saleable-unit count (`10`). `2.0 dev` only -- no merge, no publish.

### Root cause / prior behavior

`app/threshold_service.py`'s `_parse_packing()` split on `*` and multiplied every part together:

```python
if "*" in text:
    parts = [float(p.strip()) for p in text.split("*")]
    result = 1.0
    for part in parts:
        result *= part
    return result
```

This was written when the notation's meaning was assumed to be "N boxes × M units per box = total units," but the correct interpretation (per this task) is that the **first** number is only the strip/box/carton count and the **second (last)** number is the actual saleable-unit pack size that rounding must use -- the two numbers should never be multiplied together.

### The fix

`_parse_packing()` now splits on any of `*`, `×`, `x`, or `X` (case-insensitive, via `_PACKING_SEPARATOR_PATTERN = re.compile(r"[*×xX]")`) and takes **only the last** numeric part, instead of multiplying all parts together. A cell with no separator at all (e.g. `72`) is still parsed as a single plain number, exactly as before -- unaffected by this change.

```
"10*10" -> 10   (was 100)
"1*10"  -> 10   (was 10 -- unaffected, since 1×10 == 10 either way)
"5×12"  -> 12   (was 60)
"20x15" -> 15   (was 300)
"72"    -> 72   (unaffected -- no separator)
```

### Files modified

- `app/threshold_service.py` -- `_parse_packing()` rewritten to the last-number rule; docstring updated to explain the correct interpretation and cite this milestone. `generate_thresholds_from_sales()`'s own docstring updated to reflect that `_parse_packing` extracts the last number, not a product.

**Files NOT modified:** `app/cwh_service.py`, `app/replenishment_service.py` (neither has its own Packing-parsing logic -- both read the already-parsed `packing` value from `InventoryThreshold`, set once by `_parse_packing` at threshold-generation time); `database/models.py` (no schema change).

### Validation performed

- ✓ Unit-tested `_parse_packing()` directly against every example in the task (`10*10`->10, `1*10`->10, `5×12`->12, `20x15`->15, `20X15`->15, `72`->72, `30`->30, `60`->60), plus edge cases (extra whitespace around separators, more than one separator e.g. `2*5*10`->10, unparseable text, empty/`None` input) -- all correct.
- ✓ **End-to-end**: uploaded a Sales Report with items using `10*10`, `1*10`, `5x12`, and `72` notation. Confirmed each `InventoryThreshold.packing` value is now correct (10, 10, 12, 72) and each `packed_threshold` correctly rounds UP to a multiple of the CORRECTED packing size (e.g. a raw threshold of 142.5 rounds to 150 for packing=10, to 144 for packing=12) -- not the old, inflated packing.
- ✓ **Replenishment**: confirmed `deficit_display` on the Replenishment page rounds using the corrected packing size too (reads the same stored, now-correct `packing` value via `app.threshold_service.round_up_to_pack`).
- ✓ **Ahmedabad CWH unaffected**: CWH has no packing dimension at all (a single warehouse's stock vs. total demand, no pack-rounding) -- re-verified its threshold computation is untouched and uses only its own multiplier and summed sales.
- ✓ Re-ran the Milestone 45, 46, and 49 regression suites in full -- all checks still pass.
- ✓ **Real-data impact check** (read-only, before any reprocessing): the live `2.0 dev` database currently holds 1152 threshold rows with three distinct stored packing values under the OLD (buggy) parser: `10` (148 rows), `72` (51 rows), and **`100` (953 rows -- the large majority)**. Spot-checked several of the `100`-packing rows (e.g. `BENULIV`, `BENUVO`, `BILZIT 40`) -- consistent with real Packing cells that were `10*10`-style and got multiplied to 100 instead of correctly resolving to 10.

### Important: this does not retroactively correct already-stored data

Only the ORIGINAL Packing cell text (e.g. `"10*10"`) determines the correct pack size, and that original text is never stored -- only the final parsed number (`InventoryThreshold.packing`) is. There is no way to safely re-derive the correct value for an already-stored row without re-reading the actual source Sales Report file again. Consistent with this codebase's existing "changes take effect on next processing run, never retroactively" convention (already established for both threshold multipliers), the corrected parser takes effect the next time each CFA/item combination's row is reprocessed -- and per Milestone 49's full-refresh architecture, ANY Sales Report reprocessing (even a partial one) will correctly re-derive `packing` for every CFA/item combination that upload actually mentions, while combinations NOT in that upload keep their existing (possibly still-wrong, pre-fix) packing value until they are next included in an upload. **A full company-wide Sales Report re-upload is recommended after this fix to correct all 953 currently-affected rows in one pass.**

### Assumptions made

- `×` (Unicode multiplication sign) and both cases of `x`/`X` are all treated as equivalent separators to `*`, per the task's own explicit examples (`10×10`, `20x15`). No other separator (e.g. a hyphen or comma) is assumed without being asked for.
- A cell with more than two numbers separated by the recognized characters (e.g. a hypothetical `2*5*10`) takes the LAST number only, consistent with "use ONLY the last numeric value" -- not specifically listed in the task's examples, but the most literal reading of the stated rule.

## Milestone 51 — Threshold vs. Replenishment Packing Divergence: Stale Snapshot, Not a Second Parser (2026-07-28)

Reported as a suspected architectural bug: the Threshold page still showed `Packing = 100.0` for `10×10`-style items after Milestone 50's parser fix, while the Replenishment page showed correctly pack-rounded values (`110 (11 Packs)`, `510 (51 Packs)`, `72 (1 Pack)`) for what looked like the same kind of item -- taken as evidence of a second, unfixed packing parser somewhere. Explicit instruction: do not modify the packing parser; find why the Threshold page isn't consuming the same value the Replenishment page uses. `2.0 dev` only -- no merge, no publish.

### Investigation -- traced with real, live data, not assumption

Confirmed via `grep` across the entire codebase: `_parse_packing()` in `app/threshold_service.py` is the **only** packing-parsing function that exists anywhere in the application. There is no second parser to find.

Traced both pages' full data path:
- **Thresholds page**: `InventoryThresholdsPage.on_show()` -> `app.threshold_service.get_all_thresholds()` -> reads `InventoryThreshold.packing` directly, live, on every visit. No caching, no re-derivation.
- **Replenishment page**: `InventoryReplenishmentPage.on_show()` -> `app.replenishment_service.get_replenishment_required()` -> reads `InventoryReplenishment.packing`, a column that is **NOT independently parsed at all** -- `evaluate_replenishment()` (line ~180) sets it via `packing = threshold_record["packing"]`, i.e. copied verbatim from the exact same `InventoryThreshold` row the Thresholds page reads, at the moment the Inventory Report was last uploaded. It is a snapshot, not a computation.

**Both pages ultimately trace back to the exact same single stored value, produced by the exact same single parser.** The question became: why would that snapshot ever disagree with the live value?

Queried the real, live `2.0 dev` database directly to check for concrete evidence of a genuine discrepancy (not just theorize):

```
InventoryThreshold rows: 1152 -- packing distinct values: {100.0: 953, 10.0: 148, 72.0: 51}
InventoryReplenishment rows: 1152 -- packing distinct values: {10.0: 1101, 72.0: 51}
153 items where InventoryReplenishment.packing does NOT match ANY InventoryThreshold.packing for that same item_key
  (e.g. "BILZIT 40": InventoryReplenishment.packing=10.0, InventoryThreshold.packing=100.0)
```

Checked `last_updated` timestamps for both tables to establish the actual timeline:

```
InventoryThreshold.last_updated:      2026-07-28 12:08:48 (1000 rows -- Milestone 49's validation sweep, packing untouched)
                                       2026-07-28 12:22:42 (152 rows  -- a real Sales Report reprocessing AFTER Milestone 50's fix)
InventoryReplenishment.last_updated:  2026-07-27 15:14:15 (152 rows)
                                       2026-07-28 02:33:08 (997 rows)
                                       2026-07-28 02:35:09 (3 rows)
```

**Every single InventoryReplenishment row's `last_updated` predates Milestone 50's fix by hours.** No Inventory Report has been re-uploaded since the packing parser was corrected. The "110 (11 Packs)"/"510 (51 Packs)"/"72 (1 Pack)" values cited as proof the fix works were therefore NOT evidence of the new parser running at all -- they are old, pre-fix snapshots that happened to already look correct (either the item's real notation was something like "1×10", where the OLD multiply-together bug and the NEW last-number rule coincidentally produce the identical result, or the item had no separator at all, e.g. a plain "72"). Meanwhile the Threshold page's `packing` column is always the LIVE value -- for the 1000 rows nobody has reprocessed with a Sales Report since Milestone 50, it is still showing the OLD, pre-fix parse, because no new Sales Report upload has told it otherwise. The two pages were not fighting over two different parsers; they were simply looking at two different points in time -- one live, one frozen.

### The actual, precise, in-scope defect found

Inspecting `get_replenishment_required()` itself surfaced one real inconsistency worth fixing, independent of the reprocessing-timing story above: within that SAME function, `previous_month_sales` is already looked up **live** from `get_thresholds_lookup()` on every call (explicitly documented: "always the same number that actually drove this row's threshold, never re-derived or estimated") -- but `packing`, used for the exact same row's deficit rounding, was read from `row.packing`, the STALE stored snapshot, right next to it. One field in the function was already following the "always read live" policy the rest of this codebase uses everywhere for display; the other, accidentally, was not.

### The fix

`app/replenishment_service.py`'s `get_replenishment_required()` now computes `packing = threshold_record["packing"] if threshold_record else row.packing` -- reading the SAME live `InventoryThreshold` record `previous_month_sales` already comes from, instead of the row's own frozen copy -- and uses this live `packing` for both `round_up_to_pack()` (the `cwh_shortage` comparison) and `format_deficit_display()`. Falls back to the row's own stored value only if no matching threshold record exists at all (mirroring `previous_month_sales`' identical existing fallback). The packing parser itself (`_parse_packing()`) was NOT touched, per explicit instruction -- this is purely a "which already-parsed value gets used for display" fix, not a parsing change.

`stock_deficit`/`status` (the actual replenishment DECISION -- whether this row needs replenishment at all) remain exactly as `evaluate_replenishment()` last computed them, unchanged -- consistent with the established, deliberate "changes take effect on next processing run, never retroactively" convention this codebase already uses for thresholds and CWH. Only the ROUNDING/DISPLAY of that already-decided deficit now uses the live packing value, exactly like `deficit_display` itself is already documented as "computed fresh on every read, never stored."

### Why the Threshold page was "bypassing" the normalized packing logic -- it wasn't

It never bypassed anything. The Threshold page was, and remains, the ONE place in the entire application always showing the live, current `InventoryThreshold.packing` value. The actual defect was on the OTHER side: the Replenishment page was quietly trusting a value it had copied and frozen at an earlier point in time, for one specific field, while every other display-time value on that same page was already correctly live. After this fix, both pages read the identical, single, live value -- there is now exactly one packing parser AND exactly one point of consumption for its output at display time, with no snapshot able to drift out of sync between them ever again, regardless of which report (Sales or Inventory) was most recently reprocessed.

### Files modified

- `app/replenishment_service.py` -- `get_replenishment_required()`: added the live `packing` lookup (mirroring `previous_month_sales`' existing pattern) and switched both `round_up_to_pack()`/`format_deficit_display()` calls to use it instead of `row.packing`. Docstring updated to explain the fix and why it was needed.

**Files NOT modified:** `app/threshold_service.py`/`_parse_packing()` (untouched, per explicit instruction -- Milestone 50's fix stands as-is); `evaluate_replenishment()` itself (still correctly stores its own `packing` snapshot on `InventoryReplenishment` for record-keeping/audit purposes -- only the DISPLAY read path in `get_replenishment_required()` changed); `ui/inventory_thresholds_page.py`/`ui/inventory_replenishment_page.py` (no UI change needed); `database/models.py` (no schema change -- `InventoryReplenishment.packing` still exists and is still populated, just no longer the value actually used for rounding at display time).

### Validation performed

(Isolated scratch DB, `app.config.DATABASE_PATH` override, plus a live real-database read-only check; scratch DB deleted after use, not committed)

- ✓ **Reproduced the exact reported scenario and confirmed the fix**: generated a threshold with the corrected parser (packing correctly 10), evaluated replenishment (fresh copy, also 10, `deficit_display` correctly rounds to a multiple of 10) -- then directly simulated `InventoryThreshold.packing` reverting to a stale `100` (exactly the real-world scenario: a threshold row not yet reprocessed since a parser fix) WITHOUT touching `InventoryReplenishment` at all. Before this fix, Replenishment would have kept showing packing=10 (stale, wrong-in-the-other-direction) while Thresholds correctly showed 100 -- a live mismatch. After the fix, Replenishment's `deficit_display` immediately reflects the live packing=100, matching the Thresholds page exactly, with `stock_deficit` (the underlying decision) provably unchanged at 145 throughout.
- ✓ **Real-data confirmation** (read-only): checked the actual live `2.0 dev` database's `BILZIT 40` rows after the fix -- Replenishment's `deficit_display` now rounds against the SAME live packing (100) the Thresholds page shows for that item, closing the exact 153-item discrepancy found during investigation.
- ✓ Re-ran the Milestone 45, 46, and 49 regression suites in full -- all checks still pass, confirming no regressions.

### Assumptions made

- `InventoryReplenishment.packing` (the stored snapshot column) is left in the schema and still populated by `evaluate_replenishment()` -- it's no longer read by `get_replenishment_required()`'s display path, but removing it entirely was out of scope for this fix and it may still be useful for audit/history purposes (what packing was in effect at the moment this row's replenishment decision was made).
- The 953 threshold rows still showing the pre-Milestone-50 packing value (and the corresponding replenishment rows, now correctly mirroring them) will only become numerically correct once a full company-wide Sales Report is re-uploaded -- this fix guarantees the two PAGES never disagree with each other, but does not, and cannot, retroactively know what any given cell's original un-parsed text was (same limitation already noted in Milestone 50).

## Milestone 52 — Hard Rule: Only 10 or 72 Are Valid Packing Sizes, Plus One-Time Repair of Already-Stored Bad Values (2026-07-28)

Explicit instruction, effective immediately and in force "until told otherwise": the current real dataset has ONLY two genuine pack sizes, 10 and 72 -- any other value the parser produces from a multiplication-notation cell is wrong. `2.0 dev` only -- no merge, no publish.

### Why "still 100s" persisted after Milestones 50/51

Checked the live database directly before making any change: 789 `InventoryThreshold` rows still held `packing=100`, and every one of them carried the exact same `last_updated` timestamp as Milestone 49's own validation sweep -- i.e. none of them had been touched by a Sales Report upload since the Milestone 50 parser fix was deployed. This was not a remaining code bug; Milestones 50 and 51 both worked correctly for anything actually reprocessed (164 rows had already correctly moved from 100 to 10 by this point, confirming the fix functions). The other 789 simply hadn't been reprocessed yet, and -- per the established, deliberate convention in this codebase -- a code fix alone never retroactively corrects an already-stored value.

Given the explicit instruction to lock this down immediately regardless, this milestone does two things: tightens the parser going forward, and directly repairs the currently-stored bad values right now rather than waiting for a future upload.

### The hard rule (parser change)

`app/threshold_service.py`: new `_HARD_RULE_PACKING_SIZES = (10.0, 72.0)`. For a multiplication-notation cell (one that contains `*`, `×`, `x`, or `X`), if either `10` or `72` appears ANYWHERE among the split parts, that value is used directly -- taking priority over the plain last-number result if they would otherwise disagree (in practice they don't, for every real notation seen so far: "1*10"/"10*10"/"5*10"/"20*10" all already resolve to 10 via the last-number rule alone, and "1*72" already resolves to 72). A plain, separator-free cell (e.g. "30") is NOT touched by this rule -- there's no multiplication artifact to correct there, so a value outside the two permitted sizes is only logged as a warning, never silently coerced. Every case that DOES fall outside the two permitted sizes now logs a clear `logger.warning()`, so any future stray notation is immediately visible in the log rather than silently passing through.

### The one-time data repair

Since only 10 and 72 exist per the hard rule, and `100` can only ever arise from a `"10*10"`-style cell being multiplied together (`72`'s only source, `"1*72"`, multiplies to `72` itself, never `100`), every currently-stored `packing=100` row can be deterministically corrected to `10` with no ambiguity. Ran a one-time, read-verified repair directly against the live `2.0 dev` database:

- Queried all 789 `InventoryThreshold` rows with `packing == 100.0`.
- Set `packing = 10.0` and recomputed `packed_threshold = _packed_threshold(raw_threshold, 10.0)` for each (using the exact same rounding function the normal generation path already uses -- no new logic introduced for this repair).
- Committed once.

Post-repair, the live database's entire `InventoryThreshold` table has exactly two distinct packing values: `10.0` (1101 rows) and `72.0` (51 rows) -- zero rows at `100` or any other value.

This was a direct, one-time correction of already-stored data (not a new standing code path) -- justified only because the business hard rule makes the correct replacement value unambiguous for this specific historical artifact. It is not a general-purpose "auto-heal stale data" mechanism.

### Files modified

- `app/threshold_service.py` -- `_parse_packing()`: added the `_HARD_RULE_PACKING_SIZES` constant and the priority-match-against-10-or-72 logic for multiplication-notation cells, plus explicit warning logs for anything landing outside the two permitted sizes. Docstring updated.
- **Data repair** (one-time, executed directly against the live database, not a code change): 789 `InventoryThreshold.packing` values corrected from `100.0` to `10.0`, with `packed_threshold` recomputed to match.

**Files NOT modified:** `app/cwh_service.py`; `app/replenishment_service.py` (needed no further change -- Milestone 51's live-read fix means the Replenishment page automatically picked up the repaired `InventoryThreshold.packing` values with zero additional work); `database/models.py` (no schema change).

### Validation performed

- ✓ Unit-tested `_parse_packing()` against every stated example plus edge cases: `"10*10"`/`"1*10"`/`"5*10"`/`"20*10"` all -> `10`; `"1*72"`/`"72*1"` -> `72`; a plain `"72"`/`"10"` unaffected; a plain `"30"` (no separator) left as-is with a warning logged (not coerced); a multiplication cell with neither 10 nor 72 present (`"5×12"`) falls back to the last-number rule with a warning logged.
- ✓ **Confirmed the repair took effect immediately on both pages**: post-repair, the Thresholds page's live packing distribution is exactly `{10.0: 1101, 72.0: 51}` (verified via `get_all_thresholds()`), and `BILZIT 40`'s Replenishment rows (previously showing a mismatched `100` on Thresholds vs. `10` on Replenishment) now correctly show `packing=10`-rounded `deficit_display` values on the Replenishment page too, automatically, via Milestone 51's live-read path -- no separate repair needed for `InventoryReplenishment`.
- ✓ Re-ran the Milestone 45, 46, and 49 regression suites in full -- all checks still pass, confirming no regressions.

### Assumptions made

- This hard rule is explicitly temporary, per the user's own framing ("until i tell you to change") -- if a genuinely new, legitimate pack size is introduced to the real data later, this rule will need to be relaxed or the new size added to `_HARD_RULE_PACKING_SIZES`; it is not intended as a permanent architectural constraint.
- The one-time repair assumed every `packing=100` row's original cell was `"10*10"`-style (the only notation that produces exactly 100 under the old buggy multiply-together parser, given only 10/72 are valid pack sizes) -- there is no stored original cell text to verify this against directly, but it is the only value-preserving explanation consistent with the hard rule itself.

## Milestone 53 — TRUE ROOT CAUSE: The Cloud Sync Layer Was Silently Reverting Local Data (2026-07-28)

The actual cause of the "packing keeps coming back as 100" saga, found after the user rightly refused another symptom fix and demanded a real root cause investigation. **The packing parser, the display read, and the data repairs from Milestones 50-52 were all correct.** None of them were ever the problem. `2.0 dev` only -- no merge, no publish.

### The evidence that broke it open

The app's own log, repeating identically at 12:55:16, 13:10:50, 13:19:51 and 13:28:31:

```
Sync: pulled 1000 row(s) from table='inventory_thresholds'
Sync: reconciled table='inventory_thresholds' -- 1000 row(s) to pull, 152 row(s) to push
Sync: pulled 1000 row(s) from table='inventory_thresholds'   [_sync_table wrote them into local SQLite]
```

Every Inventory refresh pulled 1000 rows and overwrote local. Three facts pinned it down:

1. **The reverted rows always came back with the byte-identical timestamp `12:08:48.762944`.** No parser or calculation can manufacture an exact historical microsecond stamp -- only a restore can. `_sync_table`'s pull branch sets `existing.last_updated = _parse_ts(cloud_row["last_updated"])`, which is precisely that fingerprint.
2. **The split was a constant 1000/152 that never converged**, run after run.
3. **The repairs held perfectly for 60+ seconds with the app closed**, then reverted within minutes of it running -- proving the writer was the running app's sync, not the parser.

### Root cause A -- the Last-Modified-Wins comparison could never be won locally

`app/inventory_sync_service.py::_sync_table()` stored its local timestamp under the dict key `"updated_at"` and asked `reconcile_rows()` to compare on `"updated_at"` -- but it *pushes* that timestamp to the cloud under a column named `last_updated`. The cloud table also carries Supabase's **own** server-managed `updated_at` column, in **UTC**.

So `reconcile_rows()` compared our local naive EDT `last_updated` against Supabase's UTC `updated_at` -- a value that is both 4 hours ahead *and* re-stamped to "just now" on every write. The cloud copy therefore looked strictly newer on **every matched row, on every sync, permanently**. `to_pull` won unconditionally; no local edit could ever survive. This is why every repair silently evaporated.

The arithmetic confirms it exactly: `to_pull=1000, to_push=152` means all 1000 matched keys were judged cloud-newer, with the remaining `1152 - 1000 = 152` treated as cloud-absent.

### Root cause B -- `pull_rows()` never paginated

`app/sync_service.py::pull_rows()` issued a bare `select("*")` with no range. PostgREST caps any single response at 1000 rows by default and gives **no indication it truncated**. The table holds 1152, so the last 152 rows were invisible to every reconcile: they looked "absent from the cloud", were re-pushed on every single sync, and never reconciled. That is exactly why *some* products kept the correct packing of 10 (they sat in that unreachable tail) while the other ~1000 reverted to 100 -- the split the user kept observing.

### Files modified

- `app/sync_service.py` -- `pull_rows()` now pages explicitly via `.range()` in `_PULL_PAGE_SIZE` (1000) blocks until a short page proves the end, so a full-table pull returns the whole table. An explicit `limit=` argument is still honoured verbatim (single request), so bounded callers are unaffected. `reconcile_rows()` gained a loud `logger.warning` when *no* cloud row carries the requested comparison column -- the exact silent condition that hid this defect for hours.
- `app/inventory_sync_service.py` -- new `_UPDATED_AT_COLUMN = "last_updated"`; local rows now key their timestamp under the cloud column's real name and reconcile on it, so both sides compare the same field. The push path no longer needs its rename dance.
- `app/payment_sync_service.py` -- `sync_customer_profiles()` had the **identical latent defect** (pushed as `last_updated`, compared on `updated_at`). Fixed the same way via `_CUSTOMER_PROFILE_UPDATED_AT_COLUMN`, before it could corrupt Payment data the same way.

**Audited and found already correct (left untouched):** `findings_sync_service`, `email_sync_service`, `import_sync_service`, `organization_data_sync_service`, and payment invoices/outstanding/active-months -- all of these genuinely push their timestamp *as* `updated_at`, so their comparison was always consistent.

**One-time data repair:** 651 remaining `InventoryThreshold` rows (and their `InventoryReplenishment` counterparts) corrected from `packing=100` to `10`, with `packed_threshold` recomputed. Performed with the app **fully closed**, since a running instance would otherwise have re-pulled the stale snapshot mid-repair -- which is exactly what silently undid the Milestone 52 repair attempt.

### Validation performed

- ✓ **8 new sync-layer checks** (fake in-memory cloud; no credentials or real Supabase access needed) covering: the exact production row shape (stale `last_updated` + newer server `updated_at`) now correctly **pushes** the newer local row instead of overwriting it; a **direct regression proof** that comparing `updated_at` still pulls (i.e. the old code really was the bug); a genuinely newer cloud row is **still pulled**, so Last-Modified-Wins itself is intact; ties still favour push (documented behaviour, unchanged); a 1152-row table pages back **complete** rather than capped at 1000, with no duplicates; and an explicit `limit=` is still honoured.
- ✓ **Live confirmation against the real running app** -- the log now reads:
  ```
  Sync: pulled 1152 row(s) from table='inventory_thresholds'          <- full table, was capped at 1000
  Sync: reconciled ... -- 0 row(s) to pull, 1152 row(s) to push       <- was 1000 pull / 152 push
  Sync: push complete for table='inventory_thresholds' (1152 row(s))
  ```
  Local correct data now flows **up** to the cloud, healing the stale cloud snapshot that had been the source of the reverts.
- ✓ **Packing held stable across multiple live sync cycles**: `InventoryThreshold` and `InventoryReplenishment` both steady at `{10.0: 1101, 72.0: 51}`, zero rows at 100 -- the first time any repair has survived the app actually running.
- ✓ Re-ran the Milestone 45, 46 and 49 regression suites in full -- all pass, no regressions.

### Why every previous fix failed -- and the mistake that cost the most time

Milestones 50, 51 and 52 were all in the *write* path (parser, display read, data repair) and were each individually correct. None touched the *sync* path, which reasserted a stale snapshot over the top on the very next refresh. The fixes worked; they were simply overwritten within minutes, every time.

The specific error that derailed three consecutive investigations: **Milestone 48 concluded "the cloud sync for `inventory_thresholds` is a hard no-op because the anon key gets `permission denied`", and that conclusion was wrong.** The standalone diagnostic scripts query Supabase **anonymously**, while the running app queries it **authenticated**, where RLS permits the read. Testing in a different security context than the app actually runs in produced a false negative that took cloud sync off the suspect list entirely -- and it was the culprit the whole time. Lesson recorded here deliberately: a diagnostic that cannot reproduce the app's own auth context cannot be used to *rule out* a subsystem.

### Assumptions made

- Root cause A's precise mechanism (a Supabase-managed UTC `updated_at` column on these tables) is the explanation consistent with every observed number and with the now-confirmed fix behaviour, but the cloud column itself was inferred rather than directly read -- the anon key is refused `SELECT` and the user's credentials were deliberately not handled.
- With ties favouring push (pre-existing, documented behaviour), a full 1152-row push now occurs on each Inventory refresh rather than the previous 152. Functionally correct and observed to complete in ~0.5s; left as-is rather than changing the tie-break rule, which is out of scope for this fix.

## Milestone 54 — Table Export Framework (New Development Standard), Replenishment as Reference Implementation (2026-07-28)

New reusable architecture, not a one-off feature: a generic "export exactly what's on screen" framework any table in the app can plug into, requiring each page to supply only four things (dataset, columns, headers, filename). Replenishment is the first and reference implementation. `2.0 dev` only -- no merge, no publish.

### Framework design

New module `app/table_export_service.py` -- the framework's entire skeleton lives here, exactly once. Full design rationale and a worked adoption example are in the module's own docstring (the intended living reference for future pages, not just this log entry). Two layers:

- **`write_rows_to_excel(rows, columns, headings, file_path, *, sheet_title=, row_style_fn=, progress_callback=)`** -- the ONE canonical writer. Pure and synchronous: no UI, no threading, no dialog. Builds a single-sheet `openpyxl` workbook with a bold brand-orange header row (`ui.theme.Color.PRIMARY`), frozen header pane, and auto-sized columns. `row_style_fn(row) -> RowStyle | None` is the extension point letting a table mirror an on-screen row highlight (fill/font color) into the exported cells -- optional, ignored entirely by tables with no such concept. Never raises for an ordinary write failure (bad path, disk full, file locked elsewhere) -- returns `ExportResult(success=False, error_message=...)` instead, matching this codebase's existing `RowsSyncResult`-style "failure is data, not an exception" convention (see `app/sync_service.py`).
- **`export_rows_with_ui(widget, rows, columns, headings, suggested_filename, *, ...)`** -- the one call a page's Export button needs. `prompt_save_path()` opens a native Save dialog (seeded with `app.config.REPORTS_DIR` -- declared at startup since the very first version of this app but never actually used anywhere until now) synchronously on the main thread (a Tkinter requirement), then the actual write runs on a background thread behind a `LoadingOverlay`, using the exact same `run_in_background` + `LoadingOverlay` pattern `ui/inventory_upload_page.py` and `ui/sales_upload_page.py` already use for uploads -- applied here to the export direction instead, so a page with a very large table never freezes its window. Shows a success/error `messagebox` on completion.

**Why the "exactly what's on screen" guarantee actually holds:** the framework never queries, filters, or sorts data itself. A calling page hands it the literal list of dicts it JUST used to populate its own Treeview. There is no second source of truth to drift out of sync with the visible table -- if a page's current branch filter, division filter, search box, or column sort changed what's rendered, that same change is already reflected in the list handed to the framework, because it IS that list.

**Future-workflow compatibility (explicitly not built yet, kept possible only):** the eventual automated pipeline (`Inventory Upload -> Replenishment Generated -> Export Framework -> Excel File -> Automatic Email Attachment`) will call `write_rows_to_excel()` directly -- no dialog, no threading, since there's no user present to click Save and the caller controls its own thread. Because it is the SAME function the Export button calls, a human's downloaded workbook and a future automated email's attached workbook can never independently drift into two different implementations. No email-sending code was added in this milestone, per explicit instruction -- only this compatibility guarantee.

### Reference implementation: Replenishment page

`ui/inventory_replenishment_page.py`:
- New `self._current_display_rows` -- set inside `_render_table()` to the exact post-filter `rows` list, captured BEFORE the "no rows match" empty-state check (so it's correctly `[]`, not stale, when nothing matches).
- New `_on_export_clicked()` -- the entire integration: calls `export_rows_with_ui(self, rows=self._current_display_rows, columns=COLUMNS, headings=HEADINGS, suggested_filename=default_export_filename("Replenishment", suffix=<active branch/division filter, if any>), sheet_title="Replenishment", row_style_fn=_replenishment_row_style)`.
- New module-level `_replenishment_row_style(row)` -- mirrors this page's existing on-screen CWH-shortage red highlight (`Color.CRITICAL_ROW_BG`/`CRITICAL_ROW_TEXT`, from Milestone 46) into the exported row's cell fill/font, so a downloaded workbook visually matches what the user was looking at.
- New "Export" button (`PrimaryButton` + new `download` icon) added to the existing filter row, right-aligned next to the branch/division dropdowns.

**Files modified:** `app/table_export_service.py` (new); `ui/inventory_replenishment_page.py` (Export button + `_current_display_rows` + `_on_export_clicked` + `_replenishment_row_style`); `ui/icons.py` (new `icon_download()`, the visual inverse of the existing `icon_upload()`, registered under `"download"`).

**Files NOT modified:** `app/replenishment_service.py`/`get_replenishment_required()` (the export reads whatever this already returns -- no new data-layer code needed); every other page (Thresholds, CWH, etc. -- explicitly scoped to Replenishment only for this first implementation, per instruction).

### Validation performed

- ✓ Constructed a real `InventoryReplenishmentPage` against the live database (365 real replenishment rows) and confirmed `_current_display_rows` exactly equals `_all_rows` under no filter, then narrows to exactly the matching subset (verified count and branch-equality) after applying a real branch filter -- proving the "exact same list" guarantee holds against real data, not just a synthetic test.
- ✓ **Wrote a real `.xlsx` file** via the framework using that filtered real data (50 rows, branch-filtered), then **independently re-opened it with `openpyxl`** (a separate read, not trusting the writer's own success flag) and verified: sheet title, `A1:I51` dimensions (50 data rows + 1 header, exactly matching the filtered count), frozen header pane, every header cell bold/white-on-brand-orange, every data cell's value matches the exact on-screen formatted string (e.g. `"360 (5 Packs)"`, not a re-derived raw number), column widths auto-sized, and -- critically -- **exactly 15 rows carry the CWH-shortage fill/font color**, matching the live page's own `cwh_shortage` count for that filter precisely.
- ✓ Edge cases on the pure writer: an empty row list still produces a valid header-only workbook (no error); an unwritable path (`Z:\` -- nonexistent drive) returns `ExportResult(success=False, error_message=...)` without raising; a sheet title over Excel's 31-character limit is truncated rather than raising `openpyxl`'s own `InvalidSheetNameException`.
- ✓ Full `InventoryModule` smoke test -- constructed the real module and cycled every page's `on_show()` (Dashboard, Inventory Upload, Previous Month Sales Upload, Thresholds, Replenishment, Central Warehouse, Settings) -- all succeeded, confirming the new icon/button/import additions introduced no regressions anywhere else in the module.

### Assumptions made

- The Save dialog's default directory is `app.config.REPORTS_DIR` (a per-user, always-writable folder, auto-created at app startup since Version 2.0's earliest commit but never actually used by any code until this milestone) rather than a fixed, non-negotiable export location -- matching the existing upload-side convention of always letting the user pick/redirect the path via a native dialog, never silently writing somewhere the user can't see or choose.
- "Current sorting" and "current search results" (named in the task's requirements) are captured by the framework's design in principle -- whatever list a page hands it is exported verbatim -- but Replenishment itself currently has neither a search box nor column-click sorting (only the pre-existing branch/division dropdown filters), so there was nothing further to wire up for THIS page specifically. Adding either feature later requires zero export-framework changes, only updating what list `_render_table()` computes -- which is exactly the point of the "call site owns filtering, framework owns everything else" split.
- The exported filename includes the active branch/division filter (e.g. `Replenishment_GHAZIABAD_2026-07-28.xlsx`) as a convenience so a downloaded file is self-describing -- not explicitly required by the task, but low-risk and directly useful given the task's own "if filtered to Zandra, only Zandra should be exported" example.

## Milestone 55 — Table Export Framework Rolled Out Application-Wide (2026-07-28)

Every real data table in the application now has an "Export to Excel" button, all sharing the exact same framework built in Milestone 54 -- no new export logic was written anywhere; every page below is a thin wrapper around `app.table_export_service`'s existing `export_rows_with_ui()`/`write_rows_to_excel()`. `2.0 dev` only -- no merge, no publish.

### 1. Every table that now supports export (10 total, including Replenishment)

| # | Page (file → class) | Filter/search/sort captured | Row highlight mirrored into export |
|---|---|---|---|
| 1 | `ui/inventory_replenishment_page.py` → `InventoryReplenishmentPage` | CFA + Division dropdowns (Milestone 54) | CWH-shortage red |
| 2 | `ui/inventory_thresholds_page.py` → `InventoryThresholdsPage` | CFA + Division dropdowns, item-name search | none (page has none) |
| 3 | `ui/inventory_cwh_page.py` → `InventoryCwhPage` | Division dropdown, SKU search, item-name search | Healthy/Low Stock/Critical status color (all 3, every row) |
| 4 | `ui/findings_page.py` → `FindingsPage` | Status dropdown, employee/code/message search, **column-click sort** | Suppressed / Suppression Inconclusive / Region Suppressed |
| 5 | `ui/email_center_page.py` → `EmailCenterPage` | none (page has none -- full send log always) | none (page has none) |
| 6 | `ui/operations_page.py` → `OperationsPage` (Import History) | none (page has none -- last 20 imports always) | none (page has none) |
| 7 | `ui/organization_data_page.py` → `OrganizationDataPage` | Search only | none (page has none) |
| 8 | `ui/payment_collections_page.py` → `PaymentCollectionsPage` | 6 dropdowns (Division, HQ, Status, Days Over Due, Due Date, Follow-up) + 2 searches (Party, Bill No.) + custom date range -- the most filter-rich page in the app | Status color, overridden by "followed up" grey once checked off |
| 9 | `ui/payment_customer_analytics_page.py` → `PaymentCustomerAnalyticsPage` | Risk dropdown, search, **column-click sort** | Risk category color (Green/Yellow/Orange/Red) |
| 10 | `ui/payment_dashboard_page.py` → `PaymentDashboardPage` (Critical Customers) | none (page has none -- always every Red-risk customer) | Critical red (single color, every row) |

Every page's `on_show()`/reload behavior, filter logic, sort logic, and Treeview rendering are otherwise completely unchanged -- the only additions are: an `Export` button, a `self._current_display_rows` capture, an `_on_export_clicked()` (or `_on_export_history_clicked()` for Operations) handler, and -- for pages with row highlighting -- a small `_<page>_row_style()` function mirroring the existing Treeview tag colors.

### 2. Confirmation every export uses the shared framework

Zero new export/Excel-writing code was added anywhere in this milestone. Every one of the 10 pages calls the identical `app.table_export_service.export_rows_with_ui()` (itself unmodified since Milestone 54) with page-specific `rows`/`columns`/`headings`/`suggested_filename`/`row_style_fn` arguments only. Confirmed by direct grep: `export_rows_with_ui` and `default_export_filename` are imported from `app.table_export_service` in all 10 pages and nowhere else is `openpyxl.Workbook(` or `asksaveasfilename` called in `ui/*.py`.

### 3. Confirmation every export remains filter-aware and UI-aware

The mechanism guaranteeing this is structural, not per-page discipline: every page captures `self._current_display_rows` **inside its own existing render method**, set to the exact list already being looped over to call `tree.insert(...)` -- never a second, independently-filtered query. For the three data sources that aren't naturally dict-shaped, the exact on-screen value is captured, not re-derived:

- **ORM objects** (Findings' `Finding` rows, Email Center's `EmailNotification` rows) -- a `display_row` dict is built ONCE per row, using the identical expressions the Treeview's own `values=` tuple already used (including any display transforms, e.g. Findings' `_notification_status_display()`), and BOTH the Treeview insert and the exported list read from that same dict -- they cannot drift apart because there is only one place the values are computed.
- **Plain tuples** (Organization Data's `df.itertuples()` rows, Operations' `ImportHistory` query results) -- zipped/rebuilt into dicts keyed by the page's own `COLUMNS`, preserving the exact same values and order already shown.
- **Internal-only style keys** (Findings' `_row_tag`, Payment Collections' `_followed_up`) -- stashed into the row dict alongside the real columns so `row_style_fn` can read them, but never included in `columns`, so they're invisible in the exported spreadsheet itself.

### 4. Tables that could not be migrated in this pass, with reasons

- **`ui/user_table.py`'s `UserTable`** (used by `ui/user_management_page.py`'s `UserManagementPage`) -- **not built on `ttk.Treeview`/`styled_treeview` at all**. It's a `CTkScrollableFrame` with one real CTk widget per cell (`StatusBadge`, a working Edit button, a working Enable/Disable button embedded directly in every row) -- an explicit, deliberate design choice documented in the file's own docstring, since Treeview cells cannot host real interactive widgets. The export framework's actual data contract (`rows: list[dict]`, `columns`, `headings`) does not strictly require a Treeview -- `UserManagementPage._filtered_sorted_users()` already computes exactly the right filtered/sorted list -- so this is NOT an architectural dead end, just out of scope for this pass: adding it would mean touching an admin/user-management page not otherwise examined in this session, which deserves its own dedicated pass rather than a rushed addition here.
- **`ui/inventory_dashboard_page.py`'s "Recent Uploads" table** -- **hardcoded placeholder data** (`RECENT_UPLOADS_ROWS`, four literal fake filenames/dates, e.g. `"Inventory_Report_June2026.xlsx"`), confirmed never reloaded in `on_show()` (which only refreshes the page's KPI cards). Wiring an Export button to this table would let a user download a spreadsheet of invented, non-existent uploads presented as if real -- actively misleading rather than merely incomplete. Left unmigrated until this table is backed by a real query.

Every other `ttk.Treeview`/`styled_treeview` table found anywhere under `ui/*.py` is accounted for above -- none were skipped.

### Validation performed

- ✓ **Full-application smoke test**: constructed all 10 migrated pages directly (plus a fresh check of the already-migrated Replenishment), called each `on_show()`, confirmed `_current_display_rows` exists as a list and the export handler exists, against REAL data where present: Thresholds 1152 rows, CWH 105, Replenishment 365, Findings 140, Email Center 28, Organization Data 594, Operations 3. Payment pages correctly showed 0 (no Payment historical data currently loaded in this environment -- a legitimate empty state, not a defect; the attributes/handlers are present and correctly structured regardless).
- ✓ **Real filtered export, independently re-verified**: filtered Thresholds to a real CFA (98 of 1152 rows), exported, then re-opened the file with `openpyxl` (not trusting the writer's own success flag) -- row count matched the filter exactly, and every exported row's CFA value matched the filter.
- ✓ **Real row-highlight export, independently re-verified**: exported CWH (all 105 rows always carry a status color by this page's own design -- confirmed every row's fill exactly matches its own status: Healthy → green, Low Stock → yellow, Critical Shortage → red, with zero cross-contamination between statuses).
- ✓ **ORM-to-dict conversion verified**: exported Findings (140 real rows, ORM `Finding` objects) -- row count matched exactly, and the internal `_row_tag` styling key correctly does NOT appear as a spreadsheet column header.
- ✓ **Tuple-to-dict conversion + search-filter verified**: applied a real partial-name search to Organization Data (594 → 5 matching rows), exported, confirmed the write succeeded with the filtered count.
- ✓ Re-ran the Milestone 45, 46, and 49 Inventory regression suites in full -- all pass, confirming the Thresholds/CWH page edits introduced no regressions in unrelated calculation logic.
- ✓ Relaunched `2.0 dev` with the full rollout in place; clean startup, no errors.

### Assumptions made

- Pages with no filter/search/sort state of their own (Email Center's send log, Operations' import history, Payment Dashboard's Critical Customers) correctly export their full current row set with no additional wiring -- there is no missing filter-awareness here, these pages simply have no filters to be aware of; the framework contract is already satisfied by "export exactly what's rendered," which for these pages is unconditionally everything currently on screen.
- Where a page's Treeview is embedded directly in its content area with no natural "filter row" header to attach an Export button to (Email Center, Operations, Payment Collections, Payment Dashboard), a small header row was added directly above that specific table (label + right-aligned Export button) rather than forcing it into an existing, differently-purposed row -- kept visually consistent with every other page's placement (top-right of the table's own area) without disturbing surrounding layout.

## Milestone 56 — Inventory Settings Scroll Fix + Automated Email System (Foundation) (2026-07-29)

Two-part milestone: (1) a small scrolling fix to Inventory Settings, and (2) the Inventory Automated Email System -- built explicitly as a reuse of the existing, already-tested Path Validator Automated Email System (`app/smtp_service.py`, `app/email_settings_service.py`, `ui/settings_page.py`'s Email Settings card), not a reimplementation. `2.0 dev` only -- no merge, no publish.

### Part 1 -- Inventory Settings scrolling fix

`ui/inventory_settings_page.py`'s outer container changed from a plain `CTkFrame` to a `CTkScrollableFrame` (same one-line pattern already used by `ui/operations_page.py` and `ui/payment_collections_page.py` for tall settings-style pages) -- pure container swap, no restyle. The page had grown past available vertical space once the CFA Threshold Multiplier, CWH Threshold Multiplier, Threshold Display Mode, and (this milestone's own) Email Configuration cards were all present, compressing the Save button and hiding the bottom of the page with no way to reach it. Every setting is now always reachable regardless of window height.

### Part 2 -- Automated Email System (Foundation)

**Reused from Path Validator, unmodified in behavior:**
- `app/smtp_service.py` -- `open_smtp_connection`, `send_via_connection`, `send_email`, `test_connection` all gained new OPTIONAL trailing parameters (`sender_email=None, app_password=None` and, on the send functions, `attachments=None`). Every existing Path Validator call site passes none of these and is unaffected -- confirmed by inspecting every call site (`app/notification_service.py`, `ui/settings_page.py`) and by comparing part counts on `_build_message()` with/without an attachment.
- `app/table_export_service.py` -- called directly, not duplicated. The Inventory email system's attachment generator (`_generate_attachment()` in the new `app/inventory_notification_service.py`) calls the exact same `write_rows_to_excel()` the Replenishment page's own Export button calls (via a temp file, since that's the function's existing public contract) -- there is only ONE export implementation in the application, per explicit instruction.
- `app/replenishment_service.py` -- `REPLENISHMENT_REPORT_COLUMNS`/`REPLENISHMENT_REPORT_HEADINGS`/`REPLENISHMENT_REPORT_SHORTAGE_FILL`/`REPLENISHMENT_REPORT_SHORTAGE_TEXT` moved here from `ui/inventory_replenishment_page.py` (which now imports them back, aliased to its existing `COLUMNS`/`HEADINGS` names -- zero behavior change, verified byte-identical) so both the manual Export button and the new email system read the SAME single source of truth for what a "Replenishment report" contains.
- `ui/user_dialogs.py`'s `ConfirmDialog` -- reused as-is for the new page's Delete Recipient confirmation; no second confirmation dialog was written.
- Design patterns mirrored (not code-shared, since the underlying data model differs): `ui/settings_page.py`'s Email Settings card layout/copy/save-then-test flow; `ui/email_center_page.py`'s Send Log Treeview + Export + double-click-to-view-body pattern; `ui/user_management_page.py` + `ui/user_dialogs.py`'s list + Add/Edit dialog shape (the closest existing precedent for a manually-managed CRUD list, since Path Validator itself has no such page -- its own recipients resolve dynamically from the hierarchy).

**New components built for Inventory:**
- `database/models.py` -- two new tables: `InventoryEmailRecipient` (id, name, email, `divisions` as a sorted comma-joined string, timestamps) and `InventoryEmailNotification` (one row per send attempt: `report_type` (future-proofed, currently always `"Replenishment"`), recipient name/email/divisions, subject, body, row_count, `status` ∈ {Draft, Sent, Failed, **Skipped - No Data**}, error_message, timestamps). No migration function needed -- `Base.metadata.create_all()` inside `init_db()` picks up new model classes automatically.
- `app/inventory_email_settings_service.py` -- Inventory's OWN sender email/app password/automatic-sending-enabled, stored as three `InventoryParameter` rows written/read DIRECTLY via `get_config_session()`, deliberately bypassing `app/inventory_parameters_service.py`'s cloud-sync wiring -- mirrors Path Validator's own `email_settings_service.py`, which likewise never pushes `gmail_app_password` to the cloud. Verified these three keys never appear in `get_full_configuration()`'s output. A genuinely separate credential set from Path Validator's own (Inventory reports may need to send from a different Gmail account); no `master_email` field (Inventory has no hierarchy-fallback consolidated-rollup concept).
- `app/inventory_email_recipients_service.py` -- CRUD for manually-configured recipients. `DIVISION_OPTIONS = ("GUARDIANS", "ONYX", "XANDRA")` -- the real, currently-stored Division spellings (confirmed against the live database), not the task's own illustrative "Onyx/Guardian/Zandra" examples. `create_recipient`/`update_recipient`/`delete_recipient`/`get_all_recipients`, all working in terms of a plain `list[str]` of divisions (comma-join/split handled internally).
- `app/inventory_send_state.py` -- mirrors `app/send_state.py`'s shape exactly, but as a genuinely separate module-level state dict with no `import_id`-equivalent key (Inventory has only ever one "the current Replenishment send" in flight, not multiple concurrent import sessions) -- kept separate from Path Validator's own send state so one module's send-in-progress flag can never answer for the other's.
- `app/inventory_notification_service.py` -- the core service, deliberately split into a GENERIC half and a thin Replenishment-specific half, per the task's own explicit "design this as a reusable reporting framework" instruction:
  - Generic: `build_email_batch_for_report(report_type, rows)` (filters `rows` by each recipient's divisions, one draft per recipient, `Skipped - No Data` if their filtered set is empty) and `send_report_batch(drafts, columns, headings, *, report_type, sheet_title, row_style_fn, progress_callback)` (opens one shared SMTP connection, sends/logs each draft, commits every 3 emails) -- neither function knows anything about Replenishment specifically.
  - Replenishment-specific: `build_inventory_replenishment_email_batch()` and `send_inventory_replenishment_emails()` -- thin wrappers supplying Replenishment's own columns/headings/row-style/report-type to the generic functions above. A future Threshold/CWH/Employee-Detector/Path-Validator/Scheduled report needs only its own equally-thin wrapper -- zero changes to the generic core.
- `ui/inventory_settings_page.py` -- new Email Configuration card (sender email, app password, Enable Automatic Email Sending switch, Save + Test Connection buttons), built to mirror `ui/settings_page.py`'s own Email Settings card as closely as possible, including its exact synchronous "save-then-send-a-real-test-email" Test Connection behavior (via `smtp_service.test_connection`'s new optional credential-override parameters).
- `ui/inventory_email_recipient_dialog.py` (new) -- `RecipientFormDialog`, mirroring `ui/user_dialogs.py`'s `UserFormDialog` shape (title/geometry/centering, error label, Save/Cancel, `finish_saving()`), with Division checkboxes instead of a role dropdown. Recipient writes are small local SQLite calls, not network calls, so they run synchronously with no "Saving..." delay.
- `ui/inventory_automated_emails_page.py` (new) -- the "Automated Emails" page: a Recipients card (Treeview list, `+ Add Recipient`, double-click-to-edit, Delete Selected with confirmation) and a Send Log card (Treeview of every `InventoryEmailNotification` row, Export button reusing the shared export framework, double-click to view the full sent email body) -- registered in `ui/inventory_module.py`'s `BASE_PAGES` (between Central Warehouse (CWH) and Settings) and `ui/icons.py` (new `"Automated Emails"` key, reusing the existing bell/`icon_notifications` glyph Email Center already uses).
- `ui/inventory_upload_page.py` -- automatic-send trigger. Once replenishment evaluation succeeds and its own progress bar/loading overlay has already hidden itself, `if is_automatic_sending_enabled(): self._start_automatic_send(summary_text)` spins up a SEPARATE background thread (mirrors `ui/operations_page.py`'s `_start_automatic_send` exactly) calling `send_inventory_replenishment_emails()`, wrapped in `inventory_send_state.start_sending()`/`finish_sending()`, with the status label updated on completion (sent/failed/skipped counts) or on error.

### A real bug found and fixed while wiring the trigger

While testing `_start_automatic_send`'s exception path (mocked the send function to raise), the `except Exception as exc: ... self.after(0, lambda: self._on_automatic_send_done(..., error=exc))` pattern crashed with `NameError: cannot access free variable 'exc'` instead of showing the error. Python implicitly deletes an `except ... as exc` binding the moment its block exits, but the lambda passed to `self.after(0, ...)` doesn't actually run until later, on the Tk main loop -- by which point `exc` no longer exists in that closure. Fixed in `ui/inventory_upload_page.py` by assigning `exc` to a plain local (`error = exc`) before building the lambda. **This exact same pattern already exists in `ui/operations_page.py`'s own `_start_automatic_send`** (the reference implementation this was copied from) -- flagged as a separate out-of-scope fix rather than touched here, since Path Validator's own working code was explicitly off-limits for this milestone.

### Files modified/created

- New: `app/inventory_email_settings_service.py`, `app/inventory_email_recipients_service.py`, `app/inventory_send_state.py`, `app/inventory_notification_service.py`, `ui/inventory_email_recipient_dialog.py`, `ui/inventory_automated_emails_page.py`.
- Modified: `database/models.py` (2 new model classes), `app/smtp_service.py` (optional credential/attachment parameters), `app/replenishment_service.py` (report column/heading/color constants moved in), `ui/inventory_replenishment_page.py` (imports constants back, no behavior change), `ui/inventory_settings_page.py` (scrollable container + Email Configuration card), `ui/inventory_upload_page.py` (automatic-send trigger + bugfix), `ui/inventory_module.py` (new page registered), `ui/icons.py` (new icon key, reusing an existing glyph).

### Validation performed

- ✓ Each new backend module tested independently against a scratch SQLite database (never the real app database), each scratch DB removed immediately after its test run: `inventory_email_settings_service` (round-trip save/load, confirmed cloud-sync exclusion), `inventory_email_recipients_service` (12 checks: create/order/serialize/update-including-missing-id/delete-including-missing-id), `inventory_send_state` (state transition sequence), `inventory_notification_service` (11 checks with mocked SMTP: per-recipient division filtering including a recipient whose division has zero matching rows correctly getting `Skipped - No Data` and never actually sent, real `.xlsx` attachment bytes confirmed via genuine zip magic bytes, exactly 3 `InventoryEmailNotification` rows logged with the correct statuses).
- ✓ `ui/inventory_settings_page.py` constructed in a real (non-mocked) `CTkScrollableFrame`-backed Tk root: Email Configuration card save/reload round-trips correctly through the real `inventory_email_settings_service`, `on_show()` reloads all four cards.
- ✓ `ui/inventory_automated_emails_page.py` constructed against a real Tk root + scratch DB: empty state renders; add/edit/delete recipient all round-trip through the real service layer; Send Log renders a real `InventoryEmailNotification` row and captures it correctly into the export-ready row list.
- ✓ `ui/inventory_module.py` constructed with the new page registered: `"Automated Emails"` present in both `pages` and `nav_buttons`, `show_page("Automated Emails")` correctly activates it.
- ✓ **Full end-to-end integration test** (the most complete check performed) -- seeded real `InventoryReplenishment` rows (one `ONYX` needing replenishment, one `GUARDIANS` needing replenishment, one healthy row correctly excluded) directly in a scratch database, configured real recipients and real (mocked-SMTP) email settings via the real service layer, then called `send_inventory_replenishment_emails()` with no arguments (its default, production code path: `get_replenishment_required()` reads the real table itself) -- confirmed the ONYX recipient received exactly the ONYX row, the GUARDIANS+XANDRA recipient received exactly the GUARDIANS row (no XANDRA data existed), both attachments were genuine `.xlsx` files (zip magic bytes), both `InventoryEmailNotification` rows logged with `status="Sent"`, and zero real network/SMTP calls were made.
- ✓ Automatic-send trigger wiring tested against a real Tk main loop (not just a manual `update()` polling loop, which does not correctly service background-thread `.after()` calls) -- both the success path (status label shows sent/skipped counts) and the exception path (status label shows the real error message, post-bugfix) verified.
- ✓ All touched modules (`ui.inventory_module`, `ui.inventory_settings_page`, `ui.inventory_upload_page`, `ui.inventory_automated_emails_page`, `ui.inventory_email_recipient_dialog`, `ui.settings_page`, `ui.operations_page`, `ui.email_center_page`, `app.smtp_service`, `app.notification_service`, `app.inventory_notification_service`, `app.inventory_email_settings_service`, `app.inventory_email_recipients_service`, `app.inventory_send_state`, `app.replenishment_service`, `ui.inventory_replenishment_page`) import cleanly together in one process -- no circular imports or cross-module breakage introduced.
- All scratch databases created for this milestone's testing were deleted immediately after each test run completed.

### Assumptions made

- No "preview when Automatic Email Sending is off" mode was built (unlike Path Validator's `_start_preview_only`, which generates Draft-status email batches for review even when automatic sending is disabled) -- the task's own automated-workflow description and validation checklist describe only the enabled-automatic-sending path; adding an equivalent preview mode was judged out of scope for this foundation milestone rather than silently expanding it. Recipients and credentials remain fully configurable regardless of the switch's state, ready for the next time it's turned on.
- No manual "Send Now" button was added to the Automated Emails page for when Automatic Email Sending is off (Path Validator's Email Center has an analogous manual "Send All Emails" for that case) -- same reasoning: not named in the task's explicit requirements, and `send_inventory_replenishment_emails()` is already fully wired and independently tested, so adding one later is a single small addition to an already-working function, not a redesign.
- Division checkboxes display the real stored spellings (`GUARDIANS`, `ONYX`, `XANDRA`) rather than the task's own illustrative "Onyx/Guardian/Zandra" examples, matching every other Division-aware screen in Inventory Monitoring rather than introducing a display-only relabeling layer nothing else in the app has.

### Recommended next step

- Decide whether to extend this same reusable framework to a second report type (Threshold or CWH) next, which should now only require a thin wrapper pair (`build_<x>_email_batch()`/`send_<x>_emails()`) plus that report's own column/heading constants -- no changes to `build_email_batch_for_report()`/`send_report_batch()` themselves -- or to first add the preview-mode/manual-send parity noted above.

## Milestone 57 — Inventory Excel Format Migration: New One-Row-Per-Product Layout (2026-07-29)

The Inventory Report's incoming Excel shape changed again (a new ERP export layout), per explicit instruction to adapt ONLY the Import/Parsing layer -- zero changes to Thresholds, Replenishment, CWH, the Export Framework, or the Automated Email System. `2.0 dev` only -- no merge, no publish.

### Parsing strategy

`app/excel_validation.py` already had a two-tier "translate into the same flat shape, try newest format first" pattern from a previous format change (`validate_pivoted_inventory_report`, added before this session). This milestone adds a THIRD tier ahead of it, following the identical pattern: detect the new header shape, unpivot into the exact same flat DataFrame shape (`BranchLocation`, `Item Code`, `Item Name`, `TotalQty`, `Transit Stock`) every format before it already produced, and hand that off unchanged. `evaluate_replenishment()` and `evaluate_cwh_stock()` consume this DataFrame and have no way to tell which format produced it -- neither function was touched.

New format: one row per product (`Product` identity column), followed by one column group per warehouse. Unlike the older pivoted format's fixed 5-column groups, this format's groups are variable-width: CWH and Ahmedabad each carry exactly one column (`Closing` only), every other CFA carries two (`Closing` then `Transit`). `_detect_new_format_groups()` reads each group's width directly from how many consecutive columns share the same forward-filled warehouse name in the header row (merged-cell anchor value), rather than assuming a fixed width -- so the parser adapts to however many real CFAs a given file lists without any hardcoded branch list.

Detection order in `validate_inventory_report()`: new format → old pivoted format → old flat format, each tried in full before falling through, and each tier's own real, format-specific malformed-file error is returned immediately rather than silently falling through to a different tier's misleading "missing columns" message (same discipline the existing pivoted-vs-flat fallback already followed).

### Mapping from the new Excel format to the internal inventory model

| New Excel format | Internal model (unchanged since before this milestone) |
|---|---|
| `Product` (row identity) | `Item Name` |
| *(no code/alias column in this format)* | `Item Code` = `None` (nullable, display-only downstream -- same as it already was for the pivoted format's missing `Item Group`) |
| Warehouse group's own header name (e.g. `AMBALA`) | `BranchLocation` = that name, unchanged |
| CWH group's header name (however spelled -- `CWH`, `Central Warehouse`, etc.) | `BranchLocation` = synthesized `NEW_FORMAT_CWH_BRANCH_LABEL` = `"AHMEDABAD (CWH)"` |
| Any group's `Closing` column | `TotalQty` |
| A 2-column group's `Transit` column | `Transit Stock` |
| A 1-column group (CWH, Ahmedabad -- no Transit column at all) | `Transit Stock` = `0` (forced; there is no column to read) |

The one deliberate piece of translation logic (not a business-logic change, a *labeling* decision so the existing exclusion mechanism keeps working): CWH's column is renamed to `"AHMEDABAD (CWH)"` specifically because `app.threshold_service.is_ahmedabad_cwh_branch()` -- the existing, untouched function both `evaluate_replenishment()` (excludes) and `evaluate_cwh_stock()` (includes) already call -- recognizes "ahmedabad" followed by "cwh"/"central warehouse"/"head office". Ahmedabad's own group is passed through with its real name (`"AHMEDABAD"`, no suffix), which that same regex does NOT match, so it is correctly evaluated as an ordinary CFA. This one naming choice is what makes rules 1 and 2 of the task (CWH isolated, Ahmedabad normal) fall out of the EXISTING exclusion mechanism with zero new business-rule code.

### Files modified

- `app/excel_validation.py` -- added `_is_new_format_cwh_group()`, `_detect_new_format_groups()`, `NEW_FORMAT_CWH_BRANCH_LABEL`, `validate_new_format_inventory_report()` (new); `validate_inventory_report()` now tries the new format first, falling through to the existing pivoted/flat tiers unchanged.

**Files NOT modified (per explicit instruction, and confirmed by inspection):** `app/replenishment_service.py`, `app/cwh_service.py`, `app/threshold_service.py`, `app/table_export_service.py`, `app/inventory_notification_service.py`, `app/inventory_email_recipients_service.py`, `app/inventory_email_settings_service.py`, every Inventory UI page. `evaluate_replenishment()`'s `effective_available_stock = closing_stock + transit_stock` calculation is byte-for-byte the same line it was before this milestone.

### Validation performed

- ✓ **Parser-level**: built a real `.xlsx` (via `openpyxl`, real merged header cells, not a synthetic DataFrame) matching the new format exactly as specified -- `Product | CWH | AHMEDABAD | AMBALA (merged, 2 cols) | BANGALORE (merged, 2 cols)` -- across 2 products. Confirmed: 8 unpivoted records (2 products × 4 groups); CWH rows carry `BranchLocation="AHMEDABAD (CWH)"` and `Transit Stock=0`; Ahmedabad rows carry `BranchLocation="AHMEDABAD"` and `Transit Stock=0`; Ambala/Bangalore rows carry their real Closing AND Transit values; `is_ahmedabad_cwh_branch("AHMEDABAD (CWH)")` is `True` and `is_ahmedabad_cwh_branch("AHMEDABAD")` is `False`; the public `validate_inventory_report()` entry point picks the new format up first and returns the identical result.
- ✓ **Malformed-file handling**: a file with a `Product` identity row but an invalid 3-column group returns `success=False` with a specific, format-aware error message -- confirmed it does NOT fall through to the pivoted or flat tiers' own (misleading) missing-columns errors.
- ✓ **Regression, old pivoted format**: a real `Alias`/`Product` 5-column-group file (unchanged from before this milestone) still validates successfully through the public `validate_inventory_report()` entry point, unpivoted identically to before.
- ✓ **Regression, old flat format**: a real flat `BranchLocation`/`Item Group`/`Item Code`/`Item Name`/`TotalQty`/`Transit Stock` file still validates successfully as the final fallback tier.
- ✓ **Full end-to-end pipeline against a scratch database** (the most complete check performed): generated real `InventoryThreshold` rows from a synthetic Previous Month Sales Report (3 CFAs: Ahmedabad, Ambala, Bangalore, unrelated to this milestone's changes), then fed the new-format-parsed DataFrame through the **completely unmodified** `evaluate_replenishment()` and `evaluate_cwh_stock()`. Confirmed via direct DB query of `InventoryReplenishment`/`InventoryThreshold`: CWH's synthesized branch never appears in either table (2 rows correctly excluded, logged by the existing exclusion log line, unchanged); Ahmedabad appears as a normal CFA row with `transit_stock=0` and `effective_available_stock == closing_stock` (Closing + 0); Ambala/Bangalore rows show `effective_available_stock == closing_stock + transit_stock` exactly as before (e.g. 30 + 5 = 35, 100 + 20 = 120); `get_cwh_overview()` (the CWH module's own read path) correctly shows the CWH stock, isolated from Replenishment.
- ✓ **Export + Email pipeline against the same data**: configured a real recipient and (mocked-SMTP) email settings, called the unmodified `send_inventory_replenishment_emails()` -- it built and "sent" a real `.xlsx` attachment from the new-format-derived Replenishment rows with zero code changes to the export or email layers.
- ✓ Re-ran `tests/test_inventory_module_shell.py` (full module construction + every page's `on_show()` cycled) -- passes unchanged.
- All scratch files/databases created for this milestone's testing were deleted immediately after each test run completed.

### Assumptions made

- The new format's CWH/Ahmedabad column-group WIDTH (1 column, Closing only) is not by itself enough to tell "this is CWH" apart from "this is Ahmedabad" -- both are structurally identical single-Closing-column groups. Distinguishing them is done by the group's own header NAME (`_is_new_format_cwh_group()` matches "cwh"/"central warehouse" case-insensitively), not position or column count. If a real file ever labels the CWH column something entirely unrelated to "CWH"/"Central Warehouse" (e.g. a plain location code), it would be silently treated as an ordinary 1-column CFA instead of being excluded -- flagged here rather than guessed around, since the user's own example used the literal label "CWH".
- A normal CFA group that happens to carry only a `Closing` column (no `Transit`) in some future file is NOT treated as an error -- it is accepted as a Closing-only group like CWH/Ahmedabad, with `Transit Stock` forced to 0. The task named exactly two exceptions to "every CFA has Closing + Transit"; this parser doesn't hardcode a check that only CWH/Ahmedabad are allowed to be Closing-only, since doing so would require a hardcoded branch name list this codebase has otherwise avoided everywhere else in Inventory parsing (branches are always read from the file, never hardcoded).
- `Item Code` is always `None` for rows produced by this format, since it carries no code/alias column at all (only `Product`). This matches the existing pivoted format's precedent for its own missing `Item Group` column -- downstream, `item_code` is nullable and display-only, never part of any matching key.

### Confirmation

- ✓ **CWH never appears inside Replenishment, Thresholds, the Replenishment Export, or Automated Replenishment Emails** -- verified by direct query: zero `InventoryReplenishment`/`InventoryThreshold` rows ever carry the synthesized CWH branch label, because `evaluate_replenishment()`'s pre-existing `is_ahmedabad_cwh_branch` exclusion (unchanged) drops those rows before any threshold lookup or storage happens. It exists only inside `CwhStock`, read exclusively by `app/cwh_service.py`'s own module.
- ✓ **Ahmedabad uses Closing only, behaves like a normal CFA** -- verified: `transit_stock=0`, `effective_available_stock == closing_stock`, and it participates in threshold matching/replenishment exactly like Ambala or Bangalore.
- ✓ **Every other CFA uses Closing + Transit, `Effective Available Stock = Closing + Transit`, unchanged** -- verified against real computed values from `evaluate_replenishment()`, the exact same line of code as before this milestone.
- ✓ **Downstream modules required no business logic changes** -- `app/replenishment_service.py`, `app/cwh_service.py`, `app/threshold_service.py`, the Export Framework, and the Automated Email System are byte-for-byte unmodified; confirmed via `git`-free direct inspection (no version control in this project) that only `app/excel_validation.py` changed, and via the end-to-end test that every downstream consumer produced identical, correct results from the new format's translated data.
- ✓ **CWH is completely isolated from CFA workflows** -- it is now reachable only via the synthesized label that the pre-existing exclusion mechanism already filters out of every CFA-facing calculation, and is visible only inside the dedicated CWH module (`get_cwh_overview()`), exactly as required.
