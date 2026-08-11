
## Email notifications

`src/email_sender/notifier.py` runs once a day, after the scraper. At most one
email per day:

- changes found → a digest listing them
- nothing for 7 days → a "no changes detected" note
- otherwise → nothing

### run demo

```bash
cd src/email_sender
python3 demo.py
```

Simulates three weeks of daily runs,
emails are printed rather than sent. Expect 4 emails over 21 days: a silent
baseline on day 1, digests on days 3 and 12, and all clears on days 10 and 19.

### Configuration

Settings come from the environment, so no credentials live in source.

```bash
cp .env.example .env      # then fill it in
set -a; source .env; set +a
```

`.env` is gitignored never commit real values. Only `EMAIL` and
`EMAIL_PASSWORD` are needed to actually send; everything else defaults to
`example.com` placeholders so the demo runs with no setup. Nothing auto loads
`.env` yet, so export it as above.

### TODOs

These are marked in the code as well:

- **Retries.** If SMTP is down the row stays unsent and we retry next run,
  which is fine for now but it retries forever and tells nobody. Needs an attempt
  counter and a backoff.
- **Safety net for the site being down.** If most checks failed, "every page
  was deleted" is the wrong thing to email anyone. Needs the scraper to report
  how many checks passed and failed per run.
- **Perth time.** Timestamps currently show UTC.
- **HTML email.** Plain text only for now. The plain text part has to stay
  either way, as some corporate mail clients make a mess of HTML-only email.
- **Tests.** `pytest` and `pytest-mock` are already dev dependencies and
  `pyproject.toml` expects them in `tests/`.
