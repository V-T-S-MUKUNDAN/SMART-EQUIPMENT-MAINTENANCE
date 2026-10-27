"""Filter objects that turn UI selections into bound SQL parameters (never string-interpolated values)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping

from .db import in_clause


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


@dataclass(frozen=True)
class AzureFilters:
    """Date range (inclusive of both days) + optional machine and component selections.

    ``machines=None`` / ``components=None`` mean "no filter"; an empty tuple means "match nothing".
    """
    start: str                                   # 'YYYY-MM-DD'
    end: str                                     # 'YYYY-MM-DD' (inclusive day)
    machines: tuple[int, ...] | None = None
    components: tuple[str, ...] | None = None

    @classmethod
    def build(cls, start, end, machines=None, components=None) -> "AzureFilters":
        return cls(_to_date(start).isoformat(), _to_date(end).isoformat(),
                   None if machines is None else tuple(sorted(int(m) for m in machines)),
                   None if components is None else tuple(sorted(str(c) for c in components)))

    @property
    def end_excl(self) -> str:
        return (_to_date(self.end) + timedelta(days=1)).isoformat()

    def render(self, sql: str) -> tuple[str, dict]:
        """Fill the {machine_filter}/{component_filter} tokens; return (sql, params)."""
        mclause, mparams = in_clause("machine_id", self.machines, "mf")
        cclause, cparams = in_clause("component", self.components, "cf")
        params: dict = {"start": self.start, "end_excl": self.end_excl, **mparams, **cparams}
        return sql.format(machine_filter=mclause, component_filter=cclause), params

    def is_default(self, min_date: str, max_date: str) -> bool:
        return (self.machines is None and self.components is None
                and self.start <= min_date[:10] and self.end >= max_date[:10])


@dataclass(frozen=True)
class AI4IFilters:
    """Row-level filters for the AI4I dataset. ``None`` = unrestricted."""
    types: tuple[str, ...] | None = None
    tool_wear: tuple[float, float] | None = None
    rpm: tuple[float, float] | None = None
    torque: tuple[float, float] | None = None
    air_temp: tuple[float, float] | None = None
    process_temp: tuple[float, float] | None = None

    _RANGES = {"tool_wear": "tool_wear_min", "rpm": "rotational_speed_rpm", "torque": "torque_nm",
               "air_temp": "air_temp_k", "process_temp": "process_temp_k"}

    def where(self) -> tuple[str, dict]:
        """Return (' AND ...' fragment, params) for use inside a WHERE 1 = 1 clause."""
        clause, params = in_clause("machine_type", self.types, "ty")
        for attr, col in self._RANGES.items():
            rng = getattr(self, attr)
            if rng is not None:
                clause += f" AND {col} BETWEEN :{attr}_lo AND :{attr}_hi "
                params[f"{attr}_lo"], params[f"{attr}_hi"] = float(rng[0]), float(rng[1])
        return clause, params

    def render(self, sql: str) -> tuple[str, dict]:
        clause, params = self.where()
        return sql.format(row_filter=clause), params

    def describe(self) -> str:
        parts = []
        if self.types is not None:
            parts.append("type in " + ",".join(self.types))
        for attr in self._RANGES:
            rng = getattr(self, attr)
            if rng is not None:
                parts.append(f"{attr} {rng[0]:g}-{rng[1]:g}")
        return "; ".join(parts) or "no filters"


def merge_params(*dicts: Mapping) -> dict:
    out: dict = {}
    for d in dicts:
        out.update(d)
    return out
