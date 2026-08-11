"""HierarchyTableSection: the KPI grid + searchable/exportable hierarchy
table originally built inline on ui/organization_data_page.py (Path
Validator's own Organization Data page) -- extracted into ONE reusable
component so any other page needing to display the same employee_hierarchy
data (e.g. ui/work_distribution_email_center_page.py's Hierarchy Workbooks
card) shows byte-for-byte identical output with zero duplicated table/KPI
logic, per explicit "do not create a second implementation" instruction.

Purely presentational and read-only: this component never calls
app.hierarchy_parser.refresh_hierarchy() itself -- each owning page keeps
its own Browse/Refresh workflow (file connections differ per page's own
context) and, after a successful refresh, hands this component the
resulting stats dict (see update_kpis()) and asks it to reload
(load_from_db()). This keeps hierarchy parsing/refresh logic exactly where
it already lived, in each page's own Refresh handler -- this module adds no
new business logic, only shared display.
"""

import pandas as pd
from sqlalchemy import inspect

from app.hierarchy_parser import HIERARCHY_TABLE
from app.table_export_service import default_export_filename, export_rows_with_ui
from database.connection import get_data_engine
from ui.components import Card, EmptyState, KPICard, PrimaryButton, styled_treeview
import customtkinter as ctk
from ui.icons import get_icon
from ui.theme import Color, Font, Spacing

COLUMNS = (
    "employee_code",
    "employee_name",
    "designation",
    "mobile",
    "email",
    "senior_name",
    "division",
    "source_sheet",
)
HEADINGS = {
    "employee_code": "Code",
    "employee_name": "Employee",
    "designation": "Designation",
    "mobile": "Mobile",
    "email": "Email",
    "senior_name": "Senior",
    "division": "Division",
    "source_sheet": "Sheet",
}
WIDTHS = {
    "employee_code": 80,
    "employee_name": 150,
    "designation": 90,
    "mobile": 100,
    "email": 190,
    "senior_name": 150,
    "division": 90,
    "source_sheet": 100,
}

KPI_SPECS = [
    ("Total Employees Imported", Color.PRIMARY),
    ("Total BM", Color.INFO),
    ("Total ABM", Color.INFO),
    ("Total RBM", Color.INFO),
    ("Vacant Ignored", Color.WARNING),
    ("Email Addresses Loaded", Color.SUCCESS),
    ("Hierarchy Relationships", Color.SUCCESS),
]


class HierarchyTableSection(ctk.CTkFrame):
    """KPI cards + search + Export + a styled_treeview table of the current
    employee_hierarchy data. The owning page is responsible for triggering
    app.hierarchy_parser.refresh_hierarchy() (each page's own Browse/Refresh
    workflow differs) and then calling update_kpis(stats) + load_from_db()
    with the result -- this component only renders."""

    def __init__(self, master, export_filename_prefix: str = "OrganizationData", **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._export_filename_prefix = export_filename_prefix
        self.kpi_cards: dict[str, KPICard] = {}
        self._all_rows: list[tuple] = []
        self._current_display_rows: list[dict] = []
        self._build_widgets()

    def _build_widgets(self) -> None:
        kpi_grid = ctk.CTkFrame(self, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, Spacing.LG))
        for i in range(4):
            kpi_grid.grid_columnconfigure(i, weight=1)
        for i, (label, accent) in enumerate(KPI_SPECS):
            row_idx, col_idx = divmod(i, 4)
            card = KPICard(kpi_grid, label=label, value="0", accent=accent)
            card.grid(
                row=row_idx, column=col_idx, sticky="ew",
                padx=(0 if col_idx == 0 else Spacing.SM, 0),
                pady=(0 if row_idx == 0 else Spacing.SM, 0),
            )
            self.kpi_cards[label] = card

        table_card = Card(self)
        table_card.pack(fill="both", expand=True)

        table_body = ctk.CTkFrame(table_card, fg_color="transparent")
        table_body.pack(fill="both", expand=True, padx=Spacing.LG, pady=Spacing.LG)

        search_row = ctk.CTkFrame(table_body, fg_color="transparent")
        search_row.pack(fill="x", pady=(0, Spacing.SM))
        ctk.CTkLabel(search_row, text="Search:", font=Font.BODY, text_color=Color.TEXT_SECONDARY).pack(
            side="left", padx=(0, Spacing.SM)
        )
        self.search_entry = ctk.CTkEntry(
            search_row, placeholder_text="Search by employee, designation, ABM, RBM, or email…"
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, Spacing.SM))
        self.search_entry.bind("<KeyRelease>", lambda event: self._apply_filter())

        self.export_button = PrimaryButton(
            search_row,
            text="Export",
            image=get_icon("download", size=16, color=Color.TEXT_ON_PRIMARY),
            command=self._on_export_clicked,
        )
        self.export_button.pack(side="right")

        self.table_container = ctk.CTkFrame(table_body, fg_color="transparent")
        self.table_container.pack(fill="both", expand=True)

    # --- Public API ------------------------------------------------------

    def update_kpis(self, stats: dict) -> None:
        """`stats` is exactly app.hierarchy_parser.refresh_hierarchy()'s own
        return dict -- never recomputed here."""
        self.kpi_cards["Total Employees Imported"].set_value(f"{stats['employees_loaded']:,}")
        self.kpi_cards["Total BM"].set_value(f"{stats['total_bm']:,}")
        self.kpi_cards["Total ABM"].set_value(f"{stats['total_abm']:,}")
        self.kpi_cards["Total RBM"].set_value(f"{stats['total_rbm']:,}")
        self.kpi_cards["Vacant Ignored"].set_value(f"{stats['vacant_ignored']:,}")
        self.kpi_cards["Email Addresses Loaded"].set_value(f"{stats['emails_loaded']:,}")
        self.kpi_cards["Hierarchy Relationships"].set_value(f"{stats['hierarchy_relationships']:,}")

    def load_from_db(self) -> None:
        """Reloads the table from the current employee_hierarchy table --
        call after a refresh, and once on page show, exactly like
        Organization Data's own on_show() always has."""
        if not inspect(get_data_engine()).has_table(HIERARCHY_TABLE):
            self._all_rows = []
        else:
            df = pd.read_sql_table(HIERARCHY_TABLE, con=get_data_engine())
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            df = df[list(COLUMNS)]
            # Defensive: pandas.read_sql_table() turns a SQL NULL in a text
            # column into float NaN, which would otherwise render as the
            # literal text "nan" in the table below.
            df = df.fillna("")
            self._all_rows = list(df.itertuples(index=False, name=None))

        self._apply_filter()

    # --- Internal ----------------------------------------------------------

    def _on_export_clicked(self) -> None:
        export_rows_with_ui(
            self,
            rows=self._current_display_rows,
            columns=COLUMNS,
            headings=HEADINGS,
            suggested_filename=default_export_filename(self._export_filename_prefix),
            sheet_title="Organization Data",
        )

    def _apply_filter(self) -> None:
        for widget in self.table_container.winfo_children():
            widget.destroy()
        self._current_display_rows = []

        query = self.search_entry.get().strip().lower()
        rows = self._all_rows
        if query:
            rows = [row for row in rows if query in " ".join(str(v) for v in row).lower()]

        if not rows:
            message = (
                "No Organization Data yet — connect a workbook and refresh."
                if not self._all_rows
                else "No employees match your search."
            )
            EmptyState(self.table_container, message).pack(fill="both", expand=True)
            return

        # `rows` are plain tuples ordered exactly per COLUMNS (see
        # load_from_db) -- zip them into dicts so the export framework's
        # row_style_fn/dict contract works the same way it does for every
        # other page, without changing how the Treeview itself is populated
        # below.
        self._current_display_rows = [dict(zip(COLUMNS, row)) for row in rows]

        tree = styled_treeview(self.table_container, COLUMNS, HEADINGS, WIDTHS, height=14)
        for row in rows:
            tree.insert("", "end", values=row)
        tree.pack(fill="both", expand=True)
