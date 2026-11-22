# Probe agent

An active layer after capture. It walks a suspect site as a visitor would and stores what it
finds as ordinary observations, so the existing layers, fusion and evidence table use them.
This is the one exception to the capture rule that nothing is typed or submitted (architecture
§4): plain `ftf capture` is unchanged. The team reports that the NASK mentors agreed to levels
3-4 (2026-11-22).

| Level | Does | Never does |
| --- | --- | --- |
| 1 Explore | Follows same-site links and quiz steps | Submits anything |
| 2 Payment | Reads checkout pages: provider, merchant ID, public key, IBAN, wallet | Fills a payment field |
| 3 Register | Registers only when a form requires just email and password, opens the activation link in the sandbox, logs in | Fills any other field |
| 4 Watch | Records the site for `ftf probe-mail` | Opens attachments, or any mail link other than the activation link |

```bash
uv run ftf probe --fake-site --level 4 --ingest      # safe demo, needs FTF_PROBE_MAIL_DOMAIN

# Real sites run only in the sandbox; its output is then ingested by the app.
docker compose --profile probe build probe probe-guard
docker compose --profile probe run --rm probe https://suspect.example --level 2 --out /data/probe-sandbox/runs/case1
docker compose --profile probe run --rm probe https://suspect.example --level 4 --live --out /data/probe-sandbox/runs/case2
uv run ftf ingest data/probe-sandbox/runs/case2
uv run ftf probe-mail [--leaks] && uv run ftf run
```

## From the app

```bash
docker compose up -d --build
```

Open **Add material** at <http://localhost:8000/add>, paste a link and click **Run probe agent**. Tick the box to let it register. **Run demo**, under the tools, uses the built-in fake site.
The app only writes a job file. `probe-worker` runs it in the sandbox, and **Add to analysis**
ingests the result after refusing any links or special files in it.

## Rules enforced in code

- Non-GET requests are aborted except while the agent submits its own form.
- Private, loopback and reserved addresses are refused. `--fake-site` allows loopback only.
- If a form requires anything beyond email, password and a terms checkbox, the agent skips
  registration and keeps reading the public pages.
- For email confirmation the app reads the bait inbox (the sandbox holds no key) and passes one
  activation link to the sandboxed browser. The app itself never opens it.
- Logout, payment, deposit, download and admin controls are never followed.
- Choices come from fixed rules and no language model, so page text cannot instruct the agent.
- The password lives in memory for one run and is not stored.

## Sandbox

`ftf probe` refuses any real site outside the `probe` container. That container has no API keys,
no app database, no capabilities, a read-only root filesystem, and memory and process limits. It
can write only to `data/probe-sandbox`. It shares the network of `probe-guard`, whose firewall
rejects private, link-local and reserved addresses and has IPv6 off, so the app, CompreFace, the
host and the LAN stay unreachable even if a page escapes the browser. Mail is read outside the
sandbox, so the Resend key never enters it. The firewall and worker start with a plain `docker compose up`. If
your user ID is not 1000, set `FTF_UID`.

## Evidence

New namespaces, all in `ids.conversion`: `contact.iban`, `contact.wallet`, `payment.public_key`,
`payment.merchant_id`, `company.nip`, `company.krs`, `mail.sender_domain`. `payment.provider` is
risk context only. Company numbers weigh 0.6 because scam pages copy real companies' numbers.

Bait addresses are never evidence, since they would link every probed site. Each site gets a
unique invented name and, with `FTF_PROBE_MAIL_DOMAIN`, its own address, both recorded in
`$FTF_DATA_DIR/probe/ledger.json`. With Resend a message is attributed by the exact address it
was sent to. With a shared IMAP mailbox it is attributed only when it contains exactly one bait
name. It counts as **resale** when the site given that identity differs from a DKIM-verified
sender that is not a shared mail service.

## Resend setup

1. Add `bait.<your-domain>` in Resend and enable receiving. Sending can stay off.
2. Add the MX record the Resend dashboard shows for that subdomain. Root-domain mail is untouched.
3. Set `FTF_PROBE_MAIL_DOMAIN` and `FTF_RESEND_API_KEY` in `.env`.

`ftf probe-mail` only reads the list and the messages addressed to ledger addresses. It does not
open links, attachments or raw message downloads.

With per-site addresses, `--leaks` shows which site's address appeared in a breach. It never
downloads breach data, and the agent does not visit dark-web markets.
