"""Reusable, sortable user list table.

Deliberately "dumb": ui/user_management_page.py owns all state (the full
dataset, current search/sort/page) -- this component only ever renders
whatever rows it's given and reports header/button clicks back via
callbacks. Built from CTk widgets rather than the app's existing
ttk.Treeview helper (ui/components.py's styled_treeview) because Treeview
cells can't embed real widgets, and this table needs a real Edit and a
real Enable/Disable button in every row.

"Assigned Role" (the old shared-role column) is now "Modules" -- a
per-user summary built from UserRecord.is_super_admin/modules (module
titles looked up via app.module_registry, not raw keys, since this is a
display column) rather than a single shared name.
"""

import customtkinter as ctk

from app import module_registry
from app.user_management_service import UserRecord
from ui.components import EmptyState, SecondaryButton, StatusBadge
from ui.theme import Color, Font, Spacing

# (data key, header label, relative column weight) -- "modules" is a
# synthetic column (see _modules_summary), not a plain UserRecord field,
# same as every other column here that reads straight off the record.
_COLUMNS = [
    ("full_name", "Full Name", 3),
    ("email", "Email", 4),
    ("modules", "Modules", 3),
    ("active", "Status", 1),
    ("created_at", "Created", 2),
]
_ACTIONS_COLUMN = len(_COLUMNS)


def _modules_summary(user: UserRecord) -> str:
    if user.is_super_admin:
        return "All Modules (Super Admin)"
    if not user.modules:
        return "None"
    titles = [module.title for key in user.modules if (module := module_registry.get_by_key(key)) is not None]
    return ", ".join(titles) if titles else "None"


class UserTable(ctk.CTkFrame):
    def __init__(self, master, on_sort, on_edit, on_toggle_active) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_sort = on_sort
        self._on_edit = on_edit
        self._on_toggle_active = on_toggle_active
        self._header_buttons: dict[str, tuple[ctk.CTkButton, str]] = {}

        self._build_header()
        self._body = ctk.CTkScrollableFrame(self, fg_color=Color.CARD)
        self._body.pack(fill="both", expand=True, pady=(Spacing.XS, 0))
        for col, (_key, _label, weight) in enumerate(_COLUMNS):
            self._body.grid_columnconfigure(col, weight=weight)
        self._body.grid_columnconfigure(_ACTIONS_COLUMN, weight=2)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=Color.SURFACE, corner_radius=0)
        header.pack(fill="x")
        for col, (key, label, weight) in enumerate(_COLUMNS):
            header.grid_columnconfigure(col, weight=weight)
            button = ctk.CTkButton(
                header,
                text=label,
                font=Font.SMALL_BOLD,
                fg_color="transparent",
                hover_color=Color.BORDER,
                text_color=Color.TEXT_SECONDARY,
                anchor="w",
                command=lambda k=key: self._on_sort(k),
            )
            button.grid(row=0, column=col, sticky="ew", padx=Spacing.SM, pady=Spacing.SM)
            self._header_buttons[key] = (button, label)

        header.grid_columnconfigure(_ACTIONS_COLUMN, weight=2)
        ctk.CTkLabel(
            header, text="Actions", font=Font.SMALL_BOLD, text_color=Color.TEXT_SECONDARY, anchor="w"
        ).grid(row=0, column=_ACTIONS_COLUMN, sticky="ew", padx=Spacing.SM, pady=Spacing.SM)

    def set_sort_indicator(self, column: str, ascending: bool) -> None:
        for key, (button, label) in self._header_buttons.items():
            if key == column:
                button.configure(text=f"{label} {'▲' if ascending else '▼'}")
            else:
                button.configure(text=label)

    def render_rows(self, users: list[UserRecord], current_user_id: str = None) -> None:
        for widget in self._body.winfo_children():
            widget.destroy()

        for row_index, user in enumerate(users):
            row_bg = Color.CARD if row_index % 2 == 0 else Color.SURFACE

            ctk.CTkLabel(
                self._body, text=user.full_name or "—", font=Font.BODY, text_color=Color.TEXT_PRIMARY,
                anchor="w", fg_color=row_bg,
            ).grid(row=row_index, column=0, sticky="ew", padx=Spacing.SM, pady=2)

            ctk.CTkLabel(
                self._body, text=user.email, font=Font.BODY, text_color=Color.TEXT_PRIMARY,
                anchor="w", fg_color=row_bg,
            ).grid(row=row_index, column=1, sticky="ew", padx=Spacing.SM, pady=2)

            ctk.CTkLabel(
                self._body, text=_modules_summary(user), font=Font.BODY, text_color=Color.TEXT_SECONDARY,
                anchor="w", fg_color=row_bg, wraplength=220, justify="left",
            ).grid(row=row_index, column=2, sticky="ew", padx=Spacing.SM, pady=2)

            status_cell = ctk.CTkFrame(self._body, fg_color=row_bg)
            status_cell.grid(row=row_index, column=3, sticky="ew", padx=Spacing.SM, pady=2)
            StatusBadge(
                status_cell, text="Active" if user.active else "Inactive",
                kind="success" if user.active else "neutral",
            ).pack(anchor="w")

            created_text = user.created_at.strftime("%b %d, %Y") if user.created_at else "—"
            ctk.CTkLabel(
                self._body, text=created_text, font=Font.BODY, text_color=Color.TEXT_SECONDARY,
                anchor="w", fg_color=row_bg,
            ).grid(row=row_index, column=4, sticky="ew", padx=Spacing.SM, pady=2)

            actions_cell = ctk.CTkFrame(self._body, fg_color=row_bg)
            actions_cell.grid(row=row_index, column=_ACTIONS_COLUMN, sticky="ew", padx=Spacing.SM, pady=2)
            SecondaryButton(
                actions_cell, text="Edit", width=56, height=28, font=Font.SMALL,
                command=lambda u=user: self._on_edit(u),
            ).pack(side="left", padx=(0, Spacing.XS))

            is_self = current_user_id is not None and user.id == current_user_id
            toggle_button = SecondaryButton(
                actions_cell, text="Disable" if user.active else "Enable", width=68, height=28, font=Font.SMALL,
                command=lambda u=user: self._on_toggle_active(u),
            )
            if is_self and user.active:
                # Mirrors the RLS policy in migration 0005: an admin can't
                # disable their own account. Disabling the button here is
                # just the faster/clearer UX -- the database blocks it
                # either way even if this were somehow bypassed.
                toggle_button.configure(state="disabled")
            toggle_button.pack(side="left")

    def render_empty(self, message: str) -> None:
        for widget in self._body.winfo_children():
            widget.destroy()
        EmptyState(self._body, message).grid(
            row=0, column=0, columnspan=_ACTIONS_COLUMN + 1, sticky="ew", pady=Spacing.XL
        )
