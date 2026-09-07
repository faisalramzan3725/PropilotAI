"""
Domain model and in-memory store for the onboarding copilot.

This module owns all state and all business rules. It is deliberately kept
free of any MCP / agent concerns so it can be unit-tested directly (see
eval/run.py) without spinning up a server or an agent.

Scope: the "slice" of the real 14-step onboarding named in the brief --
import, grouping (cluster + tags), landlord terms (commission scheme or
rent-to-rent, co-ownership, blocking), and setup costs. No P&L / payout
computation -- see PRIORITIES.md for why.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DomainError(ValueError):
    """Raised for any invalid operation; the MCP tool layer turns this into
    a tool error the agent sees and can relay to the operator, rather than
    letting bad input silently corrupt the account."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

CommissionBase = Literal["net_of_ota_fees", "gross_guest_total"]
SchemeType = Literal["percentage", "rent_to_rent"]
CostShape = Literal["recurring", "one_off", "per_booking", "per_guest"]


@dataclass
class Landlord:
    name: str
    share_pct: float  # 0-100; all landlords on a listing must sum to 100


@dataclass
class Scheme:
    type: SchemeType
    # percentage scheme fields
    percentage: Optional[float] = None
    base: Optional[CommissionBase] = None
    extras_included_in_base: bool = False
    # rent_to_rent scheme fields
    monthly_rent: Optional[float] = None
    season_start: Optional[str] = None  # ISO date
    season_end: Optional[str] = None


@dataclass
class SetupCost:
    label: str  # short slug identifying what this cost is, e.g. "pulizie"
    shape: CostShape
    amount: float
    # recurring: start_date + end_date define the validity window (inclusive)
    # one_off: only `date` is used (the month/day it was incurred)
    # per_booking / per_guest: start_date/end_date optional; None = always applies
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    date: Optional[str] = None
    note: Optional[str] = None


@dataclass
class Listing:
    id: str
    nome: str
    indirizzo: str
    comune: str
    camere: int
    posti_letto: int
    pms_owner: Optional[str] = None  # owner name as it arrived in the PMS export; immutable record of provenance
    cluster: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    landlords: list[Landlord] = field(default_factory=list)
    scheme: Optional[Scheme] = None
    setup_costs: list[SetupCost] = field(default_factory=list)
    blocking: bool = False
    blocking_reason: Optional[str] = None

    @property
    def configured(self) -> bool:
        """A listing counts as configured once it has either a scheme with a
        valid landlord split, or is explicitly marked blocking (which is
        itself a deliberate, communicated state -- not "unconfigured")."""
        if self.blocking:
            return True
        if self.scheme is None:
            return False
        if self.scheme.type == "percentage":
            return bool(self.landlords) and self._landlord_shares_valid()
        if self.scheme.type == "rent_to_rent":
            return True  # no owner cut to validate
        return False

    def _landlord_shares_valid(self) -> bool:
        total = sum(l.share_pct for l in self.landlords)
        return abs(total - 100.0) < 0.01

    def to_dict(self) -> dict:
        d = asdict(self)
        d["configured"] = self.configured
        return d


# ---------------------------------------------------------------------------
# Selector: the one filtering mechanism reused by every read and bulk-write
# tool, so "what would this affect" (list_listings) and "make it so" (any
# mutation) always agree on the same set of listings.
# ---------------------------------------------------------------------------

@dataclass
class Selector:
    listing_ids: Optional[list[str]] = None
    cluster: Optional[str] = None
    tags: Optional[list[str]] = None       # AND: listing must have every tag
    comune: Optional[str] = None
    camere: Optional[int] = None
    owner: Optional[str] = None            # current landlord name (substring, case-insensitive)
    scheme_type: Optional[str] = None      # "unset" | "percentage" | "rent_to_rent"
    blocking: Optional[bool] = None

    def is_empty(self) -> bool:
        return not any([
            self.listing_ids, self.cluster, self.tags, self.comune,
            self.camere is not None, self.owner, self.scheme_type, self.blocking is not None,
        ])


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class Store:
    def __init__(self) -> None:
        self.listings: dict[str, Listing] = {}
        self.bookings: list[dict] = []
        self.costi_extract: list[dict] = []
        self.imported = False
        self.source_files: dict = {}

    # -- import ------------------------------------------------------------

    def import_export(self, data_dir: Path) -> dict:
        immobili_path = data_dir / "immobili.csv"
        prenotazioni_path = data_dir / "prenotazioni.csv"
        costi_path = data_dir / "costi.csv"

        self.listings = {}
        for row in _read_semicolon_csv(immobili_path):
            listing_id = row["ID"].strip()
            pms_owner = row.get("Proprietario", "").strip() or None
            self.listings[listing_id] = Listing(
                id=listing_id,
                nome=row.get("Nome", "").strip(),
                indirizzo=row.get("Indirizzo", "").strip(),
                comune=row.get("Comune", "").strip(),
                camere=_to_int(row.get("Camere")),
                posti_letto=_to_int(row.get("Posti letto")),
                pms_owner=pms_owner,
                cluster=None,
                tags=[],
                landlords=_landlords_from_pms(pms_owner),
            )

        self.bookings = list(_read_semicolon_csv(prenotazioni_path))
        self.costi_extract = list(_read_semicolon_csv(costi_path))
        self.imported = True
        self.source_files = {
            "immobili": str(immobili_path),
            "prenotazioni": str(prenotazioni_path),
            "costi": str(costi_path),
        }

        comuni = sorted({l.comune for l in self.listings.values()})
        owners = sorted({l.name for lst in self.listings.values() for l in lst.landlords})
        unowned = sorted(lid for lid, lst in self.listings.items() if not lst.landlords)
        checkins = [r.get("Check in", "") for r in self.bookings if r.get("Check in")]

        return {
            "listings_imported": len(self.listings),
            "bookings_imported": len(self.bookings),
            "cost_lines_imported": len(self.costi_extract),
            "comuni": comuni,
            "owners_seen": owners,
            "listings_without_owner": unowned,
            "booking_date_range": {
                "first": min(checkins, key=_dmy_key) if checkins else None,
                "last": max(checkins, key=_dmy_key) if checkins else None,
            },
            "note": (
                "Cluster and Tag are empty on every listing and Quota "
                "Proprietario/Quota PM are empty on every booking -- that is "
                "expected, it's what this call fills in."
            ),
        }

    # -- selection -----------------------------------------------------------

    def select(self, sel: Selector) -> list[Listing]:
        if sel.is_empty():
            raise DomainError(
                "Selector is empty -- refusing to match all 40 listings by "
                "accident. Pass listing_ids or at least one filter field."
            )
        if sel.listing_ids:
            missing = [i for i in sel.listing_ids if i not in self.listings]
            if missing:
                raise DomainError(f"Unknown listing id(s): {missing}")
            return [self.listings[i] for i in sel.listing_ids]

        out = []
        for lst in self.listings.values():
            if sel.cluster is not None and lst.cluster != sel.cluster:
                continue
            if sel.tags and not set(sel.tags).issubset(set(lst.tags)):
                continue
            if sel.comune is not None and lst.comune.lower() != sel.comune.lower():
                continue
            if sel.camere is not None and lst.camere != sel.camere:
                continue
            if sel.owner is not None:
                names = [ll.name for ll in lst.landlords] + ([lst.pms_owner] if lst.pms_owner else [])
                if not any(sel.owner.lower() in n.lower() for n in names):
                    continue
            if sel.blocking is not None and lst.blocking != sel.blocking:
                continue
            if sel.scheme_type is not None:
                cur = "unset" if lst.scheme is None else lst.scheme.type
                if cur != sel.scheme_type:
                    continue
            out.append(lst)
        return sorted(out, key=lambda l: l.id)

    # -- mutations (all return a diff; persist only when dry_run=False) ------

    def set_cluster(self, sel: Selector, cluster: str, dry_run: bool) -> dict:
        targets = self.select(sel)
        diff = [{"id": l.id, "before": l.cluster, "after": cluster} for l in targets]
        if not dry_run:
            for l in targets:
                l.cluster = cluster
        return {"dry_run": dry_run, "field": "cluster", "changes": diff}

    def set_tags(self, sel: Selector, tags: list[str], mode: str, dry_run: bool) -> dict:
        if mode not in ("add", "remove", "replace"):
            raise DomainError('mode must be "add", "remove" or "replace"')
        targets = self.select(sel)
        diff = []
        for l in targets:
            before = list(l.tags)
            if mode == "add":
                after = sorted(set(l.tags) | set(tags))
            elif mode == "remove":
                after = sorted(set(l.tags) - set(tags))
            else:
                after = sorted(set(tags))
            diff.append({"id": l.id, "before": before, "after": after})
            if not dry_run:
                l.tags = after
        return {"dry_run": dry_run, "field": "tags", "mode": mode, "changes": diff}

    def set_commission_scheme(
        self, sel: Selector, percentage: float, base: str,
        extras_included_in_base: bool, dry_run: bool,
    ) -> dict:
        if base not in ("net_of_ota_fees", "gross_guest_total"):
            raise DomainError(
                'base must be "net_of_ota_fees" or "gross_guest_total" -- '
                "never guess this. If the operator only said a percentage, "
                "ask what it applies to before calling this tool."
            )
        if not (0 < percentage <= 100):
            raise DomainError("percentage must be between 0 and 100")
        targets = self.select(sel)
        diff = []
        for l in targets:
            before = asdict(l.scheme) if l.scheme else None
            after = Scheme(
                type="percentage", percentage=percentage, base=base,
                extras_included_in_base=extras_included_in_base,
            )
            diff.append({"id": l.id, "before": before, "after": asdict(after)})
            if not dry_run:
                l.scheme = after
                l.blocking = False
                l.blocking_reason = None
        return {"dry_run": dry_run, "field": "scheme", "changes": diff}

    def set_rent_to_rent(
        self, sel: Selector, monthly_rent: float, season_start: str,
        season_end: str, dry_run: bool,
    ) -> dict:
        targets = self.select(sel)
        diff = []
        for l in targets:
            before = asdict(l.scheme) if l.scheme else None
            after = Scheme(
                type="rent_to_rent", monthly_rent=monthly_rent,
                season_start=season_start, season_end=season_end,
            )
            diff.append({"id": l.id, "before": before, "after": asdict(after)})
            if not dry_run:
                l.scheme = after
                l.landlords = []  # sublocazione: no owner cut
                l.blocking = False
                l.blocking_reason = None
        return {"dry_run": dry_run, "field": "scheme", "changes": diff}

    def set_landlords(self, listing_id: str, landlords: list[dict], dry_run: bool) -> dict:
        if listing_id not in self.listings:
            raise DomainError(f"Unknown listing id: {listing_id}")
        total = sum(x["share_pct"] for x in landlords)
        if abs(total - 100.0) > 0.01:
            raise DomainError(f"Landlord shares must sum to 100, got {total}")
        l = self.listings[listing_id]
        before = [asdict(x) for x in l.landlords]
        after = [Landlord(name=x["name"], share_pct=x["share_pct"]) for x in landlords]
        diff = {"id": l.id, "before": before, "after": [asdict(x) for x in after]}
        if not dry_run:
            l.landlords = after
        return {"dry_run": dry_run, "field": "landlords", "changes": [diff]}

    def set_blocking(self, sel: Selector, blocking: bool, reason: Optional[str], dry_run: bool) -> dict:
        if blocking and not reason:
            raise DomainError("reason is required when blocking=true")
        targets = self.select(sel)
        diff = []
        for l in targets:
            diff.append({
                "id": l.id,
                "before": {"blocking": l.blocking, "reason": l.blocking_reason},
                "after": {"blocking": blocking, "reason": reason if blocking else None},
            })
            if not dry_run:
                l.blocking = blocking
                l.blocking_reason = reason if blocking else None
        return {"dry_run": dry_run, "field": "blocking", "changes": diff}

    def add_setup_cost(
        self, sel: Selector, shape: str, label: str, amount: float,
        start_date: Optional[str], end_date: Optional[str],
        cost_date: Optional[str], note: Optional[str], dry_run: bool,
    ) -> dict:
        if shape not in ("recurring", "one_off", "per_booking", "per_guest"):
            raise DomainError('shape must be one of "recurring", "one_off", "per_booking", "per_guest"')
        if shape == "recurring" and not (start_date and end_date):
            raise DomainError("recurring costs need start_date and end_date")
        if shape == "one_off" and not cost_date:
            raise DomainError("one_off costs need cost_date (the month/day it was incurred)")
        targets = self.select(sel)
        diff = []
        for l in targets:
            existing = next((c for c in l.setup_costs if c.label == label), None)
            before = asdict(existing) if existing else None
            new_cost = SetupCost(
                label=label, shape=shape, amount=amount,
                start_date=start_date, end_date=end_date,
                date=cost_date, note=note,
            )
            diff.append({"id": l.id, "label": label, "before": before, "after": asdict(new_cost)})
            if not dry_run:
                if existing:
                    l.setup_costs = [new_cost if c.label == label else c for c in l.setup_costs]
                else:
                    l.setup_costs.append(new_cost)
        return {"dry_run": dry_run, "field": "setup_costs", "changes": diff}

    def remove_setup_cost(self, sel: Selector, label: str, dry_run: bool) -> dict:
        targets = self.select(sel)
        diff = []
        for l in targets:
            existing = next((c for c in l.setup_costs if c.label == label), None)
            if existing is None:
                continue
            diff.append({"id": l.id, "label": label, "removed": asdict(existing)})
            if not dry_run:
                l.setup_costs = [c for c in l.setup_costs if c.label != label]
        return {"dry_run": dry_run, "field": "setup_costs", "changes": diff}

    # -- summary / export ----------------------------------------------------

    def summary(self) -> dict:
        listings = list(self.listings.values())
        by_cluster: dict[str, int] = {}
        by_scheme: dict[str, int] = {}
        for l in listings:
            by_cluster[l.cluster or "(none)"] = by_cluster.get(l.cluster or "(none)", 0) + 1
            key = l.scheme.type if l.scheme else "unset"
            by_scheme[key] = by_scheme.get(key, 0) + 1
        configured = [l for l in listings if l.configured]
        blocking = [l for l in listings if l.blocking]
        unconfigured = [l.id for l in listings if not l.configured]
        return {
            "total_listings": len(listings),
            "configured": len(configured),
            "blocking": len(blocking),
            "unconfigured_ids": unconfigured,
            "by_cluster": by_cluster,
            "by_scheme": by_scheme,
        }

    def to_configuration_dict(self) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_files": self.source_files,
            "summary": self.summary(),
            "listings": [l.to_dict() for l in sorted(self.listings.values(), key=lambda x: x.id)],
        }

    def export_configuration(self, out_path: Path) -> dict:
        cfg = self.to_configuration_dict()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"written_to": str(out_path), "summary": cfg["summary"]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_semicolon_csv(path: Path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            yield row


def _to_int(v: Optional[str]) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def _landlords_from_pms(name: str) -> list[Landlord]:
    if not name:
        return []
    return [Landlord(name=name, share_pct=100.0)]


def _dmy_key(s: str):
    try:
        return datetime.strptime(s, "%d/%m/%Y")
    except ValueError:
        return datetime.min
