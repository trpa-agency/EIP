# Tester invite email + observed-session facilitator guide

Two testing waves, in this order (per the "observed sessions first" decision):

1. **Observed sessions** — 2 scheduled 30-min Teams/screen-share think-alouds (one GIS-savvy, one
   non-GIS). Catch blocking issues before wider invites. Use the **facilitator script** below.
2. **Async wave** — email 5–8 staff the **invite** below; 1-week window.

Live start links (GitHub Pages):
- Tools Index — https://trpa-agency.github.io/EIP/html/index.html
- Projects Map — https://trpa-agency.github.io/EIP/html/projects-map.html
- Project Locator — https://trpa-agency.github.io/EIP/html/locator.html

---

## Part 1 — Observed-session facilitator script (for you to run)

**Setup:** tester shares their screen. You watch, take notes, and stay quiet — no coaching unless
they're fully stuck for ~2 min. Your job is to notice *where* they hesitate, misclick, or say "wait,
where's…". Have the Survey123 form open at the end (`session_type = observed`).

**Warm-up (2 min):** "This is a test of the tools, not of you. Please think out loud — say what you're
looking for, what you expect a button to do, and when something surprises you. There are no wrong moves."

**Tasks — read one at a time, don't paraphrase the UI labels:**

Projects Map (start at the Projects Map link):
1. Show only *Watersheds & WQ* projects that are *Completed*. *(watch: do they find pill double-click?)*
2. Find EIP project 01.01.01.0001 and tell me its lead implementer.
3. Filter by any funding source and read me the count.
4. Pick 3–5 projects and show only those on the map.
5. Copy a link to this exact view and open it in a new private window.
6. Export those selected projects as a CSV and open it.
7. Export a branded map image for a slide.

Project Locator (send them to the Locator link, or use the handoff button from task 4):
8. Search a street address you know and drop a pin.
9. Add an EIP project by name or number.
10. Switch the style to TRPA, then back to EIP; choose the "Detail + Basin" layout.
11. Export a PowerPoint slide and open it.
12. From the Locator, get back to the Projects Map with your projects still selected.

**Wrap-up (3 min):** "What one thing would you change first? … Anything you expected to be here that
wasn't?" Then have them submit the Survey123 form while it's fresh.

**Your note template per task:** `task# | completed? (Y/N) | time-ish | friction observed | their words`.
Anything that blocked completion → a P0 GitHub issue the same day.

---

## Part 2 — Async invite email (send to 5–8 staff)

> **Subject:** 20 min to test two new EIP mapping tools (beta) — your feedback shapes them
>
> Hi [name],
>
> I've built two web tools for working with EIP project data and I'd like your eyes on them before we
> roll them out more widely. No install — they run in your browser. Budget about **20 minutes**.
>
> **Start here:** https://trpa-agency.github.io/EIP/html/index.html
> Open both tools from that page. There's a small **"Beta — feedback"** chip on each — click it when
> you're done to send your notes (it takes ~3 minutes).
>
> Please try to actually *do* these, and note anywhere you get stuck or confused — that's the useful part:
>
> **Projects Map** (the interactive map of all EIP projects)
> 1. Show only *Watersheds & WQ* projects in the *Completed* stage. *(tip: try double-clicking a filter pill)*
> 2. Search for EIP `01.01.01.0001` and open its details — who's the lead implementer?
> 3. Filter by a funding source and note the count.
> 4. Select 3–5 projects, then turn on "Show only selected."
> 5. Copy a share link (the link icon in the header) and open it in a private window — does it come back the same?
> 6. Export your selection as CSV and open it.
> 7. Export a branded map image (PNG) of your current view.
> 8. Open it on your phone: pan the map and tap a pin.
>
> **Project Locator** (makes branded locator maps for slides)
> 9. From the Tools page, open the Locator, search a street address, and add it.
> 10. Add an EIP project by name or number.
> 11. Switch the style EIP → TRPA → EIP, and pick the "Detail + Basin" layout.
> 12. Export a PowerPoint slide and open it in PowerPoint.
> 13. Back in the Projects Map, select 3 projects and click "Locator map (3)" — it should open the Locator with your picks.
> 14. From that Locator tab, click "EIP Projects Map" to jump back with the same projects selected.
>
> When you're done, click the **Beta — feedback** chip on either tool and tell me: what you finished,
> how easy it felt, what confused you, and what's missing for your real work.
>
> Please get your feedback in by **[Friday date]**. Thanks — this genuinely changes what I build next.
>
> — Mason

---

## Consolidation (after both waves)

- Export the Survey123 results CSV.
- Each distinct finding → a GitHub issue, labels: `user-testing`, `tool:projects-map` / `tool:locator`
  / `tool:index`, and severity `P0` (blocks a task) / `P1` (major friction) / `P2` (polish).
- **Exit criteria:** ≥5 testers submitted · all P0/P1 fixed and re-verified by an original reporter ·
  median ease ≥4 per tool. Then blank `FEEDBACK_URL` in all three files to retire the chips.
