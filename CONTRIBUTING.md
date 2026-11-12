# How we work

Short rules so three people can work at the same time without breaking each other's code.

## 1. Never push straight to `main`

`main` must always run. Every change goes through a branch + Pull Request (PR).

```bash
git checkout main
git pull                                  # get everyone's latest work
git checkout -b p3/prometheus-setup       # new branch for ONE task
# ... edit files ...
git add .
git commit -m "Add Prometheus config scraping app servers"
git push -u origin p3/prometheus-setup
```

Then open a PR on GitHub.

## 2. Branch names

`<person>/<short-task>` — lowercase, dashes.

| Example | Who |
|---|---|
| `p1/app-health-endpoints` | Person 1 |
| `p2/round-robin` | Person 2 |
| `p3/locust-scenarios` | Person 3 |

## 3. Commit messages

Start with a verb, say *what* changed:

- ✅ `Add /ready endpoint to app server`
- ✅ `Fix gateway skipping healthy server`
- ❌ `stuff`, `update`, `final final v2`

## 4. Pull Requests

- One PR = one task (small PRs get reviewed faster).
- Fill in the PR template.
- **At least 1 teammate approves** before merge.
- CI (GitHub Actions) must be green ✅.
- Link the issue: write `Closes #12` in the PR description.

## 5. Issues = our to-do list

- Every task in `docs/team-plan.md` becomes a GitHub Issue.
- Labels: `person-1`, `person-2`, `person-3`, `week-N`, `bug`, `docs`.
- Assign yourself when you start.

## 6. Contracts are sacred

`docs/contracts.md` defines the JSON shapes between our parts.
**If you want to change a contract, open a PR that edits `docs/contracts.md` first and get both teammates to approve.** Otherwise someone else's code breaks silently.

## 7. Write down decisions

Any "why did we choose X?" goes in `docs/decisions.md` (2–3 lines). Interviewers ask exactly these questions.

## 8. Secrets

API keys go in `.env` only. If you accidentally commit a key: tell the team, **revoke the key immediately**, then make a new one. Deleting the commit is not enough.

## 9. Code style

- Python 3.12, formatted with `ruff format`, checked with `ruff check`.
- Run before pushing:

```bash
pip install -r requirements-dev.txt
ruff format .
ruff check .
pytest
```

## 10. Weekly sync

- Short meeting each week: what I finished / what I'm doing next / what's blocking me.
- Friday of integration weeks (Week 3, Week 6): merge everything and run the scenario together.
