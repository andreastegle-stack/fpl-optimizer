# fpl-optimizer

Fantasy Premier League decision support for one manager (Andreas). Goal: maximise total
season points (overall rank). The tool recommends; Andreas decides.

## Working with Andreas
- Product manager, not a developer. Talk to him in Norwegian, plain language, consequences
  over mechanics. Do technical steps yourself; never hand him a command if you can run it.
- Code, docs, issues and PRs in English (open repo, outside contributors). The report he reads
  is in Norwegian.

## Decisions (agreed 2026-10-15, restart from first principles)
Why the restart: the previous version (private repo `fpl-optimizer-utdatert`, do not copy code
from it) finished GW1–6 slightly below the global average. Root causes:
1. Starting XI and bench were picked on a 5-GW weighted horizon instead of the next GW alone.
2. Transfers were valued as if no future transfers exist: no value for rolling a free transfer,
   no multi-week transfer plan, so marginal moves were recommended.
3. The projection model was never benchmarked against a simple baseline.
4. It depended on the home PC and a self-rescheduling local task; a Gmail app password once
   leaked into git.

| Area | Decision |
|---|---|
| Projections | Our own expected-points model per player per GW. |
| Model gate | Before advice relies on it, the model must beat (a) FPL `form` / `ep_next` (which in 2026/27 is just form) and (b) a simple baseline (points per game adjusted for minutes and fixtures) on a full past season. Until then advice uses the simple baseline. A CI check enforces the gate on every PR that touches the model. |
| Optimiser | Reuse the open-source solver approach (solioanalytics/open-fpl-solver, Apache-2.0) with our projections plugged in. MILP over a 6-GW horizon: transfers per GW, value of rolled free transfers, hits only when worth it, chip timing. |
| Lineup | Starting XI, bench order and captain use the next GW's projections only. |
| Chips | Planned from day one. Set 1 (WC, FH, BB, TC) expires after GW19. |
| Data | FPL public API only. No FPL login: the squad is read as of the last deadline. Transfers Andreas made before the deadline are told in chat and recorded in a pending-transfers file. |
| Overrides | Andreas tells Claude in chat (injury news, roles); Claude records them. No file he has to edit himself. |
| Runs | GitHub Actions, no home PC. Every morning (after the nightly price change at 00:00 UK) and about 3 hours before each deadline. |
| Report | Static page on GitHub Pages, readable on a phone. Shows the recommendation, 2–3 alternatives with expected points and reasons, and a weekly scorecard. |
| Notification | Email to Andreas before each deadline, sent from Actions. SMTP credentials only as GitHub Actions secrets, never in the repo. |
| Measurement | Weekly: advice vs what Andreas did vs the GW average. Backtests on past seasons once the model exists. |
| Contributions | Open repo, MIT. Claude reviews each PR and writes an assessment in Norwegian; the model gate runs in CI; Andreas approves the merge. |

## FPL rules 2026/27 (verified against the API 2026-10-15)
- £100m, 15 players (2 GK / 5 DEF / 5 MID / 3 FWD), max 3 per club. XI: 1 GK, ≥3 DEF, ≥2 MID, ≥1 FWD.
- Points: 1 (<60 min) / 2 (60+). Goals GK 10, DEF 6, MID 5, FWD 4; assist 3. Clean sheet GK/DEF 4,
  MID 1. −1 per 2 conceded (GK/DEF). +1 per 3 saves, penalty save +5, penalty miss −2, yellow −1,
  red −3, own goal −2, bonus 1–3 (BPS). Defensive contributions +2 (DEF 10 CBIT; MID/FWD 12 CBIRT).
- 1 free transfer per GW, bank up to 5, −4 per extra transfer.
- Prices change once a day at 00:00 UK. Sell price = buy price + half the profit, rounded down to 0.1m.
- Two chip sets (GW1–19, GW20–38), one chip per GW. `ep_next` equals `form` this season.
- Read `game_settings`, `game_config.scoring` and `chips` from `bootstrap-static` instead of hard-coding where possible.

## Security
- Never commit secrets. Keep `.env`, `.env*` and `*.env` in .gitignore. Check
  `git status --untracked-files=all` before adding files. Never `git add -A` blindly.
