from __future__ import annotations

from collections.abc import Mapping

from rich.text import Text
from textual.widgets import Static


class SystemCard(Static):
    """A compact status line shared by dashboard infrastructure and services."""

    STATUS_COLORS = {
        "online": "#879965",
        "running": "#879965",
        "offline": "#bd6455",
        "stopped": "#d1a34a",
        "planned": "#77796c",
        "contacting": "#d1a34a",
    }

    def __init__(
        self,
        name: str,
        *,
        status: str = "contacting",
        metrics: Mapping[str, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(self.build_text(name, status, metrics), **kwargs)
        self.system_name = name

    @classmethod
    def build_text(
        cls,
        name: str,
        status: str,
        metrics: Mapping[str, str] | None = None,
    ) -> Text:
        normalized_status = status.lower()
        text = Text()
        text.append(name.upper(), style="bold")
        text.append("  ")
        text.append(
            normalized_status.upper(),
            style=cls.STATUS_COLORS.get(normalized_status, "#d0c9ad"),
        )
        for label, value in (metrics or {}).items():
            text.append(f"  //  {label.upper()} {value}")
        return text

    def update_system(
        self,
        status: str,
        metrics: Mapping[str, str] | None = None,
    ) -> None:
        self.update(self.build_text(self.system_name, status, metrics))

