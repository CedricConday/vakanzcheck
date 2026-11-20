"""Put an offered salary and the salaries stated in job ads on comparable scales.

The offer has two monthly figures, kept apart on purpose:

- `total`: base pay plus one-off payments (13th salary, holiday and Christmas
  money) spread over twelve months. The Entgeltatlas median includes one-off
  payments, so this is the figure compared against it.
- `base`: the regular monthly pay alone.

Job ads state pay in two units in the Jobsuche v6 results: STUNDENLOHN and
JAHRESGEHALT (MONATSGEHALT is handled but was not seen). They are not on the
same footing. An hourly wage is regular pay and is compared with `base`. An
annual salary usually includes the 13th salary and holiday money, so annual
figures / 12 are compared with `total`. Mixing them on one monthly scale made
annual-salary ads (none flagged Zeitarbeit in the demo runs) look better paid
than hourly-wage ads (mostly flagged Zeitarbeit) by about a 13th salary.

Hourly pay becomes monthly with the BA's own day basis: hours per week times
365.25 / 7 / 12 weeks per month (about 4.35).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

WEEKS_PER_MONTH = 365.25 / 7 / 12


# Bounds on an offer beyond the per-unit salary ranges (PLAUSIBLE, below): the
# Arbeitszeitgesetz allows 48 hours a week on average, 60 at most; more than 14
# salaries or more than 20.000 € in further one-off payments is a typo.
MAX_HOURS = 60
MAX_PAYMENTS = 14
MAX_EXTRA = 20000
UNIT_FIELD = {"hour": "STUNDENLOHN", "month": "MONATSGEHALT", "year": "JAHRESGEHALT"}


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
        for name in ("amount", "hours", "payments", "extra"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number, not {getattr(self, name)}")
        low, high = PLAUSIBLE[UNIT_FIELD[self.per]]
        if not low <= self.amount <= high:
            raise ValueError(f"salary {self.amount:g} per {self.per} is outside the plausible "
                             f"range {low:g} to {high:g}; check the amount and --per")
        if not 0 < self.hours <= MAX_HOURS:
            raise ValueError(f"hours per week must be above 0 and at most {MAX_HOURS}")
        if not 12 <= self.payments <= MAX_PAYMENTS:
            raise ValueError(f"payments at least 12 and at most {MAX_PAYMENTS} salaries a year")
        if not 0 <= self.extra <= MAX_EXTRA:
            raise ValueError(f"extra must be between 0 and {MAX_EXTRA} € a year")
        if self.per == "year" and self.payments != 12:
            raise ValueError("payments apply to monthly or hourly pay; an annual salary already "
                             "includes a 13th salary, so leave payments at 12")

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


@dataclass(frozen=True)
class AdPay:
    unit: str  # "hour", "month" or "year", as the ad states it
    value: float  # in that unit; a range counts as its midpoint
    monthly: float  # hourly at the vacancy's weekly hours, annual / 12
    scale: str  # "base" or "total": which offer figure this is compared with

    def above(self, offer: Offer) -> bool:
        """True if the ad pays more than the offer, on the matching scale."""
        return self.monthly > (offer.base if self.scale == "base" else offer.total)


def ad_pay(item: dict, hours: float) -> AdPay | None:
    """The pay a Jobsuche posting states, or None if it states none or an implausible value.

    Hourly pay uses the vacancy's own weekly hours, so ad and offer are compared at
    the same working time.
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
        return AdPay("hour", value, value * hours * WEEKS_PER_MONTH, "base")
    if unit == "JAHRESGEHALT":
        return AdPay("year", value, value / 12, "total")
    return AdPay("month", value, value, "base")
