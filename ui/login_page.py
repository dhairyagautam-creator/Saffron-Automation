"""Login screen for Saffron Automation Suite.

Wired to real Supabase Authentication via app/auth_service.py, and to
permission loading via app/rbac_service.py -- this page never talks to
Supabase or the profiles/user_module_permissions tables directly; it only
calls sign_in()/restore_session()/load_profile_and_permissions() and
reacts to the result each one returns. That keeps all auth and RBAC logic
isolated in those service modules.

`on_login_success()` (raising Home) only fires after BOTH steps succeed:
Supabase authentication, then loading the user's profile+permissions. A
user with no profile never reaches Home -- they stay on this screen with a
clear error, per the "prevent access" requirement.

Embedded as a screen in MainWindow's screen registry (see
ui/main_window.py), not a separate window -- it's the initial screen shown
via `show_screen("Login")`, and `on_login_success()` swaps to Home in the
same window, exactly like every other screen-to-screen transition already
in the app (Home <-> Path Validator / Inventory / Payment Analytics).
"""

import threading
from typing import Callable

import customtkinter as ctk
from PIL import Image

from app import auth_service, rbac_service
from ui.components import Card, PrimaryButton
from ui.theme import Color, Font, LOGO_PNG, Radius, Spacing

_DEV_LABEL = "Version 2.0 Development"


class LoginPage(ctk.CTkFrame):
    """Calls `on_login_success()` once sign-in (or, at startup, restoring a
    previously saved session) succeeds -- MainWindow supplies a callback
    that raises the Home screen, the same way it wires every other page's
    navigation callbacks."""

    def __init__(self, master, on_login_success: Callable[[], None]) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_login_success = on_login_success
        self._password_visible = False
        self._startup_session_checked = False

        self._build_ui()
        self._build_session_check_overlay()

    # --- Layout ------------------------------------------------------------

    def _build_ui(self) -> None:
        card = Card(self, width=420)
        card.place(relx=0.5, rely=0.5, anchor="center")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(padx=Spacing.XL, pady=Spacing.XL)

        self._build_logo(body)

        ctk.CTkLabel(
            body, text="Saffron Automation Suite", font=Font.H1, text_color=Color.TEXT_PRIMARY
        ).pack()
        ctk.CTkLabel(
            body, text="Enterprise Operations Platform", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        ).pack(pady=(2, Spacing.LG))

        # Error banner: built now, not packed until _show_error() is called.
        self._error_frame = ctk.CTkFrame(body, fg_color=Color.ERROR_SOFT, corner_radius=Radius.SM)
        self._error_label = ctk.CTkLabel(
            self._error_frame, text="", font=Font.SMALL, text_color=Color.ERROR,
            wraplength=320, justify="left", anchor="w",
        )
        self._error_label.pack(padx=Spacing.SM, pady=Spacing.SM, anchor="w")

        self._user_id_label = ctk.CTkLabel(
            body, text="User ID / Email", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        )
        self._user_id_label.pack(fill="x", pady=(0, 4))
        self.user_entry = ctk.CTkEntry(
            body, placeholder_text="you@company.com", height=36, corner_radius=Radius.SM, width=320
        )
        self.user_entry.pack(fill="x", pady=(0, Spacing.MD))

        ctk.CTkLabel(
            body, text="Password", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).pack(fill="x", pady=(0, 4))
        password_row = ctk.CTkFrame(body, fg_color="transparent")
        password_row.pack(fill="x", pady=(0, Spacing.LG))
        password_row.grid_columnconfigure(0, weight=1)

        self.password_entry = ctk.CTkEntry(
            password_row, placeholder_text="********", show="*", height=36, corner_radius=Radius.SM
        )
        self.password_entry.grid(row=0, column=0, sticky="ew")

        self.toggle_button = ctk.CTkButton(
            password_row, text="Show", width=56, height=36, corner_radius=Radius.SM,
            fg_color="transparent", hover_color=Color.SURFACE, text_color=Color.TEXT_SECONDARY,
            border_width=1, border_color=Color.BORDER, font=Font.SMALL_BOLD,
            command=self._toggle_password_visibility,
        )
        self.toggle_button.grid(row=0, column=1, padx=(Spacing.SM, 0))

        self.login_button = PrimaryButton(body, text="Login", command=self._handle_login_click)
        self.login_button.pack(fill="x")

        self.user_entry.bind("<Return>", lambda _event: self._handle_login_click())
        self.password_entry.bind("<Return>", lambda _event: self._handle_login_click())

        ctk.CTkLabel(
            self, text=_DEV_LABEL, font=Font.SMALL, text_color=Color.TEXT_MUTED
        ).place(relx=1.0, rely=1.0, anchor="se", x=-Spacing.MD, y=-Spacing.MD)

    def _build_logo(self, master) -> None:
        if LOGO_PNG.exists():
            logo_image = ctk.CTkImage(
                light_image=Image.open(LOGO_PNG), dark_image=Image.open(LOGO_PNG), size=(84, 84)
            )
            ctk.CTkLabel(master, image=logo_image, text="").pack(pady=(0, Spacing.MD))
        else:
            # Placeholder if no logo asset is present -- a plain brand-color
            # circle with an initial, so the layout still looks intentional.
            placeholder = ctk.CTkFrame(master, width=84, height=84, corner_radius=42, fg_color=Color.PRIMARY)
            placeholder.pack(pady=(0, Spacing.MD))
            placeholder.pack_propagate(False)
            ctk.CTkLabel(placeholder, text="S", font=Font.H1, text_color=Color.TEXT_ON_PRIMARY).pack(expand=True)

    def _build_session_check_overlay(self) -> None:
        """A full-page overlay shown while `restore_session()` and/or
        `load_profile_and_permissions()` are running -- an indeterminate bar,
        since there's no meaningful "percent done" for either (unlike
        ui/loading_overlay.py's upload-progress bar, which tracks real
        percentages). Reused for both steps via `_show_loading_overlay`'s
        message parameter, since both are "please wait" states."""
        self._session_overlay = ctk.CTkFrame(self, fg_color=Color.SURFACE)
        panel = Card(self._session_overlay, width=320)
        panel.place(relx=0.5, rely=0.5, anchor="center")

        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.pack(padx=Spacing.XL, pady=Spacing.XL)

        self._session_check_bar = ctk.CTkProgressBar(
            body, width=220, mode="indeterminate", progress_color=Color.PRIMARY
        )
        self._session_check_bar.pack(pady=(0, Spacing.MD))
        self._session_check_label = ctk.CTkLabel(
            body, text="", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        )
        self._session_check_label.pack()

    def _show_loading_overlay(self, message: str) -> None:
        self._session_check_label.configure(text=message)
        self._session_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._session_overlay.lift()
        self._session_check_bar.start()

    def _hide_loading_overlay(self) -> None:
        self._session_check_bar.stop()
        self._session_overlay.place_forget()

    # --- Password visibility -----------------------------------------------

    def _toggle_password_visibility(self) -> None:
        self._password_visible = not self._password_visible
        self.password_entry.configure(show="" if self._password_visible else "*")
        self.toggle_button.configure(text="Hide" if self._password_visible else "Show")

    # --- Error area ----------------------------------------------------------

    def _show_error(self, message: str) -> None:
        self._error_label.configure(text=message)
        self._error_frame.pack(fill="x", pady=(0, Spacing.MD), before=self._user_id_label)

    def _hide_error(self) -> None:
        self._error_frame.pack_forget()

    def _reset_form_state(self) -> None:
        """Re-enable the form after a failed login attempt or once the
        startup session check has finished."""
        self.login_button.configure(state="normal", text="Login")
        self.user_entry.configure(state="normal")
        self.password_entry.configure(state="normal")
        self.toggle_button.configure(state="normal")

    # --- Sign-in (real Supabase Auth, run off the UI thread) ---------------

    def _handle_login_click(self) -> None:
        email = self.user_entry.get().strip()
        password = self.password_entry.get()
        if not email or not password:
            self._show_error("Please enter your email and password.")
            return

        self._hide_error()
        self.login_button.configure(state="disabled", text="Signing in...")
        self.user_entry.configure(state="disabled")
        self.password_entry.configure(state="disabled")
        self.toggle_button.configure(state="disabled")

        def worker() -> None:
            result = auth_service.sign_in(email, password)
            self.after(0, self._on_sign_in_complete, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_sign_in_complete(self, result: auth_service.AuthResult) -> None:
        if not self.winfo_exists():
            return  # window closed while the request was in flight
        if not result.success:
            self._reset_form_state()
            self._show_error(result.error_message or "Sign-in failed. Please try again.")
            return
        self._begin_profile_load(result.user_id, result.user_email)

    # --- Startup session restoration ---------------------------------------

    def _check_existing_session(self) -> None:
        self._show_loading_overlay("Checking your saved session...")

        def worker() -> None:
            result = auth_service.restore_session()
            self.after(0, self._on_session_check_complete, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_session_check_complete(self, result: auth_service.AuthResult) -> None:
        if not self.winfo_exists():
            return
        if not result.success:
            self._hide_loading_overlay()
            if result.error_message:
                self._show_error(result.error_message)
            self.user_entry.focus_set()
            return
        # Session restored -- still need the profile+permissions before
        # granting access (a restored session with a broken profile must
        # not bypass the same check a fresh login would go through).
        self._begin_profile_load(result.user_id, result.user_email)

    # --- Permission loading (both entry points converge here) --------------

    def _begin_profile_load(self, user_id: str, user_email: str) -> None:
        self._show_loading_overlay("Loading your profile and permissions...")

        def worker() -> None:
            result = rbac_service.load_profile_and_permissions(user_id, user_email)
            self.after(0, self._on_profile_load_complete, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_profile_load_complete(self, result: rbac_service.RbacResult) -> None:
        if not self.winfo_exists():
            return
        self._hide_loading_overlay()
        if result.success:
            self._on_login_success()
        else:
            self._reset_form_state()
            self._show_error(result.error_message or "Could not load your account. Please try again.")

    # --- MainWindow screen-registry hook -----------------------------------

    def on_show(self) -> None:
        """Called by MainWindow.show_screen() each time this screen becomes
        active. The very first time (app startup), it checks for a saved
        session in the background before showing the form. Every later
        time (e.g. after a future logout), the check is skipped -- logging
        out already clears any saved session, so re-checking would just be
        a wasted round trip -- and the form is simply reset."""
        self._reset_form_state()
        self._hide_error()
        if not self._startup_session_checked:
            self._startup_session_checked = True
            self._check_existing_session()
        else:
            self.user_entry.focus_set()
