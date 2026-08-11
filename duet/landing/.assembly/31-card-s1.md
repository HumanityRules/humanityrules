# Screen spec: Plain Product Card, Section 1 — Identification and proof

Serves both roles: the Branch B Card's opening section, and the Branch A Design-Partner Compact's donated proof unit. One shared spec; the Compact's two divergences are marked where they occur. Medium and technique are out of scope throughout; where the medium turns out to be dynamic, the storyboard note in the Figure 2 spec applies.

## 1. Element inventory and layout

The section contains exactly six elements (Card) or six elements with one substitution (Compact). Nothing else — no intro copy, no subhead, no text between or after the figures, no sentence anywhere whose job is to make the visitor believe the section.

1. **The identification sentence.** The strongest text in the section and on the page; it occupies the slot where a hero slogan would sit. One line if it fits, wrapping naturally if not.
2. **Card only: the access action**, labeled `Sign up`. Subordinate to the sentence — visibly secondary, placed with the sentence as part of one identification group, not a banner and not repeated. **Compact instead:** the line `Built and run by Victor Mendiluce.` directly after the sentence, at lower emphasis than the sentence; no access action anywhere in the section.
3. **Figure 1** immediately below the identification group: the control-plane record.
4. **Figure 1's caption**, attached directly beneath it, small, manual register.
5. **Figure 2** below, a separate proof block at the same width and visual weight as Figure 1 — sequential, never side by side.
6. **Figure 2's caption**, attached directly beneath it, same treatment as Figure 1's.

Hierarchy: sentence strongest; figures equal to each other and stronger than everything but the sentence; captions and the access action (or builder line) at the section's lowest emphasis. The repeated hostname `hermesvmendi01.humr.io` — in Figure 1's URL row and in Figure 2's address bar — is the only join between the figures. No copy states the connection.

Compact grouping: the same sequence packed tighter, sized so the section reads as one unit before the Compact proceeds to its criteria and plans. The Card gives the section more room and lets it carry the page's opening viewport. No other difference.

## 2. Final copy

Identification sentence — **FINAL**:

> **Humanity Rules deploys always-on AI agents as separate services, each with saved work and access to approved tools.**

Compact builder line — **FINAL**: `Built and run by Victor Mendiluce.`

Access action label (Card only) — **FINAL**: `Sign up`

Figure 1 caption — **FINAL TEMPLATE** (date slot = actual local capture date, spelled `Month D, YYYY`; recapture means updating the date):

> **Figure 1. hermesvmendi01 in the Humanity Rules control plane, [capture date].**

Figure 2 caption — **FINAL**:

> **Figure 2. At the same URL, the agent added one row to a Google Sheets checklist and read the row back.**

Fallback Figure 2 caption, used only if the fallback action ships — **FINAL**:

> **Figure 2. At the same deployment URL, the agent found the California LLC filing fee and returned the state source.**

Captions assert nothing beyond what their frame shows plus the capture date. If any deployment substitution occurs (§3), the name in Figure 1's caption changes with it.

## 3. Figure 1 asset spec — the control-plane record

**Source.** The dashboard app card for `hermesvmendi01` in Victor's organization, captured from the live production control plane. Never a recreation, mockup, or re-render.

**Frame.** The single app card, edge to edge. No neighboring cards, no dashboard chrome. All eight rendered elements legible: agent name as header; **URL** (`https://hermesvmendi01.humr.io`, full hostname, untruncated — the card's URL cell CSS-truncates at narrow widths, so capture at a width where the complete hostname renders); **Template**; **Environment**; **Created by**; **Created**; **Last deployed**; **Status** as the green pill reading `Succeeded`. The product's label `Succeeded` is not renamed, glossed, or captioned around.

**Known values, shown as-is.** Environment renders `Sandbox`: shown honestly, never renamed or reconfigured for the frame. If another already-existing deployment naturally passes every gate below and happens to carry a different environment name, it may be preferred; no real object is altered to improve the picture. On the Compact, no copy may use this figure to imply customer-cloud deployment. Created by renders the creator's account email verbatim; see the privacy gate.

**Lived-in gate (all must hold at capture):**
- Created at least seven full days before capture.
- Created and Last deployed both populated, and their rendered relative strings visibly differ. (Calendar-date difference is insufficient: created 8 days ago and deployed 7 days ago both render "1 week ago.")
- Status `Succeeded` with no deployment job in flight — the card carries no polling state and no spinner.
- The URL reachable at capture time.
- Figure 2 uses this exact hostname.

`hermesvmendi01` passes today (created July 30, redeployed August 6, live). Capture as near publication as practical; age only accrues.

## 4. Figure 2 asset spec — the object operating

**Source.** The agent web UI at `https://hermesvmendi01.humr.io`, in a real session, with the browser address bar in frame and the full hostname legible. The address bar is the join key; a frame without it fails.

**Primary action — Google Sheets append and read-back.** Explicit asset dependency, not product work: Victor reconnects the Google credential on this deployment and grants Sheets write scope before capture. The sheet must be a pre-existing spreadsheet Victor genuinely keeps and currently uses, with prior rows; if no such sheet exists, do not create a prop — use the fallback. The sheet must not be about producing this landing page.

Request — **FINAL TEMPLATE** (sheet-name slot = the real sheet's actual title):

> Add a row to [actual sheet name]: Statement of Information | Within 90 days of initial registration. Then read the row back.

**The frame must contain, top to bottom in one thread:** the full request text; the product's rendered activity rows for both the append and the read; and a final response that names the sheet, repeats both cell values, and confirms the row was read back after insertion (returned row or range visible). Insufficient: "Done," an `updatedCells` count, or a bare link. The exchange completes in real time, under a minute, with no elapsed-time jump. If the medium is dynamic: opening state is the typed request, the change is the activity rows appearing, end state is the final response — same content, no jump.

**Thread-history edge.** At the frame's edge, at least three pre-existing, nonempty conversation threads spanning more than one date remain visible. None renamed or created for the page.

**Fallback action — Tavily web search**, used only if Google reconnection or the exact append/read-back fails at capture. Request — **FINAL**:

> Check California's official site for the current fee to form an LLC online. Return the fee, filing method, and source link.

Frame requirements: full request; the `web_search` and `web_extract` activity rows; a final answer with fee, filing method, and official source link all legible together. Same thread-history-edge requirement. This is the recorded degraded option: it proves the deployment operates but shows the one action every chat product also performs. If neither action passes, the section has no Figure 2 and does not ship.

## 5. Privacy pass — pass/fail, no redaction budget

- Every visible string in both frames is approved for publication: field values, thread titles, sheet title, cell values, activity-row parameters, response text.
- Victor explicitly approves the displayed `Created by` email with the actual string in front of him (it is his personal Gmail). No approval, no asset.
- No customer, friend, private repository, private workspace, schedule, message, account number, token, error detail, or private thread title appears anywhere.
- Proof-bearing fields stay unobscured: agent name, hostname in both frames, relative dates, status, request, activity rows, result, thread-history edge.
- No blur boxes, no opaque redactions. Cropping may exclude peripheral material (neighboring cards, unrelated UI regions) but never a proof-bearing field.
- If anything would need redaction, the fix is a different thread or a different deployment — never an edit.

## 6. Build-time verification checklist

1. `hermesvmendi01` deployed, status `Succeeded`, no job in flight.
2. Created ≥ 7 full days before capture; Created and Last deployed rendered strings differ.
3. `https://hermesvmendi01.humr.io` reachable; identical hostname fully legible in Figure 1's URL row (untruncated) and Figure 2's address bar.
4. Template and Environment row strings read and approved exactly as rendered; Environment expected `Sandbox`, shown as-is.
5. Created-by email explicitly approved by Victor.
6. Google credential reconnected with Sheets write scope; the exact append/read-back succeeds live against Victor's real, in-use sheet with prior rows — else execute the Tavily fallback live and verify fee, method, and source are legible in its answer.
7. Thread-history edge: ≥ 3 pre-existing nonempty threads spanning more than one date, none renamed or created for the page.
8. Privacy pass (§5) on both captures.
9. Card only: `Sign up` reaches the WorkOS signup flow; the complete signup path exercised end to end.
10. Figure 1 caption date equals the actual capture date, `Month D, YYYY`; any recapture updates it.
11. Compact variant check: builder line present after the sentence, access action absent, everything else identical to the Card; no customer-cloud inference drawn from the environment value.

