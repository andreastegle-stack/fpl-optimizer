# fpl-optimizer

Fantasy Premier League decision support for one manager. Every morning, and about
three hours before each deadline, a GitHub Actions run reads the public FPL API,
projects expected points for every player for the next six gameweeks, plans
transfers, captaincy and chips with a mixed-integer program, and publishes a short
Norwegian report on GitHub Pages. Before each deadline it also emails the advice.

The tool recommends; the manager decides. Product decisions and the 2026/27 rules
are in [CLAUDE.md](CLAUDE.md).

**Report:** https://andreastegle-stack.github.io/fpl-optimizer/

## How it works

| Step | Module | What it does |
|---|---|---|
| Data | `api.py`, `squad.py` | Public FPL API only (no login). The squad is the public picks of the last passed deadline, plus transfers recorded in `data/pending_transfers.json`. Sell prices, bank, free transfers and chips are replayed from the entry history. |
| Projections | `projections/` | Expected points per player per gameweek. Today: the **baseline** (points per game adjusted for expected minutes and fixture difficulty, with last season as the prior). FPL `form` is reconstructed as the reference. `model.py` is the slot for our own model. |
| Availability | `availability.py` | FPL injury flags and "expected back" dates, plus `data/overrides.json`. |
| Planner | `optimizer.py`, `plan.py` | MILP on [HiGHS](https://highs.dev), adapted from [open-fpl-solver](https://github.com/solioanalytics/open-fpl-solver) (Apache-2.0, see [NOTICE](NOTICE)): 6-gameweek horizon, rolled free transfers with diminishing value, hits only with a safety margin, chip timing with opportunity costs. XI, bench order and captain use next gameweek's projections only. |
| Report | `report.py`, `templates/` | Static page: what to do, 2–3 alternatives with expected points and reasons, injury flags, chip status, plan for the next weeks, weekly scorecard, backtest table. |
| Review | `review.py` | Once a gameweek is final: result and rank movement, each decision (transfers, captain, bench) judged against what the model expected before the deadline, model vs reality per player (variance vs the model being wrong), and at most two lessons backed by the data. Stored per gameweek on the `state` branch; a short summary goes into the deadline email. |
| Scorecard | `scorecard.py` | Advice (saved on the `state` branch) vs what the manager did vs the gameweek average. |
| Email | `notify.py` | Gmail SMTP from Actions; credentials only as Actions secrets. |

## Model gate

`python -m fpl_optimizer.gate` backtests every projection method on a full past
season from [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League):
for each gameweek, each method sees only matches before that deadline. The CI job
`model-gate` runs it on every pull request. Once `projections/model.py` defines
`project`, the model must beat both FPL form and the baseline on next-gameweek RMSE
and six-gameweek RMSE, or CI fails. Advice only switches to the model when
`projection_source` in `config/settings.json` is `"model"`, which the gate refuses
unless the model passes.

## Running locally

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Windows; use .venv/bin/pip elsewhere
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m fpl_optimizer.gate    # backtest
.venv/Scripts/python -m fpl_optimizer run --site site   # writes site/index.html
```

Set `FPL_CACHE_DIR=.cache/api` to cache API responses while developing. A local
run writes an advice snapshot to `history/advice/` (ignored by git); in Actions the
snapshots live on the `state` branch.

## Schedules

`.github/workflows/advice.yml` runs at 01:30 UTC (after the 00:00 UK price change)
and checks every 30 minutes whether a deadline is 0.75–3.5 hours away; the first
check in that window runs the deadline advice and sends the email, once per
gameweek. Deadlines come from `bootstrap-static`, so nothing is hard-coded.

## Secrets

Never commit secrets. The email step reads `SMTP_USER`, `SMTP_PASSWORD` (a Gmail
app password) and `MAIL_TO` from GitHub Actions secrets. Without them the run still
publishes the page and skips the email.

## Contributing

Pull requests are welcome. CI runs the tests and the model gate; Claude reviews
each PR and the repository owner approves the merge. Licensed under MIT.
