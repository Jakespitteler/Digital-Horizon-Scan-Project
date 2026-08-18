
## Email notifications

Each daily run is a pipeline, one job per stage:

```
scraper      finds the pages and files on the site
diff finder  compares that against the previous run, produces Changes
notifier     packages those Changes into an email and sends it
```

`src/email_sender/notifier.py` is the last stage only. It is handed a list of
`Change` objects and does not scrape, does not work out what changed, and does
not read or write a database. What it sends:

- changes handed to it → a digest listing them
- nothing for 7 days → a "no changes detected" note
- otherwise → nothing

Everything in it is a plain function of its arguments except `send_message()`,
which is the only part that touches the network. That means the wording can be
tested without a mail server, and a mail server being down can't affect any
stage upstream.

The weekly "no changes" note needs to know when we last emailed, which is not
something any earlier stage knows. So `notify()` takes `last_email_at` as an
argument rather than looking it up — the notifier stays stateless and the
caller owns the remembering. Pass `None` and the note simply never fires.

### run demo

```bash
cd src/email_sender
python3 demo.py
```

Simulates three weeks of daily runs, emails are printed rather than sent. It
also stands in as a worked example of the caller: it keeps `last_email_at`
between runs, which is the one bit of state the notifier gives up.

Expect 4 emails over 21 days: digests on days 3 and 12, all clears on days 10
and 19. Day 1 is silent because there is no previous run to compare against,
so the diff finder reports nothing — otherwise the client gets 200 "new page"
lines on the first morning.

### run the scheduler

```bash
python src/scheduler/background_scheduler.py
# or
PYTHONPATH=src python -m scheduler.background_scheduler
```

`PYTHONPATH=src` is needed because `pyproject.toml` sets `packages = ["src"]`,
which doesn't make `email_sender` and `scheduler` importable by those names.
The scheduler works around it for the direct-script case; the proper fix is a
change to `pyproject.toml`, which is shared with the scraper and database work.

Ctrl-C stops it. A task that raises is logged and the loop carries on, so one
bad run doesn't end monitoring.

### run tests

```bash
uv sync          # first time only
uv run pytest
```

### Configuration

Settings come from the environment, so no credentials live in source.

```bash
cp .env.example .env      # then fill it in
```

`notifier.py` reads `.env` itself on import, so there is nothing to `source`.
Anything already exported wins over the file, so you can still override a
setting for one run (`SITE_NAME=x uv run python src/email_sender/demo.py`).
`.env` is gitignored — never commit real values.

Everything defaults to `example.com` placeholders, so the demo runs with no
setup at all.

### Sending real test email

Nothing leaves the machine until you ask it to: `DRY_RUN` defaults to `true`,
which prints emails instead of sending them. That default is deliberate — a
fresh checkout can't mail anyone, and forgetting to configure `.env` fails
loudly rather than quietly mailing `client@example.com`.

To send yourself a real sample report with a Gmail account:

1. Turn on 2-Step Verification on the Google account.
2. Create an **App Password** (Google account → Security → 2-Step
   Verification → App passwords). It's 16 characters. Your normal Gmail
   password will not work — Google blocks plain logins from scripts.
3. Fill in `.env`:

   ```bash
   DRY_RUN=false
   EMAIL=you@gmail.com
   EMAIL_PASSWORD=abcdefghijklmnop     # the app password, spaces optional
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   CLIENT_TO=you@gmail.com             # your own address while testing
   FROM_ADDR=you@gmail.com             # Gmail rewrites this to EMAIL anyway
   SITE_NAME=uwa.edu.au
   ```

4. Send one sample report:

   ```bash
   cd src/email_sender
   python3 send_test.py
   ```

It checks the settings first and tells you exactly what's missing rather than
failing inside SMTP. Check the spam folder if nothing arrives — a brand new
sending address often lands there the first time.

A UWA account won't work for this: Microsoft turned off basic SMTP auth, so
use a personal Gmail (or a throwaway one) for testing. Whatever the client
ends up using is a question for them.

**Put `DRY_RUN` back to `true` when you're done testing**, so nobody runs the
scheduler and mails a real person by accident.

### TODOs

These are marked in the code as well:

- **A dropped send loses that day's changes.** The notifier used to keep an
  outbox, so a dead mail server cost nothing — the email went out next run.
  Now it sends on the spot, and if that fails the diff finder has already moved
  its snapshot on, so tomorrow's run won't see those changes again and nobody
  is told. `notify()` returns `"failed"` for exactly this, but nothing acts on
  it yet. Whoever does persistence has to either hold the changes until the
  send is confirmed, or park failures somewhere they can be retried — with an
  attempt counter and a backoff, so it doesn't retry forever in silence.
- **Nothing remembers `last_email_at` yet.** The scheduler passes `None`, so
  the weekly all clear never fires in the real pipeline (the demo fakes it).
  Same owner as the point above.
- **Safety net for the site being down.** If most checks failed, "every page
  was deleted" is the wrong thing to email anyone. Needs the scraper to report
  how many checks passed and failed per run.
- **Perth time.** Timestamps currently show UTC.
- **HTML email.** Plain text only for now. The plain text part has to stay
  either way, as some corporate mail clients make a mess of HTML-only email.
- **Tests.** The notifier is covered in `tests/email_sender/test_notifier.py`.
  The scraper and diff finder still need theirs.
