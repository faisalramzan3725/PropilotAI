"""
A live, scripted replay of the operator side of `data/onboarding_call.md`
against the real agent (Claude Agent SDK + the actual MCP tool surface --
no mocks). Where eval/run.py checks the domain layer in under a second for
free, this is the other end of the pyramid: it drives the whole stack
(agent -> MCP server -> domain model -> out/configuration.json) the same
way a human operator would from the UI, and costs real money to run (see
the estimate this script prints at the end).

This is also the exact conversation documented in README.md's "Test
conversation" section -- if you change the turns here, update that section
too (and vice versa), they're meant to stay in sync.

Run with:  uv run python -m scripts.test_conversation
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.main import OnboardingAgent  # noqa: E402

TURNS = [
    "Ho l'export davanti, quaranta annunci. Iniziamo la chiamata.",

    "Otto li ho in affitto, sono i Pianeta Casa (il palazzo di Via Marina e "
    "quello di Via del Porto, a Olbia): è una sublocazione, l'incasso lo "
    "tengo tutto io e il proprietario non prende percentuale. Milleduecento "
    "al mese ad appartamento, da giugno a ottobre, anche se un mese resta "
    "vuoto. Mettimi anche milleduecento al mese come costo di setup "
    "ricorrente sulla stessa finestra, giugno-ottobre.",

    "Tutti gli altri sono a percentuale, venticinque per cento — tranne i "
    "tre senza proprietario nell'export, a quelli ci arriviamo dopo. È sul "
    "netto: prima tolgo quello che si prende il portale (Airbnb/Booking), "
    "poi faccio il venticinque per cento su quello che resta. Le pulizie "
    "stanno fuori dalla base, sono un servizio mio. Due eccezioni: Via "
    "Sulis 14 int. 3 e Piazza Yenne 2 sono al ventidue per cento, non al "
    "venticinque, stessa base.",

    "Via Garibaldi 19 int. 2 è cointestato: Rossi Gianluca sessanta per "
    "cento, Bianchi quaranta per cento (Bianchi non è nel gestionale, "
    "crealo tu).",

    "Passiamo ai raggruppamenti, per città: Cagliari, Olbia, Roma, Venezia "
    "come cluster. Però quelli di Olbia chiamali Nord Sardegna, non Olbia.",

    "Tag adesso: Mare su Cagliari e Nord Sardegna, Città su Roma e "
    "Venezia. Poi i tre di Palazzo San Lazzaro a Roma sono un'altra fascia "
    "di prezzo, taggali Luxury — ma solo quei tre, gli altri annunci di "
    "Roma restano normali.",

    "Costi ricorrenti sulle prenotazioni adesso. Le pulizie le pago a "
    "prenotazione: dieci euro su tutto quello che è taggato Mare. In città "
    "per ora lascia stare, sto rinegoziando. Poi il kit di benvenuto, due "
    "euro a ospite, stessa selezione: tutto il tag Mare.",

    "No aspetta, correzione: sui bilocali del mare, quelli con una camera "
    "sola, la pulizia è otto, non dieci. Il resto del mare resta a dieci, "
    "e il kit resta due a ospite su tutti quanti.",

    "Ultima cosa sui costi: su Corso Vittorio Emanuele 44 c'è un "
    "arredamento di settemilaquattrocento euro, è una spesa unica del "
    "quindici giugno, l'appartamento era vuoto e l'ho arredato prima di "
    "metterlo a reddito — non spalmarla sui mesi successivi. Poi ci "
    "restano tre appartamenti senza niente: Via Sassari 61, Vico Speranza "
    "2 e Via Trieste 44 int. 3 — il contratto non è ancora firmato, quindi "
    "segnameli come bloccanti con motivo 'contratto non ancora firmato'.",

    "Perfetto, dammi un riepilogo generale dello stato dell'account e "
    "salva la configurazione.",
]


async def main() -> int:
    agent = OnboardingAgent()
    total_cost = 0.0
    had_error = False
    t0 = time.time()
    try:
        for i, message in enumerate(TURNS, 1):
            result = await agent.turn(message)
            total_cost += result.cost_usd
            print(f"\n=== turn {i}/10 (${total_cost:.2f} so far) ===")
            print(f"operatore> {message}")
            print(f"agente> {result.reply}")
            for tc in result.tool_calls:
                flag = "ERR" if tc.is_error else "ok"
                print(f"  [tool:{flag}] {tc.name}({tc.input})")
            if result.is_error:
                had_error = True
                print("  !!! turn reported an error !!!")
    finally:
        await agent.disconnect()

    print(f"\n--- done: ${total_cost:.2f}, {time.time() - t0:.0f}s of agent "
          f"time (not counting this printout) ---")

    # Sanity-check the end state against the transcript's own numbers
    # (data/onboarding_call.md: "42 minuti, 37 annunci su 40 configurati.
    # Tre bloccanti in attesa di contratto.") -- reads the live snapshot
    # the server just wrote, no separate tool call needed.
    import json
    live_state = Path(__file__).resolve().parent.parent / "out" / "_live_state.json"
    ok = True
    if live_state.exists():
        summary = json.loads(live_state.read_text(encoding="utf-8"))["summary"]
        print(f"final summary: {summary}")
        checks = [
            ("40 total listings", summary["total_listings"] == 40),
            ("40 configured (real scheme or explicitly blocking)", summary["configured"] == 40),
            ("3 blocking", summary["blocking"] == 3),
            ("8 rent_to_rent + 29 percentage", summary["by_scheme"].get("rent_to_rent") == 8
             and summary["by_scheme"].get("percentage") == 29),
        ]
        for label, cond in checks:
            print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
            ok = ok and cond
    else:
        ok = False
        print("no out/_live_state.json written -- something went wrong")

    if had_error or not ok:
        print("\nTEST CONVERSATION FAILED")
        return 1
    print("\nTEST CONVERSATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
