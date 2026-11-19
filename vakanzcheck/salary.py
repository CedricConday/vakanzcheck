"""Put an offered salary and the salaries stated in job ads on the same monthly scale.

Two monthly figures are kept apart on purpose:

- `total`: base pay plus one-off payments (13th salary, holiday and Christmas
  money) spread over twelve months. The Entgeltatlas median includes one-off
  payments, so this is the figure compared against it.
- `base`: the regular monthly pay alone. Job ads state regular pay, so this is the
  figure compared against them.

Hourly pay becomes monthly with the BA's own day basis: hours per week times
365.25 / 7 / 12 weeks per month (about 4.35).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

WEEKS_PER_MONTH = 365.25 / 7 / 12


@dataclass(frozen=True)
class Offer:
    amount: float
    per: str  # "month", "hour" or "year"
    hours: float = 40.0  # per week; used for hourly pay
    payments: float = 12.0  # monthly salaries per year, for monthly or hourly pay
    extra: float = 0.0  # further one-off payments per year, €

    def __post_init__(self):
        if self.per not in ("month", "hour", "year"):
            raise ValueError(f"per must be month, hour or year, not {self.per!r}")
        if self.amount <= 0 or self.hours <= 0 or self.payments < 12 or self.extra < 0:
            raise ValueError("amount and hours must be positive, payments at least 12, extra >= 0")

    @property
    def base(self) -> float:
        if self.per == "month":
            return self.amount
        if self.per == "hour":
            return self.amount * self.hours * WEEKS_PER_MONTH
        return self.amount / 12

    @property
    def total(self) -> float:
        if self.per == "year":
            return self.amount / 12 + self.extra / 12
        return self.base * self.payments / 12 + self.extra / 12


# Plausible ranges per unit; values outside are typos or a different unit and are dropped.
PLAUSIBLE = {"STUNDENLOHN": (10, 100), "MONATSGEHALT": (1000, 15000),
             "JAHRESGEHALT": (15000, 200000)}


def ad_monthly(item: dict, hours: float) -> float | None:
    """The regular monthly pay a Jobsuche posting states, or None if it states none.

    A range counts as its midpoint. Hourly pay uses the vacancy's own weekly hours,
    so ads and offer are compared at the same working time.
    """
    unit = item.get("verguetungsangabe")
    if unit not in PLAUSIBLE:
        return None
    lo, hi = item.get("gehaltsspanneVon"), item.get("gehaltsspanneBis")
    if item.get("festgehalt"):
        value = float(item["festgehalt"])
    elif lo and hi and hi >= lo:
        value = (float(lo) + float(hi)) / 2
    elif lo or hi:
        value = float(lo or hi)
    else:
        return None
    low, high = PLAUSIBLE[unit]
    if not low <= value <= high:
        return None
    if unit == "STUNDENLOHN":
        return value * hours * WEEKS_PER_MONTH
    if unit == "JAHRESGEHALT":
        return value / 12
    return value


def quartiles(values: list[float]) -> dict | None:
    if len(values) < 4:
        return None
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"n": len(values), "q25": q1, "median": q2, "q75": q3}
