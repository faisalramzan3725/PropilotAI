"""
MCP server exposing the Dott.House onboarding domain as tools.

Run standalone with:  python -m server.main
(stdio transport -- one operator, one call, one process; see README for why)

Tool surface design notes live in README.md ("The tool surface"). The short
version: every mutating tool takes a *selector* (explicit listing_ids, or a
filter by cluster/tag/comune/room-count/scheme-status) instead of one
listing id, because the operator pain this whole exercise is about is
Edo configuring eight identical listings one at a time on a call. And the
commission-scheme tool makes `base` a required, no-default argument, because
guessing what a percentage applies to is the one mistake in this domain that
turns into a wrong tax filing.

Error handling: every failure below (no account imported yet, an unknown
listing id, an invalid selector, a rejected commission base, ...) is raised
as a DomainError rather than returned as a `{"error": ...}` dict. FastMCP
turns a raised exception into a real protocol-level error (`isError: true`
on the tool result), which is what actually lets both the UI (the red
"err" styling in ui/static/index.html) and the agent unambiguously tell a
failed call apart from a normal one -- a dict shaped like `{"error": "..."}`
is, as far as the wire protocol is concerned, a perfectly successful result,
and treating "not imported yet" as indistinguishable from a normal JSON
payload is exactly the kind of ambiguity that makes an agent mid-call
mishandle it instead of clearly surfacing it to the operator."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from server.domain import DomainError, Selector, Store

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DOTTHOUSE_DATA_DIR", REPO_ROOT / "data"))
OUT_DIR = Path(os.environ.get("DOTTHOUSE_OUT_DIR", REPO_ROOT / "out"))
LIVE_STATE_PATH = OUT_DIR / "_live_state.json"
CONFIG_PATH = OUT_DIR / "configuration.json"

store = Store()
mcp = FastMCP(
    name="dotthouse-onboarding",
    instructions=(
        "Tools for configuring a new Dott.House customer account during a live "
        "onboarding call: import the PMS export, group listings into clusters "
        "and tags, set each listing's invoicing scheme (commission % + the base "
        "it applies to, or a fixed rent-to-rent deal), record co-ownership "
        "splits, mark listings blocking, and record setup costs (recurring, "
        "one-off, per-booking or per-guest). Call import_export first. "
        "Never invent a commission base or a landlord split -- ask the operator."
    ),
)


class SelectorIn(BaseModel):
    """Identifies a set of listings. Provide EITHER listing_ids OR one or more
    filters (cluster/tags/comune/camere/scheme_type/blocking), which combine
    with AND. Use this instead of calling a tool once per listing -- e.g.
    {"tags": ["Mare"]} for "all the sea listings", or {"tags": ["Mare"],
    "camere": 1} to narrow further to the one-bedroom sea listings."""

    listing_ids: Optional[list[str]] = Field(
        default=None, description="Explicit listing IDs, e.g. [\"A001\",\"A002\"]. Takes precedence over the filters below.")
    cluster: Optional[str] = Field(default=None, description="Exact cluster name.")
    tags: Optional[list[str]] = Field(default=None, description="Listing must have ALL of these tags.")
    comune: Optional[str] = Field(default=None, description="City/comune, e.g. \"Cagliari\".")
    camere: Optional[int] = Field(default=None, description="Exact bedroom count, e.g. 1 for a bilocale.")
    owner: Optional[str] = Field(
        default=None,
        description="Current landlord name, substring match, e.g. \"Pianeta Casa\" to select all listings under that owner regardless of comune.")
    scheme_type: Optional[Literal["unset", "percentage", "rent_to_rent"]] = Field(
        default=None, description="Filter by current configuration status.")
    blocking: Optional[bool] = Field(default=None, description="Filter to listings currently marked blocking or not.")

    def to_domain(self) -> Selector:
        return Selector(
            listing_ids=self.listing_ids, cluster=self.cluster, tags=self.tags,
            comune=self.comune, camere=self.camere, owner=self.owner,
            scheme_type=self.scheme_type, blocking=self.blocking,
        )


class LandlordIn(BaseModel):
    name: str
    share_pct: float = Field(description="0-100. All landlords on a listing must sum to exactly 100.")


def _live_state_snapshot() -> None:
    """Writes a working snapshot of the current account state, refreshed after
    every successful mutation, so the UI's state pane always reflects reality
    without going through the agent. This is NOT the deliverable -- that is
    out/configuration.json, written only by export_configuration()."""
    if not store.imported:
        return
    LIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    import json
    LIVE_STATE_PATH.write_text(
        json.dumps(store.to_configuration_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _require_imported() -> None:
    if not store.imported:
        raise DomainError("No account imported yet -- call import_export first.")


# ---------------------------------------------------------------------------
# Import & inspection
# ---------------------------------------------------------------------------

@mcp.tool()
def import_export() -> dict:
    """Load the customer's PMS export (data/immobili.csv, data/prenotazioni.csv,
    data/costi.csv) and start a fresh in-memory account. Call this once at the
    start of every onboarding call, before anything else. Returns a summary:
    how many listings/bookings/cost lines were read, the comuni and owners
    seen, which listings arrived with no owner recorded, and the booking date
    range -- use this to open the call ("ho l'export davanti, quaranta
    annunci...") rather than guessing at the portfolio's shape."""
    result = store.import_export(DATA_DIR)  # FileNotFoundError/DomainError propagate as a real tool error
    _live_state_snapshot()
    return result


@mcp.tool()
def get_summary() -> dict:
    """Portfolio-wide configuration progress: total listings, how many are
    fully configured vs still unconfigured vs blocking, a breakdown by
    cluster and by scheme type, and the list of unconfigured listing ids.
    Call this whenever you need to report progress or decide what's left --
    it's cheaper than listing all 40 listings and reading scheme fields
    yourself."""
    _require_imported()
    return store.summary()


@mcp.tool()
def list_listings(selector: Optional[SelectorIn] = None) -> dict:
    """List listings matching a selector (or all 40, if selector is omitted).
    Returns a compact view of each listing (id, name, comune, camere/posti
    letto, cluster, tags, landlord split, scheme summary, blocking status,
    setup cost labels) -- enough to confirm a selection out loud with the
    operator, not the full nested detail (use get_listing for that). Always
    call this BEFORE a bulk mutation whose scope isn't already obvious, so
    you (and the operator, via the UI) can see which listings would be
    affected before they are."""
    _require_imported()
    sel = selector.to_domain() if selector else Selector(listing_ids=list(store.listings.keys()))
    listings = store.select(sel)  # DomainError (e.g. unknown id) propagates as a real tool error
    return {"count": len(listings), "listings": [_compact(l) for l in listings]}


@mcp.tool()
def get_listing(listing_id: str) -> dict:
    """Full detail for a single listing, including every setup cost with its
    shape and validity window. Use this to double check one listing rather
    than to browse -- for browsing/selecting, use list_listings."""
    _require_imported()
    if listing_id not in store.listings:
        raise DomainError(f"Unknown listing id: {listing_id}")
    return store.listings[listing_id].to_dict()


def _compact(l) -> dict:
    scheme = None
    if l.scheme:
        if l.scheme.type == "percentage":
            scheme = f"{l.scheme.percentage}% of {l.scheme.base}"
        else:
            scheme = f"rent_to_rent {l.scheme.monthly_rent}/mo {l.scheme.season_start}..{l.scheme.season_end}"
    return {
        "id": l.id, "nome": l.nome, "comune": l.comune,
        "camere": l.camere, "posti_letto": l.posti_letto,
        "cluster": l.cluster, "tags": l.tags,
        "landlords": [f"{x.name} {x.share_pct}%" for x in l.landlords],
        "scheme": scheme, "blocking": l.blocking, "blocking_reason": l.blocking_reason,
        "setup_cost_labels": [c.label for c in l.setup_costs],
        "configured": l.configured,
    }


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

@mcp.tool()
def set_cluster(selector: SelectorIn, cluster: str, dry_run: bool = False) -> dict:
    """Assign a cluster to every listing matched by selector. Clusters are
    freeform strings (usually geography or a building name) and there is no
    separate "rename cluster" tool: to rename a cluster, call this again
    with a selector that matches its current name (e.g. {"cluster": "Olbia"})
    and the new name -- this is a filter-and-set operation either way. Set
    dry_run=true first for anything wider than a couple of listings, so you
    can show the operator what would change."""
    _require_imported()
    result = store.set_cluster(selector.to_domain(), cluster, dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


@mcp.tool()
def set_tags(selector: SelectorIn, tags: list[str], mode: Literal["add", "remove", "replace"] = "add",
             dry_run: bool = False) -> dict:
    """Add, remove, or replace tags on every listing matched by selector.
    Tags are freeform (typology/market segment, e.g. Mare, Citta, Luxury,
    Budget) and a listing can have several. mode="add"/"remove" adjust the
    existing tag set; mode="replace" overwrites it."""
    _require_imported()
    result = store.set_tags(selector.to_domain(), tags, mode, dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


# ---------------------------------------------------------------------------
# Landlord terms
# ---------------------------------------------------------------------------

@mcp.tool()
def set_commission_scheme(
    selector: SelectorIn, percentage: float,
    base: Literal["net_of_ota_fees", "gross_guest_total"],
    extras_included_in_base: bool = False,
    dry_run: bool = False,
) -> dict:
    """Put every listing matched by selector on a percentage commission
    ("giro di fatturazione" a percentuale): the manager invoices the guest,
    keeps `percentage`, the owner gets the rest.

    `base` is REQUIRED and has no default on purpose. It is the single
    highest-stakes field in this whole tool surface -- the brief's own
    example is a client who says "twenty-five percent" and means twenty-five
    percent of something nobody has named yet, and the difference between
    the two options below is real money across a whole portfolio:
      - "net_of_ota_fees": percentage of (guest total minus what Airbnb/
        Booking keep as their own commission). This is what most managers
        mean by "sull'incassato" once you press on it.
      - "gross_guest_total": percentage of the full amount the guest paid,
        before OTA fees come out.
    If the operator has only said a percentage and not which base it applies
    to, DO NOT call this tool yet -- ask them first ("venticinque per cento
    di cosa?"). Guessing is the one mistake this domain cannot absorb.

    extras_included_in_base: whether cleaning fees / other guest charges
    count toward the commission base (the transcript's answer was no --
    "le pulizie stanno fuori, quelle sono un servizio mio" -- so this
    defaults to false; set true only if the operator says extras are
    included).

    Calling this on a listing clears any blocking flag it had (a scheme just
    got set, so it is no longer blocked on missing terms)."""
    _require_imported()
    result = store.set_commission_scheme(selector.to_domain(), percentage, base, extras_included_in_base, dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


@mcp.tool()
def set_rent_to_rent(
    selector: SelectorIn, monthly_rent: float, season_start: str, season_end: str,
    dry_run: bool = False,
) -> dict:
    """Put every listing matched by selector on a rent-to-rent / sublocazione
    deal ("affitto garantito"): the manager pays the owner a fixed
    `monthly_rent` for the season [season_start, season_end] (ISO dates,
    e.g. "2026-06-01") and keeps the entire booking revenue -- there is no
    owner percentage. The rent is owed for every month in the season
    regardless of occupancy (an empty month still costs monthly_rent) and
    nothing is owed outside the season. This tool only sets the revenue
    scheme; it does NOT create the recurring setup cost for the rent itself
    -- call add_setup_cost separately with shape="recurring" for that (kept
    as two calls deliberately: the scheme and the cost it implies are
    different facts about the listing, and not every rent-to-rent deal in
    the wild necessarily costs exactly what it pays out). Clears any owner
    landlord split and any blocking flag on the matched listings."""
    _require_imported()
    result = store.set_rent_to_rent(selector.to_domain(), monthly_rent, season_start, season_end, dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


@mcp.tool()
def set_landlords(listing_id: str, landlords: list[LandlordIn], dry_run: bool = False) -> dict:
    """Set the co-ownership split for ONE listing (e.g. a PMS export that
    only recorded one owner, but the real deal is 60/40 with a second person
    the PMS never heard of). Shares must sum to exactly 100. This is
    single-listing on purpose -- unlike commission percentage or tags, an
    ownership split is a fact about one specific property's deed, never a
    portfolio-wide policy you'd want to bulk-apply, so a selector would be
    the wrong tool here and would invite a mistake (accidentally splitting
    ownership on the wrong ten listings)."""
    _require_imported()
    result = store.set_landlords(listing_id, [x.model_dump() for x in landlords], dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


@mcp.tool()
def set_blocking(selector: SelectorIn, blocking: bool, reason: Optional[str] = None, dry_run: bool = False) -> dict:
    """Mark (or unmark) every listing matched by selector as blocking. A
    blocking listing's bookings are excluded from the P&L until it's
    resolved -- use this for listings the manager took on but whose terms
    aren't settled yet (e.g. contract not signed). `reason` is required when
    blocking=true (it's what shows up as unattributed revenue in the
    dashboard, so it needs to be legible on its own -- e.g. "contratto non
    ancora firmato"). Set blocking=false once terms are agreed and you're
    about to call set_commission_scheme or set_rent_to_rent for it (those
    calls also auto-clear blocking, so you rarely need to call this with
    blocking=false yourself -- it exists mainly for blocking=true)."""
    _require_imported()
    result = store.set_blocking(selector.to_domain(), blocking, reason, dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


# ---------------------------------------------------------------------------
# Setup costs
# ---------------------------------------------------------------------------

@mcp.tool()
def add_setup_cost(
    selector: SelectorIn, shape: Literal["recurring", "one_off", "per_booking", "per_guest"],
    label: str, amount: float,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    cost_date: Optional[str] = None, note: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Set a setup cost on every listing matched by selector. This is the
    tool for the negotiation part of the call -- costs get stated broadly
    and then narrowed ("dieci a prenotazione sul mare" ... "no aspetta, sui
    bilocali la pulizia e otto"). `label` is a short slug identifying WHAT
    the cost is for (e.g. "pulizie", "kit_benvenuto", "canone",
    "arredamento") and this call UPSERTS by (listing, label): if a listing
    matched by selector already has a cost with this label, its amount/
    window are REPLACED; otherwise a new cost line is added. That means a
    correction like "sui bilocali la pulizia e otto" is just another call to
    this same tool with a narrower selector and the same label -- it
    overwrites "pulizie" only on the listings it actually matches, leaving
    the broader "pulizie: 10" untouched everywhere else. Reuse the same
    label across a negotiation; don't invent a new one per correction.

    The four shapes take different date fields:
      - "recurring": applies every period within [start_date, end_date]
        (ISO dates, both required) regardless of bookings that period --
        e.g. a guaranteed-rent payment. An empty month in the window still
        costs the full amount; nothing is owed outside it.
      - "one_off": a single charge in the month it was actually incurred --
        pass cost_date (ISO date). Do not spread a one_off cost across
        months (e.g. furniture bought once should not become a monthly
        line -- that would fake a bad month and a great one next to it).
      - "per_booking": charged once per booking (e.g. cleaning). start_date/
        end_date are optional; omit both for "always applies".
      - "per_guest": charged once per guest per booking (e.g. a welcome
        kit). start_date/end_date optional, same as per_booking."""
    _require_imported()
    result = store.add_setup_cost(
        selector.to_domain(), shape, label, amount, start_date, end_date, cost_date, note, dry_run,
    )
    if not dry_run:
        _live_state_snapshot()
    return result


@mcp.tool()
def remove_setup_cost(selector: SelectorIn, label: str, dry_run: bool = False) -> dict:
    """Remove the setup cost with this label from every listing matched by
    selector that has one. Use this when a cost is cancelled outright, not
    for corrections to its amount/window -- corrections go through
    add_setup_cost's upsert (see that tool's docstring)."""
    _require_imported()
    result = store.remove_setup_cost(selector.to_domain(), label, dry_run)
    if not dry_run:
        _live_state_snapshot()
    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@mcp.tool()
def export_configuration() -> dict:
    """Write the account's current state to out/configuration.json -- this is
    the file the rest of Dott.House reads, so a configuration that only
    exists in this chat does not count. Call this whenever the operator asks
    to save/checkpoint, and always at the end of the call ("allora sei
    configurata"). Safe to call repeatedly; each call overwrites the file
    with the current state. Returns where it was written and a summary."""
    _require_imported()
    result = store.export_configuration(CONFIG_PATH)
    return result


if __name__ == "__main__":
    mcp.run()
