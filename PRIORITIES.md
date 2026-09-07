# Priorities

Written before any code, per the brief. Not revised afterward to match what I actually built.

## What this problem actually is

Reading the transcript twice, this isn't really a "CRUD form over a domain model" problem — the assignment is testing two specific things, and it says so almost in plain language:

1. **The base-of-a-percentage question.** "Marco is at 25%" is not a number, it's a placeholder for four different numbers depending on what it's a percentage *of*. The transcript spends its longest single exchange on exactly this ("sì, ma su quale incassato...") and calls it out explicitly as the thing that moves the most money. If my tool lets an agent set a commission percentage without also forcing a base, I've built a system that can silently produce a wrong tax filing, which is the one thing the brief says is not allowed to happen quietly. So: **the commission-scheme tool takes `base` as a required argument with a closed enum, no default, and the agent's system prompt is instructed to never infer it — always ask.** This is the single most important design decision in the whole exercise and everything else is secondary to it.

2. **The "otto volte" problem.** Edo configures eight Pianeta Casa listings one at a time, on screen, narrating "Marina 1... Marina 2... Marina 3..." — and Sara audibly notices ("Devi rifarlo otto volte?"). That's the operator pain the whole brief is about, dramatized in the fixture on purpose. A tool surface built one-listing-at-a-time reproduces this problem inside the chat instead of solving it. So: **every mutating tool accepts a selector (explicit IDs, or a filter by cluster/tag/comune/room count/config status) instead of a single listing ID**, and the setup-costs tool in particular has to support the negotiation pattern in the brief: apply broadly, then narrow with a correction that overrides only the narrower subset ("tutti i bilocali sul mare" after "tutti quelli al mare"). I designed this as an upsert-by-label so a correction never duplicates a cost line, it replaces the value for exactly the listings the correction names.

Everything else in the slice (grouping, blocking, co-ownership) is real but comparatively mechanical — a filter-and-set operation, not a negotiation. I gave it enough tool surface to be usable but didn't spend the scarce hours polishing it further than "correct and boring."

## What I'm building

- An MCP server (`server/`) that is the actual state owner: it holds the imported portfolio in memory, and every listing mutation goes through a tool call with a selector + a diff-shaped response, so the same server can answer "what would change" and "make it so" through the same code path (a `dry_run` flag), which is what lets the UI show a change before it lands without a second, parallel "preview" system to keep in sync.
- An agent loop (`agent/`) over that server, built on the Claude Agent SDK, restricted to only the domain tools (no filesystem/bash access for the agent — it cannot do anything to the account except through the MCP surface, which is also the honest way to demo that the tool surface is sufficient on its own).
- A minimal local web UI (`ui/`): import on one side, live configuration state in a table, chat on the other, and the diff from the most recent tool calls shown before/alongside the reply. Typing only — I'm not building speech input; see cuts below.
- `out/configuration.json`, written whenever the agent calls the export tool (I default the UI to call it automatically after every turn that changed something, in addition to on explicit "we're done", since a live demo is exactly the situation where "forgot to click save" would be embarrassing).

## What I am deliberately not building, and why

- **No P&L / quota-proprietario computation.** The brief's vocabulary table explains it, but the scope line is explicit: "import, grouping, landlord terms, and setup costs." Computing the actual owner payout needs the OTA-fee logic, tourist-tax passthrough, and cost amortization all working together correctly against real bookings — that's the next step in the real 14-step onboarding, not this slice. Getting the *configuration* right (scheme + base + costs, correctly scoped and windowed) is the prerequisite for that number ever being trustworthy, and it's already the full 4-5 hour budget on its own. I'd rather hand over a configuration file a P&L engine could be built on top of than a half-working P&L number nobody should trust.
- **No undo / edit history.** `remove_setup_cost` exists (removing a specific cost is a normal domain action, not "undo"), but there's no multi-step undo stack across the session. On a live call, if the operator misspeaks the correction is itself the next instruction ("no, otto" as we just saw), not a request to rewind. I'd rather spend the time on getting corrections-as-new-instructions right than on session history.
- **No persistence across restarts beyond the JSON export.** State lives in the MCP server process for the duration of one call. If the call is interrupted, you re-run from the exported `out/configuration.json` (which I make trivial to reload). A database is real engineering for a problem ("what if the operator's laptop dies mid-call") that isn't in scope for a 4-5 hour take-home.
- **No voice input.** The brief allows it ("if you want to go there") but doesn't ask for it, and it adds a whole speech pipeline for zero domain-logic value — the interesting problem here is what the tools do, not how text gets into the box. Typing only.
- **No dedicated "translate" tool.** The brief's Italian note reads as reassurance to *me*, the person building this, not as a requirement that the *running app* mediate Italian↔English — the operator is a native Italian speaker having the call in Italian, and Claude is fluent in Italian, so the agent should just understand Sara's and Edo's sentences directly. Building a translate_text tool the agent calls on every message would be theater, not function.
- **No auth, multi-tenant, multi-operator, or concurrent-session handling.** One operator, one call, one process. This is a screen-share tool, not a SaaS product.
- **Minimal effort on CSV-parsing robustness.** The brief says parsing is not scored and that the graded call uses a different portfolio; I read the export defensively enough not to crash on the given shape, but I'm not hardening against arbitrary malformed exports.
- **No design/styling.** Explicitly zero-weighted in the brief. An unstyled table and a text box is the correct amount of effort here.

## Where I expect to be wrong, and what I'd ask the client instead of guessing

- Whether "camera" in the fixture means bedroom or something else locally (I treated `Camere` = bedroom count, so "una camera sola" = `Camere == 1`, matching "bilocale" naming in the data) — I'd confirm this with the manager rather than assume it generalizes to every PMS export.
- Whether setup costs ever need a currency other than EUR, or a per-night (rather than per-booking) shape — not seen in this transcript, so not modeled; I'd ask before the next call whether either has come up.
- Whether a blocking listing should still accrue setup costs while blocked (I currently allow it — the cost exists whether or not the revenue is attributed yet — but this is an assumption, not something Sara was asked).
