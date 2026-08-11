"""A full-page loading overlay: a large progress bar, a percentage
readout, and one status message underneath -- shown immediately while a
background thread does real work, so the window never looks frozen.

Placed (not packed/gridded) on top of a page's own content, so it can
cover an already-built page without disturbing its layout. Progress
updates are visually smoothed (animated from the current value to the
new target) rather than snapping instantly -- this is what makes the
handful of discrete phase checkpoints (Initializing/Reading/Processing/
Validating/Finalizing) read as continuous movement instead of jumps.
"""

import customtkinter as ctk

from ui.components import Card
from ui.theme import Color, Font, Spacing

_ANIMATION_STEPS = 16
_ANIMATION_STEP_MS = 16  # ~16ms steps -> ~250ms total per transition


class LoadingOverlay(ctk.CTkFrame):
    """`overlay = LoadingOverlay(page); overlay.show(); overlay.update_progress(35, "Processing data..."); overlay.hide()`"""

    def __init__(self, master) -> None:
        super().__init__(master, fg_color=Color.SURFACE)
        self._displayed_percent = 0.0
        self._animation_job = None

        panel = Card(self, width=360)
        panel.place(relx=0.5, rely=0.5, anchor="center")

        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.pack(padx=Spacing.XL, pady=Spacing.XL)

        self.bar = ctk.CTkProgressBar(
            body, width=300, height=18, progress_color=Color.PRIMARY, corner_radius=9
        )
        self.bar.set(0)
        self.bar.pack(pady=(0, Spacing.MD))

        self.percent_label = ctk.CTkLabel(body, text="0%", font=Font.H1, text_color=Color.TEXT_PRIMARY)
        self.percent_label.pack()

        self.message_label = ctk.CTkLabel(
            body, text="Initializing...", font=Font.BODY, text_color=Color.TEXT_SECONDARY
        )
        self.message_label.pack(pady=(Spacing.XS, 0))

    def show(self) -> None:
        self._displayed_percent = 0.0
        self.bar.set(0)
        self.percent_label.configure(text="0%")
        self.message_label.configure(text="Initializing...")
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()

    def hide(self) -> None:
        if self._animation_job is not None:
            self.after_cancel(self._animation_job)
            self._animation_job = None
        self.place_forget()

    def update_progress(self, percent: float, message: str) -> None:
        if not self.winfo_exists():
            return
        self.message_label.configure(text=message)
        self._animate_to(max(0.0, min(100.0, percent)))

    def _animate_to(self, target_percent: float) -> None:
        if self._animation_job is not None:
            self.after_cancel(self._animation_job)
            self._animation_job = None

        start_percent = self._displayed_percent
        delta = target_percent - start_percent
        if delta == 0:
            return

        def step(i: int) -> None:
            if not self.winfo_exists():
                return
            fraction = i / _ANIMATION_STEPS
            value = start_percent + delta * fraction
            self._displayed_percent = value
            self.bar.set(value / 100)
            self.percent_label.configure(text=f"{int(round(value))}%")
            if i < _ANIMATION_STEPS:
                self._animation_job = self.after(_ANIMATION_STEP_MS, lambda: step(i + 1))
            else:
                self._animation_job = None

        step(1)
