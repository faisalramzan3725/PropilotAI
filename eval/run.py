"""
Checks against the domain layer directly (server/domain.py) -- no agent, no
LLM calls, so these run in under a second and are what CI/a reviewer would
actually re-run. They exist to pin down the parts of the domain model that
are easy to get subtly wrong, including one that WAS wrong during
development (case 2) until this suite would have caught it.

Five cases, each either a plain assert or a print + explicit PASS/FAIL --
run with:  python -m eval.run
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from server.domain import DomainError, Selector, Store

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def fresh_store() -> Store:
    s = Store()
    s.import_export(DATA_DIR)
    return s


# ---------------------------------------------------------------------------
# 1. import reads the fixture as shipped
# ---------------------------------------------------------------------------

def case_1_import():
    s = fresh_store()
    check("case 1: import reads all 40 listings", len(s.listings) == 40, f"got {len(s.listings)}")
    check("case 1: cluster/tags start empty per the brief",
          all(l.cluster is None and l.tags == [] for l in s.listings.values()))
    check("case 1: A038-A040 arrive with no owner",
          all(s.listings[i].pms_owner is None for i in ("A038", "A039", "A040")))


# ---------------------------------------------------------------------------
# 2. owner selector survives a scheme change that clears `landlords`
#    (this was a real bug: set_rent_to_rent empties `landlords`, which used
#    to be the ONLY thing an owner-selector matched against -- a second
#    owner="Pianeta Casa" call after the first would then match nothing.
#    Fixed by keeping an immutable `pms_owner` on the listing. This case
#    pins that fix down.)
# ---------------------------------------------------------------------------

def case_2_owner_selector_survives_scheme_change():
    s = fresh_store()
    before = s.select(Selector(owner="Pianeta Casa"))
    check("case 2: owner filter finds the 8 Pianeta Casa listings before any change",
          len(before) == 8, f"got {len(before)}")
    s.set_rent_to_rent(Selector(owner="Pianeta Casa"), 1200.0, "2026-06-01", "2026-10-31", dry_run=False)
    after = s.select(Selector(owner="Pianeta Casa"))
    check("case 2: owner filter still finds all 8 after rent_to_rent clears landlords[]",
          len(after) == 8, f"got {len(after)}")
    check("case 2: rent_to_rent listings really did lose their landlord split",
          all(l.landlords == [] for l in after))


# ---------------------------------------------------------------------------
# 3. commission scheme cannot be set without a valid, explicit base
# ---------------------------------------------------------------------------

def case_3_commission_base_is_mandatory():
    s = fresh_store()
    rejected = False
    try:
        s.set_commission_scheme(Selector(listing_ids=["A009"]), 25.0, "some_made_up_base", False, dry_run=False)
    except DomainError:
        rejected = True
    check("case 3: an invalid/guessed base is rejected, not silently accepted", rejected)
    check("case 3: the listing was NOT mutated by the rejected call",
          s.listings["A009"].scheme is None)


# ---------------------------------------------------------------------------
# 4. setup-cost negotiation: broad apply, then a narrower correction
#    overrides ONLY the narrower subset (the "sui bilocali la pulizia e
#    otto" moment from the transcript)
# ---------------------------------------------------------------------------

def case_4_setup_cost_upsert_scopes_correctly():
    s = fresh_store()
    s.set_tags(Selector(comune="Cagliari"), ["Mare"], "add", dry_run=False)
    s.set_tags(Selector(comune="Olbia"), ["Mare"], "add", dry_run=False)
    s.add_setup_cost(Selector(tags=["Mare"]), "per_booking", "pulizie", 10.0, None, None, None, None, dry_run=False)
    s.add_setup_cost(Selector(tags=["Mare"], camere=1), "per_booking", "pulizie", 8.0, None, None, None, None, dry_run=False)

    bilocale_mare = [l for l in s.select(Selector(tags=["Mare"])) if l.camere == 1]
    trilocale_plus_mare = [l for l in s.select(Selector(tags=["Mare"])) if l.camere != 1]
    check("case 4: at least one bilocale and one non-bilocale sea listing exist to test with",
          len(bilocale_mare) > 0 and len(trilocale_plus_mare) > 0)

    def pulizie_amount(listing):
        return next(c.amount for c in listing.setup_costs if c.label == "pulizie")

    check("case 4: bilocali (1 camera) on the sea were corrected to 8",
          all(pulizie_amount(l) == 8.0 for l in bilocale_mare))
    check("case 4: everyone else on the sea stayed at the broad 10",
          all(pulizie_amount(l) == 10.0 for l in trilocale_plus_mare))
    check("case 4: correction did not duplicate the cost line (upsert, not append)",
          all(len([c for c in l.setup_costs if c.label == "pulizie"]) == 1 for l in s.select(Selector(tags=["Mare"]))))


# ---------------------------------------------------------------------------
# 5. the full transcript scenario reproduces the transcript's own numbers
#    (37 configured normally + 3 blocking = 40; matches the operator's own
#    end-of-call note in data/onboarding_call.md) and export writes a
#    configuration.json carrying every field the brief asks for.
# ---------------------------------------------------------------------------

def case_5_full_scenario_matches_transcript_and_exports():
    s = fresh_store()
    s.set_rent_to_rent(Selector(owner="Pianeta Casa"), 1200.0, "2026-06-01", "2026-10-31", dry_run=False)
    s.add_setup_cost(Selector(owner="Pianeta Casa"), "recurring", "canone", 1200.0, "2026-06-01", "2026-10-31", None, None, dry_run=False)
    s.set_commission_scheme(Selector(scheme_type="unset"), 25.0, "net_of_ota_fees", False, dry_run=False)
    s.set_commission_scheme(Selector(listing_ids=["A009", "A010"]), 22.0, "net_of_ota_fees", False, dry_run=False)
    s.set_landlords("A037", [{"name": "Rossi Gianluca", "share_pct": 60}, {"name": "Bianchi", "share_pct": 40}], dry_run=False)
    for comune in ("Cagliari", "Olbia", "Roma", "Venezia"):
        s.set_cluster(Selector(comune=comune), comune, dry_run=False)
    s.set_cluster(Selector(cluster="Olbia"), "Nord Sardegna", dry_run=False)
    s.set_tags(Selector(cluster="Cagliari"), ["Mare"], "add", dry_run=False)
    s.set_tags(Selector(cluster="Nord Sardegna"), ["Mare"], "add", dry_run=False)
    s.set_tags(Selector(cluster="Roma"), ["Citta"], "add", dry_run=False)
    s.set_tags(Selector(cluster="Venezia"), ["Citta"], "add", dry_run=False)
    s.set_tags(Selector(listing_ids=["A024", "A025", "A026"]), ["Luxury"], "add", dry_run=False)
    s.add_setup_cost(Selector(tags=["Mare"]), "per_booking", "pulizie", 10.0, None, None, None, None, dry_run=False)
    s.add_setup_cost(Selector(tags=["Mare"]), "per_guest", "kit_benvenuto", 2.0, None, None, None, None, dry_run=False)
    s.add_setup_cost(Selector(tags=["Mare"], camere=1), "per_booking", "pulizie", 8.0, None, None, None, None, dry_run=False)
    s.add_setup_cost(Selector(listing_ids=["A013"]), "one_off", "arredamento", 7400.0, None, None, "2026-06-15", None, dry_run=False)
    s.set_blocking(Selector(listing_ids=["A038", "A039", "A040"]), True, "contratto non ancora firmato", dry_run=False)

    summary = s.summary()
    check("case 5: 37 listings land on a real scheme (not blocking)",
          summary["total_listings"] - summary["blocking"] == 37, f"summary={summary}")
    check("case 5: exactly 3 are blocking, matching the operator's own note in the transcript",
          summary["blocking"] == 3)
    check("case 5: all 40 count as configured (real scheme OR explicitly blocking -- none silently unset)",
          summary["configured"] == 40)

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "configuration.json"
        s.export_configuration(out_path)
        import json
        cfg = json.loads(out_path.read_text(encoding="utf-8"))
        required_fields = {"id", "scheme", "cluster", "tags", "setup_costs", "blocking", "blocking_reason"}
        listings = cfg["listings"]
        check("case 5: exported JSON has all 40 listings", len(listings) == 40)
        check("case 5: every listing carries every field the brief asks for",
              all(required_fields.issubset(l.keys()) for l in listings))
        a013 = next(l for l in listings if l["id"] == "A013")
        check("case 5: A013's furniture cost is one_off, not spread across months",
              any(c["shape"] == "one_off" and c["label"] == "arredamento" for c in a013["setup_costs"]))


if __name__ == "__main__":
    for fn in (
        case_1_import,
        case_2_owner_selector_survives_scheme_change,
        case_3_commission_base_is_mandatory,
        case_4_setup_cost_upsert_scopes_correctly,
        case_5_full_scenario_matches_transcript_and_exports,
    ):
        print(f"\n-- {fn.__name__} --")
        fn()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")
