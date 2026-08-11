"""First-run Setup Wizard: a modal shown once, the first time the
application ever starts (see app.app_state_service.is_setup_completed and
ui/main_window.py). Walks the user through connecting the three
Organization Data workbooks and saving email settings — both steps just
call the same service functions the regular Organization Data and Settings
pages already use, so there's no parallel upload/parsing logic to maintain.

Either step can be skipped; skipping only means that setting stays
unconfigured until the user visits the regular page later, exactly like a
never-configured install behaves today. Finishing (even with everything
skipped) marks setup complete so the wizard never appears again unless the
user chooses "Reset Configuration" on the About page.
"""

from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

from app.app_state_service import set_setup_completed
from app.email_settings_service import get_settings, save_settings
from app.hierarchy_parser import refresh_hierarchy
from app.workbook_connections import WORKBOOK_NAMES, get_connections, get_status, set_connection
from ui.components import Card, PrimaryButton, SecondaryButton, StatusBadge
from ui.theme import LOGO_PNG, Color, Font, Spacing

STATUS_BADGE_KIND = {"Connected": "success", "File Not Found": "error", "Not Configured": "neutral"}


class SetupWizard(ctk.CTkToplevel):
    """Modal — grabs focus and blocks interaction with the main window
    until finished, since it's meant to run before the app is used at all."""

    def __init__(self, master, on_finished) -> None:
        super().__init__(master)
        self._on_finished = on_finished

        self.title("Saffron Automation — Setup")
        # Fit the wizard to the screen so it can never open taller than the
        # display on a smaller or higher-DPI-scaled machine — which would push
        # the Skip/Continue/Finish buttons off-screen and block a brand-new
        # user from completing (or dismissing) first-run setup on a fresh
        # install. Sized in CustomTkinter's logical (pre-DPI-scaling) units,
        # resizable, with a sensible minimum. See MainWindow._apply_responsive_
        # geometry for the same technique and the full rationale.
        pref_w, pref_h = 620, 520
        try:
            screen_w = int(self._reverse_window_scaling(self.winfo_screenwidth()))
            screen_h = int(self._reverse_window_scaling(self.winfo_screenheight()))
            win_w = min(pref_w, int(screen_w * 0.9))
            win_h = min(pref_h, int(screen_h * 0.9))
            self.geometry(f"{win_w}x{win_h}")
            self.minsize(min(520, win_w), min(440, win_h))
        except Exception:
            self.geometry(f"{pref_w}x{pref_h}")
            self.minsize(520, 440)
        self.configure(fg_color=Color.SURFACE)
        self.resizable(True, True)

        self.protocol("WM_DELETE_WINDOW", self._on_finish_clicked)  # closing the window = finish, not skip setup

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        self._path_labels: dict[str, ctk.CTkLabel] = {}
        self._status_badges: dict[str, StatusBadge] = {}

        self._show_welcome_step()

        # Modal: grab focus after the window is actually mapped, and center
        # it over the main window.
        self.transient(master)
        self.after(50, self._make_modal)

    def _make_modal(self) -> None:
        self.grab_set()
        self.focus_force()

    def _clear_content(self) -> None:
        for widget in self.content.winfo_children():
            widget.destroy()

    # --- Step 1: Welcome ----------------------------------------------------

    def _show_welcome_step(self) -> None:
        self._clear_content()

        if LOGO_PNG.exists():
            from PIL import Image

            logo_image = ctk.CTkImage(light_image=Image.open(LOGO_PNG), size=(64, 64))
            ctk.CTkLabel(self.content, image=logo_image, text="").pack(pady=(Spacing.LG, Spacing.MD))

        ctk.CTkLabel(
            self.content, text="Welcome to Saffron Automation", font=Font.H1, text_color=Color.TEXT_PRIMARY
        ).pack(pady=(0, Spacing.SM))
        ctk.CTkLabel(
            self.content,
            text=(
                "Let's get you set up. This will only take a minute — you can always "
                "change any of this later from the Organization Data or Settings pages."
            ),
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            wraplength=480,
            justify="center",
        ).pack(pady=(0, Spacing.LG))

        PrimaryButton(self.content, text="Get Started", command=self._show_organization_data_step).pack()

    # --- Step 2: Organization Data workbooks --------------------------------

    def _show_organization_data_step(self) -> None:
        self._clear_content()

        ctk.CTkLabel(
            self.content, text="Connect Organization Data", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            self.content,
            text="Connect each division's workbook so employees can be matched to their managers.",
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        card = Card(self.content)
        card.pack(fill="x", pady=(0, Spacing.MD))
        card_body = ctk.CTkFrame(card, fg_color="transparent")
        card_body.pack(fill="x", padx=Spacing.MD, pady=Spacing.MD)

        connections = get_connections()
        for name in WORKBOOK_NAMES:
            row = ctk.CTkFrame(card_body, fg_color="transparent")
            row.pack(fill="x", pady=4)

            ctk.CTkLabel(row, text=name, font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, width=90, anchor="w").pack(
                side="left"
            )

            status = get_status(connections.get(name))
            badge = StatusBadge(row, status, STATUS_BADGE_KIND[status])
            badge.pack(side="left", padx=(0, Spacing.SM))
            self._status_badges[name] = badge

            SecondaryButton(row, text="Browse…", command=lambda n=name: self._on_browse_clicked(n)).pack(side="right")

        self.status_label = ctk.CTkLabel(self.content, text="", font=Font.SMALL, text_color=Color.TEXT_MUTED, anchor="w")
        self.status_label.pack(anchor="w", pady=(0, Spacing.MD))

        nav_row = ctk.CTkFrame(self.content, fg_color="transparent")
        nav_row.pack(fill="x", side="bottom")
        SecondaryButton(nav_row, text="Skip for now", command=self._show_email_settings_step).pack(side="left")
        PrimaryButton(nav_row, text="Load & Continue", command=self._on_load_organization_data_clicked).pack(side="right")

    def _on_browse_clicked(self, workbook_name: str) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Select {workbook_name} Organization Data Workbook", filetypes=[("Excel files", "*.xlsx")]
        )
        if not file_path:
            return
        set_connection(workbook_name, file_path)
        self._status_badges[workbook_name].set_status("Connected", "success")

    def _on_load_organization_data_clicked(self) -> None:
        try:
            stats = refresh_hierarchy()
        except Exception as exc:
            logger.error(f"Setup Wizard: Organization Data refresh failed: {exc}")
            messagebox.showerror("Load Failed", f"Could not load Organization Data.\n\n{exc}")
            return

        if stats["workbooks_read"]:
            logger.info(f"Setup Wizard: loaded Organization Data from {', '.join(stats['workbooks_read'])}")
        self._show_email_settings_step()

    # --- Step 3: Email settings ----------------------------------------------

    def _show_email_settings_step(self) -> None:
        self._clear_content()

        ctk.CTkLabel(
            self.content, text="Email Settings", font=Font.H2, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            self.content,
            text="Used to send automatic manager notifications after each analysis run.",
            font=Font.BODY,
            text_color=Color.TEXT_SECONDARY,
            anchor="w",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        existing = get_settings()

        ctk.CTkLabel(self.content, text="Sender Gmail Address", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w").pack(
            anchor="w"
        )
        self.sender_entry = ctk.CTkEntry(self.content, placeholder_text="you@gmail.com")
        self.sender_entry.pack(fill="x", pady=(4, Spacing.SM))
        self.sender_entry.insert(0, existing["sender_email"])

        ctk.CTkLabel(self.content, text="Gmail App Password", font=Font.BODY_BOLD, text_color=Color.TEXT_PRIMARY, anchor="w").pack(
            anchor="w"
        )
        self.password_entry = ctk.CTkEntry(self.content, placeholder_text="16-character app password", show="*")
        self.password_entry.pack(fill="x", pady=(4, Spacing.SM))
        self.password_entry.insert(0, existing["app_password"])

        ctk.CTkLabel(
            self.content,
            text=(
                "Master Report recipients are configured later, on the Master Email Recipients page — "
                "add recipients there once setup is finished."
            ),
            font=Font.SMALL,
            text_color=Color.TEXT_MUTED,
            anchor="w",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, Spacing.MD))

        nav_row = ctk.CTkFrame(self.content, fg_color="transparent")
        nav_row.pack(fill="x", side="bottom")
        SecondaryButton(nav_row, text="Skip for now", command=self._on_finish_clicked).pack(side="left")
        PrimaryButton(nav_row, text="Save & Finish", command=self._on_save_and_finish_clicked).pack(side="right")

    def _on_save_and_finish_clicked(self) -> None:
        sender_email = self.sender_entry.get().strip()
        app_password = self.password_entry.get().strip()
        existing = get_settings()

        save_settings(sender_email, app_password, existing["automatic_sending_enabled"])
        self._on_finish_clicked()

    # --- Finish ---------------------------------------------------------------

    def _on_finish_clicked(self) -> None:
        set_setup_completed(True)
        self.grab_release()
        self.destroy()
        self._on_finished()
