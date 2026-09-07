# Dott.House Onboarding Copilot

A chat tool an operator uses live, on a call with a new customer, to set up
their account: import the property-management export, group the listings
into clusters and tags, decide how each one gets billed (a commission
percentage or a fixed rent-to-rent deal), record co-ownership splits and
one-off setup costs, and save the result to `out/configuration.json`. Built
for the take-home slice described in the brief: import, grouping, landlord
terms, setup costs. See `PRIORITIES.md` for what's deliberately left out
and why, and `transcripts/` for how this was built.

The system is built around controlled tool execution: the agent decides
*what* should happen from the operator's words, but every account change
actually happens through one of the validated domain tools below never
a direct, model-generated edit to the data. That's what makes "the agent
said it did something" and "the account actually changed" the same fact.

![Landing page — right after Import, before the operator has typed anything](docs/screenshots/landing_page.png)

The left pane is the account, live every listing, and how it's
currently configured. The right pane is the conversation. Every agent
reply that changed something shows the actual tool call it made, in a
box you can click open to see exactly what was read or written.

---

## How to run it

From a clean checkout, in this order:

```bash
uv sync                    # installs mcp, claude-agent-sdk, flask
uv run python -m eval.run  # ~1s, no LLM calls -- sanity-checks the domain
                            # logic before you trust anything built on it
uv run python -m ui.main   # starts the app on http://localhost:8000
```

Open `http://localhost:8000` and click **"1. Drop in export / Import"**
that's the "operator drops the export file in" moment from the brief. It
loads the three CSVs in `data/` and starts the call. From there just type
what changed, in Italian or English, into the box on the right.

Two other ways to run the same agent, if you don't want the browser:
`uv run python -m agent.main` is a plain terminal chat with full tool-call
visibility, and `uv run python -m server.main` runs just the MCP server on
its own over stdio (`tools/list` / `tools/call`), independent of any agent
that's the `mcp.command` in `submission.json`.

---

## Architecture

```
   Operator
      |
      v
 Web interface  (ui/main.py + ui/static/index.html)
      |
      v
 Agent runtime  (agent/main.py, Claude Agent SDK)
      |
      v
 MCP tool layer  (server/main.py -- 13 validated tools)
      |
      v
 Domain model  (server/domain.py -- pure Python, no MCP/agent code)
      |
      v
 out/configuration.json
```

The agent's only capability is that 13-tool MCP surface all of the SDK's
built-in tools (Bash, file read/write, etc.) are explicitly disabled in
`agent/main.py`. Every tool call is validated against the domain model
before anything changes, and a rejected call (an unknown listing ID, a
commission scheme with no base) comes back as a real error the agent has
to handle, not a silent no-op. The domain model itself has no idea an
LLM or an MCP server exists that's what makes `eval/run.py` able to
test the actual business logic in under a second, with no server and no
model in the loop.

---

## Walking through a real call

These screenshots are from an actual run against the live agent, not
mockups same conversation as `scripts/test_conversation.py` and the
"Testing" section below, so you can reproduce this exact sequence
yourself.

**1. Import.** Click the button, the agent calls `import_export`, and the
left pane fills in with all 40 listings nothing configured yet, cluster
and tags empty, exactly as the brief describes a fresh export.

![Right after import: 40 listings, 0 configured](docs/screenshots/ls_05.png)

**2. The "otto volte" moment.** The operator says the eight Pianeta Casa
listings are a sublease at a fixed monthly rent. One message, one selector
`set_rent_to_rent` plus a matching recurring `add_setup_cost`, both
scoped to "owner: Pianeta Casa" and all eight update together instead of
one at a time.

![8 of 40 configured after the Pianeta Casa instruction](docs/screenshots/ls_04.png)

**3. The rest, on a percentage and a clarifying question.** "Twenty-five
percent" isn't enough on its own (see "Commission safety" below), so the
operator has to say what it's a percentage *of*. The agent applies 25%
net-of-OTA-fees to everyone left, narrows two listings to 22%, and then
on its own notices three listings still have no owner recorded and asks
what to do about them instead of guessing.

![37 configured, agent flags the 3 ownerless listings and asks](docs/screenshots/ls_03.png)

**4. Either answer works.** Told to just create a landlord for those
three, the agent asks for an actual name (it won't accept "yes, create" as
a name), gets "Faisal", and finishes all 40 as configured with no blocking
listings at all:

![All 40 configured after supplying an owner name](docs/screenshots/ls_02.png)

Told instead to mark them blocking, that works too 40 configured, 3 of
them explicitly blocking with a reason, which is the ending that matches
the original call transcript in `data/onboarding_call.md`:

![All 40 configured, 3 explicitly blocking, final summary saved](docs/screenshots/ls_01.png)

Both are legitimate ways to close out three listings with missing
information the point of the tool is that it never leaves them silently
unconfigured, and never guesses an owner or a commission base on your
behalf.

The rest of the call (not screenshotted individually, but part of the
same run) covers the other half of the tool surface: clustering listings
by city (`set_cluster` Cagliari, Nord Sardegna, Roma, Venezia), tagging
by segment (`set_tags` Mare, Città, and Luxury on just the three
Palazzo San Lazzaro listings), a co-ownership split on one listing that
creates a landlord who wasn't in the export yet (`set_landlords` Via
Garibaldi 19 int. 2, Rossi Gianluca 60% / Bianchi 40%), and per-booking
and per-guest setup costs applied by tag and then narrowed with a
correction (`add_setup_cost` cleaning cost on the whole Mare tag, then
corrected down for just the one-bedrooms, without duplicating the line).

---

## The tool surface

Thirteen tools, all in `server/main.py`, backed by a plain-Python domain
model in `server/domain.py` that has no MCP or agent code in it at all
that's what makes it unit-testable on its own (`eval/run.py`) without ever
spinning up a server or calling the model.

| Tool | What it's for |
|---|---|
| `import_export` | Loads the three CSVs from `data/` and starts a fresh account. Takes no arguments the brief is explicit that `data/` is fixed. |
| `get_summary` | Portfolio-wide progress: totals, configured vs. blocking, breakdowns by cluster and scheme. |
| `list_listings` | A compact view of listings matching a selector (or all 40) the "what would this affect" preview before any bulk change. |
| `get_listing` | Full detail on one listing, including every setup cost. |
| `set_cluster` | Assigns a cluster (usually geography) to every matched listing. Renaming is just re-running this with a selector matching the old name. |
| `set_tags` | Adds, removes, or replaces tags (typology/market segment) on matched listings. |
| `set_commission_scheme` | Percentage commission, with a required `base` see "Commission safety" below. |
| `set_rent_to_rent` | A fixed monthly rent for a season instead of a percentage. Doesn't also create the matching cost line that's a separate `add_setup_cost` call, since the scheme and the cost it implies are different facts that can diverge. |
| `set_landlords` | Co-ownership split for **one** listing. Not selector-based on purpose an ownership split is a fact about one specific property, never a policy you'd bulk-apply. |
| `set_blocking` | Marks listings as blocking (excluded from P&L until resolved), with a required reason. |
| `add_setup_cost` | Records a setup cost (recurring, one-off, per-booking, or per-guest). Upserts by `(listing, label)`, so a correction like "actually the two-bedrooms are cheaper" is just another call with a narrower selector and the same label it overwrites only the listings that match. |
| `remove_setup_cost` | Deletes a cost outright different intent from a correction, so it's a separate tool rather than `amount=0`. |
| `export_configuration` | Writes `out/configuration.json`. Safe to call every turn; the agent is told to call it after every change and always at the end. |

---

## Engineering decisions

**Selector-based operations.** Every tool that changes listings takes a
selector, not a single ID. A selector is either an explicit list of IDs,
or a filter cluster, tags, comune, room count, owner, current scheme,
blocking status that all combine together. This exists because of one
moment in the source transcript: the operator configures eight identical
listings one at a time, narrating "Marina 1... Marina 2... Marina 3...",
and the client interrupts to ask if they really have to do this eight
times. A selector means "all the sea-facing listings" or "everything
under Pianeta Casa" is one call instead of eight, and the same selector
previews a change (`dry_run=true`) before it's applied for real.

**Commission safety.** `set_commission_scheme`'s `base` argument has no
default and is required on every call. "Twenty-five percent" isn't a
number until you say twenty-five percent *of what* net of the booking
platform's own fee, or the guest's full payment before that fee comes out
and that choice moves real money across a whole portfolio. The tool
simply cannot be called without naming it, and the agent's instructions
tell it to ask rather than ever guess.

**Safe cost updates.** Setup costs are matched by `(listing, label)`, not
by ID, so a correction "actually the one-bedrooms by the sea are
cheaper to clean" is just another `add_setup_cost` call with a narrower
selector and the same label: it overwrites only the listings that match,
instead of stacking a second, duplicate cost line next to the first one.
Deleting a cost outright is a separate tool (`remove_setup_cost`) on
purpose, since "correct this" and "this shouldn't exist" are different
intents that shouldn't share one call shape.

---

## What was assumed, and what's worth confirming with a real client

- **`Camere` = bedroom count**, so "a one-bedroom" (`bilocale`) means
  `camere == 1`. Worth confirming this holds for a PMS export from a
  different vendor.
- **Setup costs are always in EUR**, and always one of the four shapes
  above no per-night shape came up in the source data.
- **A blocking listing still accrues setup costs** while it's blocked (the
  cost exists whether or not revenue is currently attributed to it). This
  is a modeling choice, not something a client actually confirmed.
- **The tool will never let the agent set a commission scheme without a
  named base.** This isn't really an assumption it's the one thing in
  this domain the software refuses to guess at, on purpose.

---

## Testing

`uv run python -m scripts.test_conversation` is the other end of the
pyramid: a scripted, ten-turn conversation the same one narrated above
with screenshots run against the real agent and the real Claude Agent
SDK, no mocks. It prints every reply and tool call as it happens and
checks the final state against the source transcript's own numbers (40
listings, 8 rent-to-rent, 29 percentage, 3 blocking). This costs a few
dollars and takes under two minutes to run, since it's a live model
making real tool calls, not a fixture replay.

## Cost

Model: **claude-sonnet-5**, via the Claude Agent SDK. A full onboarding
all 40 listings, replaying the whole source transcript's content takes
**10 operator turns** (the brief's own budget) and costs roughly **$2.80–
$3.10** end to end, 85–95 seconds of agent time, based on several live
runs while building and testing this. Cost per turn varies with how much
it touches — a single-listing correction is cheap, a bulk commission-scheme
call with its own preview costs more.

---

## If something looks broken

A few real bugs turned up during testing after the initial build, all
fixed as of this version:

- **`pyproject.toml` didn't pin an upper bound on `mcp`**, so `uv sync`
  could resolve to `mcp` 2.x, which renamed the API this project is built
  on (`FastMCP` → `MCPServer`). That made the MCP server crash on startup,
  which left the agent with no real tools to call at all it would then
  write out *text* that looked like a tool call instead of actually
  making one. Fixed by pinning `mcp>=1.9.0,<2.0.0`. If you ever see this,
  delete `.venv` and `uv.lock` and run `uv sync` again.
- **The state pane could show stale data from a previous run** after
  restarting the app, because nothing cleared the on-disk snapshot when a
  new process started. Fixed a fresh process now always starts showing
  "not imported" until you actually import in that session.
- **A failed tool call used to look identical to a successful one** (a
  plain `{"error": ...}` result, not a real protocol-level error), which
  made failures easy for both the UI and the agent to miss. Fixed real
  failures now surface as real errors.
- **"New call (reset)" used to delete `out/configuration.json` too**,
  contradicting its own confirmation dialog. Fixed reset now only
  clears the in-memory call and the working snapshot; your last saved
  configuration survives.

---

## What this was built with

Claude (Cowork), on Claude Sonnet 5, driving Claude Code / the Claude
Agent SDK, in a cloud sandbox code, tests, and this README were produced
in that session from the pasted brief and the CSV/transcript data (the
booking CSV arrived truncated by a paste-size limit; see
`transcripts/session-01-build.md` for how the remaining rows were
synthesized and why). Runtime: Python 3.11, `uv` for dependencies, the
`mcp` Python SDK for the server, `claude-agent-sdk` for the agent loop,
Flask for the local UI.
