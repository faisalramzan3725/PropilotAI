# Session 1 — building the take-home

Tool: Claude (Cowork), running on Claude Sonnet 5, in a cloud sandbox linked
to the candidate's Mac via the desktop app's device bridge. This file is a
faithful reconstruction of that session's turns and decisions (the runtime
doesn't hand me a raw transcript file to commit verbatim, so this is
written from the actual conversation rather than exported from it) --
committed uncleaned, including the mistakes.

---

**User** pasted the take-home assignment (the "AI Engineer — Onboarding
Copilot" brief, Dott.House) into the chat, including the repo layout
(`AGENT_SETUP.md`) and, at first, a *description* of the four data files
rather than their literal content.

**Assistant** read the brief, then asked four clarifying questions via the
UI's question tool before writing anything, since this was a multi-hour
build with real architecture forks:
1. Where should the app actually live and run, given the brief says it has
   to run live on the candidate's machine during the call? → answered:
   build directly on the Mac via the desktop device bridge.
2. Should the assistant create and push the real private GitHub repo (needs
   a token), or just `git init` locally? → answered: local only, no token
   shared.
3. How should the agent loop be powered — Claude Agent SDK riding this
   session's own Claude access, or a raw Anthropic API key? → answered:
   Agent SDK (no separate key to manage, and it makes the README's cost/
   turn line honest about what was actually used).
4. UI style — a minimal local web app, or a terminal chat? → answered: web
   app (matches "a flow we can click through" most directly).

Follow-up: the repo needs to be named `takehome-<surname>`; the user's
account name wasn't a real surname, so the assistant asked directly and
was told **RAMZAN**, and to put the folder on the Desktop.

The assistant then requested folder access to `~/Desktop` on the linked Mac
(granted), and separately discovered that this device's bridge does not
expose a remote shell (`device_bash`) — only file transfer and a browser/
computer-use surface. That ruled out "build on the Mac directly, live" and
set the actual plan: build and fully test in the cloud sandbox (full shell,
Python, and the real `claude` CLI available there), then copy the finished,
tested repo onto the Mac's Desktop folder for the candidate to run
themselves at demo time.

**Data files.** Re-reading the pasted brief, the three CSVs were only
*described* (column names, row counts, a couple of example rows) — not
present as literal data, unlike the call transcript which was pasted in
full. The assistant flagged this explicitly rather than guessing, and asked
whether the user had the real files to attach or wanted them synthesized to
match the transcript. The user pasted the full original document (all three
parts: assignment, repo layout, and the literal data files) — but the
paste arrived truncated by a tool-side size cap partway through
`prenotazioni.csv` (out of ~705 booking rows, complete data survived for
listings A001–A027; A028–A040 were cut off mid-row).

Rather than ask for a third paste (real risk of hitting the same cap
again), the assistant kept every row that *did* arrive intact (extracted
programmatically by line range, not retyped — retyping ~480 real CSV rows
by hand would have been both wasteful and error-prone) and wrote a small
generator (`scripts/gen_remaining_bookings.py`) to fill in bookings for the
last 13 listings, reverse-engineering the exact commission formula from the
real rows first (Airbnb 15% / Booking.com 18% of soggiorno+extras, Diretto
0%, verified against several real rows by hand) so the synthesized rows
stay internally consistent with the real ones. This is called out in
PRIORITIES.md and the README as a stated assumption — the brief says
parsing isn't scored and the graded call uses a different portfolio anyway,
so exact realism on 13 of 705 rows wasn't worth blocking on.

**Design.** `PRIORITIES.md` was written next, before any code, landing on
two things as the actual center of the exercise (both taken directly from
specific lines in the brief/transcript): the commission-base question must
never be guessed by the tool layer, and the tool surface must collapse
Edo's "otto volte" repetition into one selector-based call. Domain model,
MCP tool list, and their granularity were designed from there — see
README's "the tool surface" section for the reasoning per tool.

**Build order:** `server/domain.py` (pure Python, no MCP yet) → tested
directly at the REPL against the real transcript scenario end to end. This
caught a real bug on the first run: selecting "all Pianeta Casa listings" a
second time (to attach their recurring rent cost, right after setting them
to rent-to-rent) matched **zero** listings, because `set_rent_to_rent`
clears `landlords` (a sublocazione has no owner cut) and the owner-selector
was, at that point, only matching against `landlords`. Fixed by adding an
immutable `pms_owner` field set at import and never mutated afterward, and
pointed the selector at that. This fix is pinned down as eval case 2
precisely so it can't regress silently.

`server/main.py` (FastMCP wrapper) came next, smoke-tested over real stdio
via the `mcp` client SDK (`tools/list` + a couple of `tools/call`). Then
`agent/` (Claude Agent SDK, `tools=[]` to disable all built-in file/bash
tools so the agent's only capability is the 13 domain tools, system prompt
in `agent/system_prompt.py`), tested first as a single turn, then as the
full ten-message transcript replay end to end against the real Claude
backend (not mocked) — see below. `ui/` (Flask + one HTML page) was tested
via curl against the running server before ever touching a browser.

**A prompt fix mid-build:** the first full replay run had the agent
inventing building-level clusters ("Via Marina", "Via del Porto") on its
own initiative while handling the Pianeta Casa instruction, before the
operator had said anything about clustering yet — a small but real
instance of the model filling in structure nobody asked for. Added an
explicit rule to the system prompt ("only touch cluster/tags on an explicit
grouping instruction; don't invent a name as a side effect of another
change") and re-ran; the correction held on replay (Pianeta Casa's 8
listings correctly ended up under "Nord Sardegna" once the operator gave
that instruction, not under an invented building name).

**Full replay result** (10 operator turns, scripted from the real
transcript's content but not its literal wording, run start-to-finish
against the live Claude Agent SDK / Sonnet 5 backend): 37 listings landed
on a real scheme, 3 correctly ended up blocking with a reason, matching the
operator's own end-of-call note in `data/onboarding_call.md` ("42 minuti,
37 annunci su 40 configurati"). Total cost for that run: **$3.08**, wall
time 128s excluding the human's own typing time. See README for how that
number is used.

`eval/run.py` (5 assertion-based checks against the domain layer directly,
no LLM calls) was written after the bug above was found and fixed, so case
2 specifically pins down that regression. All 5 pass.

**What the assistant pushed back on / declined:** using `uv sync` for local
testing in the cloud sandbox (no PyPI egress there) — resolved by testing
against the sandbox's already-installed system packages directly and
keeping `uv sync` in `submission.json` for the candidate's own machine,
which has normal internet access. Also declined to spend build time on a
P&L/quota-proprietario calculator, on `undo`, or on voice input — all
recorded as deliberate cuts in `PRIORITIES.md` before implementation
started, not as retroactive scope trimming.
