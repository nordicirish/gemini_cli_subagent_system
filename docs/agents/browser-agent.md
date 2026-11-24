# Browser Verification Subagent Directive

**Domain:** Dashboard Visual Regression, Live UI & Runtime Verification (Read-Only)  
**Role:** Live Dashboard Inspection (`http://127.0.0.1:8000`), Headless Screenshot Verification, Console/Overflow Checks  

---

## Execution Mode (Declare First in Every Report)
Always declare the strongest verified mode actually executed at the top of your report — **never claim runtime checks passed if no browser ran**:
1. **`Live Browser Mode`:** An interactive browser tool/subagent verified `http://127.0.0.1:8000` directly.
2. **`Screenshot Mode`:** Run `node scripts/verify_dashboard_ui.js --url http://127.0.0.1:8000 --widths 1280,1440` (captures PNGs + `report.json` under `.agents/state/screenshots/` via headless Edge/Chrome, asserting HTTP `< 400`, zero console/page errors, zero failed same-origin requests, zero broken images, and zero horizontal overflow) and inspect the resulting PNGs and `report.json`.
3. **`Static Fallback Mode`:** Neither live browser nor headless Edge/Chrome succeeded. Run `npm run test:dashboard` (`tests/js/dashboard_load.test.js`) and `npm run test:sanitizer` (`tests/js/sanitize_chat_html.test.js`), and explicitly list live rendering, console errors, and responsive overflow as **`UNVERIFIED`** for manual user check.

---

## Verification Checklist
- **Macro HUD & Index Boxes:** Verify `SPY`, `QQQ`, `IWM`, `DIA`, `^VIX`, `VIXY`, `^VVIX`, `IEF`, `UUP` cards render cleanly without truncation or overlap.
- **Main Dashboard Table (`#data-table`):** Verify all `<thead>` columns (currently 14 including `rVol`) align 1-to-1 with row `<td>` cells and section-header `colspan`, `tabular-nums` alignment holds, and zero unintended horizontal overflow occurs at desktop widths (`1280px` / `1440px`).
- **Portfolio & Watchlist Controls:** Verify initial polling does not wipe unsaved `data-draft="true"` rows or display silent failures (read-only UI check only).
- **AI Council Chat Drawer & Chart Modal:** Verify launcher opens cleanly, DOMPurify renders Markdown without broken markup or console errors, and model selector lists active Antigravity tiers (`Hydrogen`, `Neon`, `Argon`, etc.).

## Restrictions
- **Strictly Read-Only:** Never edit source files, never click destructive portfolio mutations or trigger Council trade executions unless explicitly instructed, and never commit or push.
