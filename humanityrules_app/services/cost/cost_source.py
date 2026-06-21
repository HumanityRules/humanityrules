"""The ``CostSource`` contract and the generic row it emits.

The generic cost layer (cost_refresh, model, view, chart, job) only ever sees ``DailyCostRow`` and the
source ``key`` — never a token count, a model id, or a dollar rate. That keeps adding a new source to a
single file plus one entry in ``cost_refresh.COST_SOURCES``.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from humanityrules_app.models import App


@dataclass
class DailyCostRow:
    """One source's cost for one ``(environment, date, subkey)``.

    ``subkey`` is the source's sub-dimension (Bedrock: normalized model id). ``details`` carries
    source-specific context (token counts, an ``estimate`` flag) and is stored verbatim in
    ``AppDailyCost.details``.
    """

    environment_id: UUID
    date: date
    subkey: str
    cost_usd: Decimal
    details: dict


class CostSource(Protocol):
    """A pluggable cost source. Implementations are synchronous (run inside the job worker thread)."""

    key: str

    def collect(self, app: App, start_date: date, end_date: date) -> list[DailyCostRow]:
        """Per-day cost rows for ``app`` over the inclusive UTC window ``[start_date, end_date]``."""
        ...

    def rolling_24h_usd(self, app: App) -> Decimal:
        """Total cost for ``app`` over exactly ``now-24h .. now`` (recomputed, not a sum of day bins)."""
        ...
