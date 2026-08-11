"""User Management dashboard (Version 2.0, Milestones 7-8).

Fetching, creating, editing, and enabling/disabling users are all real --
see app/user_management_service.py. Every write operation runs on a
background thread (the dialog stays open and disabled with "Saving..."
until it completes), and a successful operation always triggers a full
`_load_data()` refresh afterward -- Supabase is the single source of
truth, so the table always reflects what's actually there rather than a
locally-patched guess.

Access to this screen at all is already gated centrally in
ui/main_window.py's show_screen() (requires user_management=true) -- this
page does not re-implement that check; it assumes it's only ever shown to
an authorized user, the same assumption every other top-level module
(Path Validator, Inventory Monitoring, Payment Analytics) already makes.

Search/sort/pagination are all plain, client-side operations over the full
fetched list -- more than enough for a user list of realistic size, and the
data flow (page owns state, UserTable just renders what it's given) is
already the shape a future server-side paginated/searched version would
need, so growing into that later doesn't require restructuring this page.
"""

import threading

import customtkinter as ctk

from app import rbac_state, user_management_service
from ui.components import PrimaryButton, SecondaryButton, render_error_banner, render_success_banner
from ui.loading_overlay import LoadingOverlay
from ui.theme import Color, Font, Radius, Spacing
from ui.user_dialogs import ConfirmDialog, UserFormDialog
from ui.user_table import UserTable

_PAGE_SIZE = 15
_STATUS_MESSAGE_MS = 4000


class UserManagementPage(ctk.CTkFrame):
    def __init__(self, master, on_home) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._on_home = on_home

        self._all_users: list = []
        self._sort_column = "full_name"
        self._sort_ascending = True
        self._search_query = ""
        self._current_page = 1
        self._status_clear_job = None

        self._build_ui()

    # --- Layout --------------------------------------------------------

    def _build_ui(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=Spacing.XL, pady=Spacing.XL)

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill="x", pady=(0, Spacing.MD))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left")
        SecondaryButton(title_box, text="← Back to Home", command=self._on_home, width=140).pack(
            anchor="w", pady=(0, Spacing.SM)
        )
        ctk.CTkLabel(
            title_box, text="User Management", font=Font.H1, text_color=Color.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")

        PrimaryButton(header, text="+ Add User", command=self._open_add_dialog, width=140).pack(
            side="right", anchor="s"
        )

        self._status_container = ctk.CTkFrame(container, fg_color="transparent")

        toolbar = ctk.CTkFrame(container, fg_color="transparent")
        toolbar.pack(fill="x", pady=(0, Spacing.MD))
        self._search_entry = ctk.CTkEntry(
            toolbar, placeholder_text="Search by name or email...", height=36, corner_radius=Radius.SM, width=320
        )
        self._search_entry.pack(side="left")
        self._search_entry.bind("<KeyRelease>", lambda _event: self._on_search_changed())

        self._content = ctk.CTkFrame(container, fg_color="transparent")
        self._content.pack(fill="both", expand=True)

        self._loading_overlay = LoadingOverlay(self._content)

        self._error_container = ctk.CTkFrame(self._content, fg_color="transparent")

        self._table = UserTable(
            self._content,
            on_sort=self._on_sort,
            on_edit=self._open_edit_dialog,
            on_toggle_active=self._open_toggle_dialog,
        )
        self._table.pack(fill="both", expand=True)

        pagination_row = ctk.CTkFrame(container, fg_color="transparent")
        pagination_row.pack(fill="x", pady=(Spacing.SM, 0))
        self._prev_button = SecondaryButton(
            pagination_row, text="← Previous", width=110, command=self._go_previous_page
        )
        self._prev_button.pack(side="left")
        self._page_label = ctk.CTkLabel(pagination_row, text="", font=Font.SMALL, text_color=Color.TEXT_SECONDARY)
        self._page_label.pack(side="left", padx=Spacing.MD)
        self._next_button = SecondaryButton(pagination_row, text="Next →", width=110, command=self._go_next_page)
        self._next_button.pack(side="left")

    # --- MainWindow screen-registry hook ------------------------------------

    def on_show(self) -> None:
        """Called by MainWindow.show_screen() every time this screen
        becomes active -- reloads fresh data each time rather than reusing
        a stale in-memory list from a previous visit."""
        self._load_data()

    def _current_user_id(self):
        profile = rbac_state.current_profile()
        return profile.id if profile else None

    # --- Data loading --------------------------------------------------

    def _load_data(self) -> None:
        self._clear_error()
        self._table.pack_forget()
        self._loading_overlay.show()
        self._loading_overlay.update_progress(50, "Loading users...")

        def worker() -> None:
            users_result = user_management_service.list_users()
            self.after(0, self._on_data_loaded, users_result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_data_loaded(self, users_result) -> None:
        if not self.winfo_exists():
            return
        self._loading_overlay.hide()

        if not users_result.success:
            self._show_error(users_result.error_message or "Could not load users.")
            return

        self._all_users = users_result.users or []

        self._table.pack(fill="both", expand=True)
        self._current_page = 1
        self._render_table()

    def _clear_error(self) -> None:
        for widget in self._error_container.winfo_children():
            widget.destroy()
        self._error_container.pack_forget()

    def _show_error(self, message: str) -> None:
        self._table.pack_forget()
        self._clear_error()
        self._error_container.pack(fill="both", expand=True)
        render_error_banner(self._error_container, "Could not load users", [message])

    def _show_status(self, message: str, kind: str = "success") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        self._status_container.pack(fill="x", pady=(0, Spacing.MD), before=self._content)
        if kind == "success":
            render_success_banner(self._status_container, message)
        else:
            render_error_banner(self._status_container, "Note", [message])

        if self._status_clear_job is not None:
            self.after_cancel(self._status_clear_job)
        self._status_clear_job = self.after(_STATUS_MESSAGE_MS, self._clear_status)

    def _clear_status(self) -> None:
        self._status_clear_job = None
        for widget in self._status_container.winfo_children():
            widget.destroy()
        self._status_container.pack_forget()

    # --- Search / sort / pagination (client-side over the fetched list) ---

    def _filtered_sorted_users(self) -> list:
        users = self._all_users
        if self._search_query:
            query = self._search_query.lower()
            users = [
                user for user in users
                if query in (user.full_name or "").lower() or query in (user.email or "").lower()
            ]

        def sort_key(user):
            value = getattr(user, self._sort_column)
            if value is None:
                return ""
            return value.lower() if isinstance(value, str) else value

        return sorted(users, key=sort_key, reverse=not self._sort_ascending)

    def _render_table(self) -> None:
        filtered = self._filtered_sorted_users()
        total_pages = max(1, (len(filtered) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self._current_page = min(self._current_page, total_pages)
        start = (self._current_page - 1) * _PAGE_SIZE
        page_users = filtered[start:start + _PAGE_SIZE]

        self._table.set_sort_indicator(self._sort_column, self._sort_ascending)
        if page_users:
            self._table.render_rows(page_users, current_user_id=self._current_user_id())
        else:
            self._table.render_empty("No users match your search." if self._search_query else "No users found.")

        count_label = f"{len(filtered)} user{'s' if len(filtered) != 1 else ''}"
        self._page_label.configure(text=f"Page {self._current_page} of {total_pages} ({count_label})")
        self._prev_button.configure(state="normal" if self._current_page > 1 else "disabled")
        self._next_button.configure(state="normal" if self._current_page < total_pages else "disabled")

    def _on_search_changed(self) -> None:
        self._search_query = self._search_entry.get().strip()
        self._current_page = 1
        self._render_table()

    def _on_sort(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._render_table()

    def _go_previous_page(self) -> None:
        if self._current_page > 1:
            self._current_page -= 1
            self._render_table()

    def _go_next_page(self) -> None:
        self._current_page += 1
        self._render_table()

    # --- Add / Edit dialogs (real, backed by app/user_management_service.py) --

    def _open_add_dialog(self) -> None:
        # `dialog` is captured by reference in the lambda (standard Python
        # closure late-binding), so this works even though `dialog` isn't
        # bound until UserFormDialog(...) returns -- the lambda body only
        # runs later, when Save is clicked, by which point it's bound.
        dialog = UserFormDialog(
            self.winfo_toplevel(),
            on_submit=lambda data: self._handle_add_submit(dialog, data),
        )

    def _open_edit_dialog(self, user) -> None:
        is_self = user.id == self._current_user_id()
        dialog = UserFormDialog(
            self.winfo_toplevel(), user=user, is_self=is_self,
            on_submit=lambda data: self._handle_edit_submit(dialog, data),
        )

    def _open_toggle_dialog(self, user) -> None:
        action = "disable" if user.active else "enable"
        dialog = ConfirmDialog(
            self.winfo_toplevel(),
            title=f"{action.capitalize()} User",
            message=f"Are you sure you want to {action} {user.full_name or user.email}?",
            confirm_text=action.capitalize(),
            on_confirm=lambda: self._handle_toggle_confirm(dialog, user),
        )

    def _handle_add_submit(self, dialog, data: dict) -> None:
        def worker() -> None:
            result = user_management_service.create_user(
                full_name=data["full_name"],
                email=data["email"],
                password=data["password"],
                is_super_admin=data["is_super_admin"],
                module_keys=data["module_keys"],
                active=data["active"],
            )
            self.after(0, self._on_add_complete, dialog, data, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_add_complete(self, dialog, data: dict, result) -> None:
        if not self.winfo_exists():
            return
        if dialog.winfo_exists():
            dialog.finish_saving(result.success, result.error_message)
        if result.success:
            message = f"User {data['email']} created successfully."
            if result.needs_email_confirmation:
                message += " They must confirm their email before they can log in."
            self._show_status(message, kind="success")
            self._load_data()

    def _handle_edit_submit(self, dialog, data: dict) -> None:
        def worker() -> None:
            result = user_management_service.update_user(
                user_id=data["id"],
                full_name=data["full_name"],
                is_super_admin=data["is_super_admin"],
                module_keys=data["module_keys"],
                active=data["active"],
            )
            self.after(0, self._on_edit_complete, dialog, data, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_edit_complete(self, dialog, data: dict, result) -> None:
        if not self.winfo_exists():
            return
        if dialog.winfo_exists():
            dialog.finish_saving(result.success, result.error_message)
        if result.success:
            self._show_status(f"{data['full_name']} updated successfully.", kind="success")
            self._load_data()

    def _handle_toggle_confirm(self, dialog, user) -> None:
        def worker() -> None:
            result = user_management_service.set_user_active(user.id, not user.active)
            self.after(0, self._on_toggle_complete, dialog, user, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_toggle_complete(self, dialog, user, result) -> None:
        if not self.winfo_exists():
            return
        if dialog.winfo_exists():
            dialog.finish(result.success, result.error_message)
        if result.success:
            new_state = "enabled" if not user.active else "disabled"
            self._show_status(f"{user.full_name or user.email} {new_state}.", kind="success")
            self._load_data()
