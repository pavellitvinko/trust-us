# Victim contacts — the e-mail addresses and phone numbers victim runs give

Status: 2026-11-22. Companion to [victim-visitor-requirements.md](victim-visitor-requirements.md) §7 (VV-30…VV-32).

A victim run fills lead forms, so it has to give an e-mail address and sometimes a phone number. Every address and number it may give comes from one file. Nothing else in the code makes one up, and the model never chooses one.

## Where the file is

| | |
| --- | --- |
| Default | [`src/ftf/victim/contacts.json`](../src/ftf/victim/contacts.json), under version control |
| Another file | `FTF_VICTIM_CONTACTS=/path/to/contacts.json` in the environment or `.env`, or `ftf victim run … --contacts <file>` |
| Check what a run will use | `ftf victim personas` lists each persona's local and live contacts. `ftf victim run` prints the address and number before it opens the window. |

Keep the team's real mailbox domain and phone numbers in the committed file only if the team is happy for everyone with repository access to see them. Otherwise put them in a private copy and point `FTF_VICTIM_CONTACTS` at it.

## Format

```json
{
  "version": "2",
  "local": {
    "email":  { "domain": "victim.test", "pattern": "{persona}.{run_id}@{domain}" },
    "phones": { "retiree-income": "+48 500 000 101", "...": "one per persona" },
    "phones_confirmed_by": "not needed: local runs only"
  },
  "live": {
    "email":  { "provider": "mail.tm", "api": "https://api.mail.tm", "domain": "",
                "pattern": "{persona}.{run_id}@{domain}" },
    "phones": {},
    "phones_confirmed_by": ""
  }
}
```

| Field | Meaning |
| --- | --- |
| `version` | Change it whenever a value changes. Every run records the file and version it used (`run_files/run.json` → `contacts`). |
| `email.provider` | `static` (the default): addresses on `email.domain`, which the team must be able to receive. `mail.tm`: the run creates its own disposable inbox on [mail.tm](https://mail.tm) before the browser opens (see below). |
| `email.api` | For `mail.tm`: the API address, `https://api.mail.tm`. |
| `email.domain` | `static`: the domain. Empty means no address: a form that needs one is filled but not sent, and the run records `email_domain_unset`. `mail.tm`: empty takes the first active domain mail.tm offers; a value must be one of its domains. |
| `email.pattern` | How the address is built. Must contain `{run_id}` and `{domain}`, so every run gets its own address (VV-30), and a message that arrives later can be tied to the exact run, site and form. `{persona}` is the persona ID. |
| `phones` | One number per persona ID, as `+48` and 9 digits (spaces allowed). A persona with no number gets none: optional phone fields stay empty and a form that requires a phone stops the run with `phone_required`. |
| `phones_confirmed_by` | Who confirmed, and how, that the live numbers cannot belong to anyone, e.g. "UKE numbering plan, checked by X on 2026-11-23". **Live numbers are ignored while this is empty** (VV-31). |

The file is validated when it is read. A pattern without `{run_id}`, or a number that is not 9 Polish digits, stops the run before the browser opens.

## Which scope a run uses

The scope is decided by the run's start URL, not by a flag.

| Scope | When | What is given |
| --- | --- | --- |
| `local` | The URL is on this machine: `localhost`, `127.0.0.1`, `*.localhost`, `*.test`. This covers the case-61 replay (`ftf victim run replay:61`) and the test fixtures. | `<persona>.<run-id>@victim.test`, and the persona's fictional number. `.test` is reserved (RFC 2606) and can never receive mail. Local runs are confined to loopback, so nothing typed leaves the machine. |
| `live` | Any other URL (needs `--live`). | The `live` section: a new mail.tm inbox per run, `<persona>.<run-id>@<mail.tm domain>`. No phone numbers yet, so a form that requires a phone stops the run. |

The local numbers have the shape of real Polish mobiles so that phone widgets accept them. That is safe only because local runs never leave the machine: **never copy them into the `live` section.**

## How the values are used

- **E-mail fields** get the run's address. **Phone fields** get the national 9 digits, e.g. `500 000 101`, because the forms' country selector already supplies `+48`.
- Before a form is sent, every field is read back from the page. A form holding anything other than the persona's data, the run's address or its number is not sent (`foreign_value`). Phone widgets that reformat the number (`+48 500-000-101`) still match on the digits.
- A form that refuses the address or number is evidence (VV-32). The field, the error text and the validation are recorded, and the run stops at that form.
- Free-text chat answers never contain digit runs or e-mail addresses.
- The record of a run (`run_files/run.json`) holds the scope, address, number, file, version and the reason for anything not given. The form log (`run_files/forms.jsonl`) holds each value typed into each form.

## mail.tm inboxes (live runs)

Decided 2026-11-22: live runs get disposable mail.tm inboxes, not addresses on a team catch-all domain.

- **When:** at the start of a run whose scope has `"provider": "mail.tm"`, before the browser opens. The agent fetches mail.tm's active domains, then creates the account `<persona>.<run-id>@<domain>` with a random password (`GET /domains`, `POST /accounts`).
- **Recorded:** `run_files/mailbox.json` holds the address, the password, the account ID and the time it was created, so the inbox can be read later. The address is also in `run.json` and in the form log for every form it was typed into.
- **If it fails:** mail.tm may be unreachable, refuse the address, or rate-limit. The run then continues with no e-mail: a form that needs one is not sent, and `run.json` records why under `contacts.email_policy`.
- **Reading what arrives (VV-33):** run `ftf victim mail <run folder>`. It logs in (`POST /token`), fetches every message and saves each to `run_files/mail/<id>.json`. It adds one line per message to `run_files/mail.jsonl`: sender, sender domain, subject, links, link hosts and phone numbers. Then it re-ingests the run. Run it again later for mail that arrives later; messages already saved are skipped.
- **What it adds to the analysis:** on the stage whose form was given the address, the `victim.run` extractor records `mail.sender_domain` and `mail.link_domain` (A15), and `mail.phone` (A12). These are ordinary identifiers. A message to an address given to site A but sent from infrastructure seen in campaign B links the two (VV-33).
- **Things to know:**
  - mail.tm is a public disposable service: anyone with an inbox's address and password can read it, and `mailbox.json` holds both.
  - Some funnels refuse disposable domains. That refusal is evidence: the form log records the error, and the run stops at the form (VV-32).
  - mail.tm's domains change over time; the one used is recorded per run.
  - mail.tm asks clients to stay under about 8 requests a second; the client spaces its calls.
  - Local runs keep `victim.test`, because nothing there can reach a real inbox.

## Filling in the live section

1. E-mail is ready: mail.tm needs no setup. To use a team catch-all mailbox instead (requirements §13, question 2), set `live.email.provider` to `static` and put its domain in `live.email.domain`.
2. Once a range that cannot be assigned has been checked against the UKE numbering plan (question 1), add one number per persona to `live.phones`, and say who checked it, and how, in `live.phones_confirmed_by`.
3. Bump `version`, and check with `ftf victim personas`.
4. Live runs still need `--live`, and the mentor check (question 3) comes first.
