# Survey123 form spec — "EIP Tools Beta Feedback"

Transcribe this into the Survey123 **web designer** (survey123.arcgis.com → New Survey → Blank).
One form, three entry points. Each app page opens it with `?field:toolname=<page>` so the right
task checklist and questions appear.

> **Critical:** the field name in Survey123 must be exactly `toolname` (lowercase). If it differs from
> the URL param, the prefill fails **silently** — the form loads with nothing selected. Verify once
> against the published form before sending any invites (see checklist at bottom).

---

## Fields (in order)

### 0. `toolname` — Single Choice — **hidden**, prefilled from URL
- Label: *(internal — not shown)*
- Choices (name → label):
  - `projects-map` → Projects Map
  - `locator` → Project Locator
  - `index` → Tools Index
- In web designer: add the question, then under **Options → Visibility**, set it to **Hidden**.
  It still receives the `?field:toolname=…` value.

### 1. `session_type` — Single Choice — required
- Label: **How are you testing today?**
- Choices:
  - `observed` → In a scheduled screen-share session
  - `async` → On my own
- *(Lets you separate the 2 observed sessions from the async wave in the results CSV.)*

---

### Projects Map block — `relevant: selected(${toolname}, 'projects-map')`

**2. `pm_tasks` — Multiple Choice — required**
- Label: **Which tasks were you able to finish?** (check all that worked)
- Choices:
  - `p1` → Filter to Watersheds & WQ + Completed (double-click a pill)
  - `p2` → Search EIP 01.01.01.0001 and open its details
  - `p3` → Filter by a funding source and read the count
  - `p4` → Select 3–5 projects, then "Show only selected"
  - `p5` → Copy a share link and reopen it in a private window
  - `p6` → Export the selection as CSV
  - `p7` → Export a branded Map PNG
  - `p8` → Use it on a phone / narrow window
- Add "Other / got stuck" via the built-in **"other"** option, or a follow-up text field `pm_stuck`.

**3. `pm_ease` — Single Choice (likert 1–5) — required**
- Label: **Overall, how easy was the Projects Map to use?**
- Choices: `1` → 1 — Very hard … `5` → 5 — Very easy (5 rows)

**4. `pm_confused` — Multiline Text — optional**
- Label: **What confused you or slowed you down?**

**5. `pm_missing` — Multiline Text — optional**
- Label: **What's missing for you to use this in real work?**

---

### Project Locator block — `relevant: selected(${toolname}, 'locator')`

**6. `loc_tasks` — Multiple Choice — required**
- Label: **Which tasks were you able to finish?** (check all that worked)
- Choices:
  - `l1` → Open from the Tools index, search an address, add it
  - `l2` → Add an EIP project by name or number
  - `l3` → Switch EIP→TRPA→EIP and pick "Detail + Basin"
  - `l4` → Export PPTX and open it in PowerPoint
  - `l5` → Handoff: select 3 in Projects Map → "Locator map (3)"
  - `l6` → From that tab, click "EIP Projects Map" to return

**7. `loc_ease` — Single Choice (likert 1–5) — required**
- Label: **Overall, how easy was the Project Locator to use?**
- Choices: `1`…`5` as above

**8. `loc_confused` — Multiline Text — optional**
- Label: **What confused you or slowed you down?**

**9. `loc_missing` — Multiline Text — optional**
- Label: **What's missing for you to use this in real work?**

---

### Tools Index block — `relevant: selected(${toolname}, 'index')`

**10. `idx_clarity` — Single Choice (likert 1–5) — required**
- Label: **From the landing page, was it clear which tool to open for your task?**
- Choices: `1` → 1 — Not at all … `5` → 5 — Completely

**11. `idx_notes` — Multiline Text — optional**
- Label: **Anything unclear or missing on the landing page?**

---

### Shared closing questions (no `relevant` — shown for every tool)

**12. `browser_device` — Single Choice — required**
- Label: **What did you test on?**
- Choices:
  - `chrome_win` → Chrome — Windows
  - `edge_win` → Edge — Windows
  - `firefox` → Firefox
  - `safari_ios` → Safari — iPhone/iPad
  - `other` → Other (note below)

**13. `severity_flag` — Single Choice — optional**
- Label: **Did anything block you from finishing a task?**
- Choices:
  - `p0` → Yes — I couldn't complete a task at all
  - `p1` → Major friction, but I got there
  - `p2` → Minor / polish only
  - `none` → No, it worked
- *(Maps directly to the GitHub issue severity labels P0/P1/P2 during consolidation.)*

**14. `name` — Text — optional**
- Label: **Your name (optional — so we can follow up)**

---

## After you publish

1. Copy the form's share link. It looks like
   `https://survey123.arcgis.com/share/<itemId>`.
2. The three page URLs become:
   - Projects Map → `https://survey123.arcgis.com/share/<itemId>?field:toolname=projects-map`
   - Project Locator → `https://survey123.arcgis.com/share/<itemId>?field:toolname=locator`
   - Tools Index → `https://survey123.arcgis.com/share/<itemId>?field:toolname=index`
3. Hand me `<itemId>` and I'll set `FEEDBACK_URL` in all three files (each with its own `?field:toolname=` suffix), run the QA checklist, and open the PR.

## Prefill sanity check (do this once)
- Open the projects-map URL above in a browser.
- The Projects Map task list should appear and the Locator/Index blocks should be hidden.
- If everything is hidden or the wrong block shows → the field name isn't `toolname` or a choice
  `name` is misspelled. Fix in the designer and re-publish before inviting anyone.
