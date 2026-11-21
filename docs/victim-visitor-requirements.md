# Victim visitor — requirements

Status: draft, 2026-11-21. Companion to [architecture.md](architecture.md) and [signal-catalogue.md](signal-catalogue.md).

## 1. What it is and why

The victim visitor is a browser agent that goes through a scam funnel the way a targeted person would. It has a persona, arrives from the ad, reads, scrolls, watches, answers the quiz and the chat bot, and leaves its contact details when asked. It records everything it meets on the way.

It produces two things nothing else in the tool produces:

1. **The whole funnel as evidence.** Today the tool sees one page per capture and has to guess how an ad, an article, a chat page and a "thank you" page fit together. The baseline report marks every downstream step as unverified: "no downstream deposit/callback verified", "order success not established". The victim walks the funnel, so every stage becomes a view, and every link between stages is proven by the navigation that produced it. Stages reached only after a form (thank-you page, broker hand-off, CRM endpoint, follow-up e-mail) are new identifiers that link campaigns.
2. **A perception fingerprint.** After the walk, the model answers, as this persona, *what are they trying to convince me of, and what do they want me to do?* The answer has a fixed structure with quoted evidence, and becomes comparable fingerprint layers. Two campaigns with different domains, templates and celebrities can still be making the same pitch to the same kind of person. That is the playbook level of grouping, measured from the victim's side instead of the page's.

## 2. Decisions taken (2026-11-21)

| Question | Answer | Consequence |
| --- | --- | --- |
| How far the victim goes | Submit lead forms; never pay | It answers quizzes and chat bots and submits name / e-mail / phone forms. It stops at any payment, deposit, card, bank, ID-document or app-install step. |
| Contact details it gives | A team-owned e-mail only | One catch-all mailbox the team controls, with a unique address per run. Phone fields get a number that cannot belong to anyone (VV-31). No real person's data is ever entered. |
| Where personas come from | A small fixed library | Four hand-written personas, stored as data and versioned, so runs are comparable. |
| Browser | Always visible to the user | The agent drives a real browser window on the user's screen; the user watches every step and can stop the run or step in (VV-26). It runs on the host machine, not headless in the container. |
| Place in the build | Headline demo, narrow scope | One persona per run, one funnel walked end to end on a live URL, shown in the demo as the distinctive feature. The rest of the plan is unchanged. |

This reverses one rule in the current design for victim runs only: [architecture.md §4](architecture.md) says capture never types or submits. Plain capture keeps that rule; the victim visitor is a separate, explicit mode.

## 3. Scope

**In the hackathon build (must)**

- Persona library with four personas; the demo uses one per run.
- An agent that walks one funnel from the ad link to the last reachable stage, within the action rules of §6.
- A session record: every stage, every action, the full network log, form submissions and their responses.
- The run ingested as one incident whose views are joined by proven `funnel` edges.
- The perception fingerprint for the run, with quoted evidence verified by code.
- A command and an interface page to start a run and watch it.

**Should, if time allows**

- Reading the team mailbox and attributing follow-up e-mails to the run that caused them.
- Comparing two personas on the same URL.
- Starting a run straight from an ad found by discovery (`src/ftf/discovery/`).

**Later**

- Several personas per URL as standard, to detect persona-dependent content.
- A phone line for callbacks and SMS.
- Scheduled re-visits of known campaigns.

## 4. Personas

**VV-01** A persona is a data file (YAML or JSON) under version control, with a stable ID and version. Changing a persona changes its version, and every run records the version it used.

**VV-02** Fields:

| Group | Fields | Used for |
| --- | --- | --- |
| Identity (synthetic) | first name, surname, age, sex, town, voivodeship | Filling forms; how the agent speaks in chats |
| Situation | occupation or retired, rough monthly income, savings band, family, health worries | Quiz and chat answers ("how much can you invest?", "how long have you had symptoms?") |
| Interests and fears | e.g. pension worries, distrust of banks, wants to lose weight, follows a named TV presenter | Which link it clicks; what it finds persuasive |
| Digital habits | device (phone model or desktop), browser, platform it came from (Facebook, Instagram), how carefully it reads, typing speed | Browser profile, viewport, referrer, pacing |
| Disposition | trusting / sceptical, how fast it gives in to urgency | Agent decisions at choice points; the "would I be convinced" part of the perception |
| Locale | `pl-PL`, `Europe/Warsaw` | Browser settings |

**VV-03** Personas are synthetic. They must not be based on a real person, and the identity fields must not be combined into anything that points at one.

**VV-04** First library, matched to the audiences seen in the sample:

| ID | Persona | Matches |
| --- | --- | --- |
| `retiree-income` | 67, retired, small town, worried about pension, watches TV news | Fake-news investment funnels (cases 1, 2, 3, 48, 61) |
| `weight-loss` | 44, woman, tried diets, active on Instagram | Health offers like case 14 |
| `young-gambler` | 24, man, phone only, follows football stars | Casino and app-store imitations like case 10 |
| `small-saver` | 39, employed, some savings, curious about crypto | Investment and chat funnels |

**VV-05** The analyst chooses the persona; if none is chosen, the tool suggests one from the ad's text and imagery, and says why.

## 5. Behaviour

**VV-10 Arrival.** The run starts from the ad's original link, query string included (`fbclid`, `utm_*`), with the referrer and device of the persona's platform. Cloakers check these, so starting from the bare domain often shows only the decoy page.

**VV-11 Loop.** At each step the agent:

1. observes the page: visible text, a screenshot, and a numbered list of things it can do (links, buttons, form fields, chat options, video player);
2. decides one action, as the persona, towards the persona's goal ("find out how to get this money", "find out how to order");
3. acts, waits for the page to settle, and records the step.

**VV-12 Driver.** Recommended: the page is turned into a numbered list of candidate actions by script, and the model only picks a number and, for text inputs, the text. This is cheaper, easier to audit and harder to misuse than letting the model click screen coordinates. The model call goes through the existing gateway and cache.

**VV-13 Human pacing.** The agent reads at the persona's pace, scrolls, plays videos for a while, and waits before acting. Funnels in the sample measure time on page and scroll depth (case 61 stores both) and may hold the next stage back until these are met.

**VV-14 Choice points.** In quizzes and chat bots, answers come from the persona's situation, consistently through the run. If asked something the persona does not cover, the agent gives a plausible answer and records that it improvised.

**VV-15 Stopping.** The run ends when any of these happens, and records which:

- a forbidden step is reached (§6) — the normal end of a full funnel;
- the funnel says the next step happens off-site ("a consultant will call you");
- no new stage appears after a set number of steps;
- a step, time or page budget runs out (defaults: 40 actions, 10 minutes, 15 distinct pages);
- the site blocks the agent, or a CAPTCHA or login appears and nobody handles it (VV-24).

## 6. What it may and may not do

**VV-20 Allowed:** navigate, scroll, wait, play and pause media, click links and buttons within the funnel, answer quizzes and chat bots, type persona data into lead forms and submit them, close pop-ups.

**VV-21 Forbidden, always — the run stops at the step and records what was asked:**

- any payment, deposit, card, bank-account, BLIK or crypto-wallet entry;
- uploading or photographing an ID document, a selfie or any file;
- downloading or installing anything, including "install the app" and web-app install prompts (case 10);
- granting browser permissions: notifications (case 14's push prompt), camera, microphone, location, clipboard — all denied, and the request recorded;
- logging in with, or connecting, any real account ("continue with Facebook / Google");
- entering any data that is not the persona's.

**VV-22 Scope of links.** It follows the funnel: links, redirects and form targets that lead onward. It does not open unrelated outbound links (share buttons, the real publisher's site, legal pages of real companies), except to read a page's own terms or privacy page when the perception needs it.

**VV-23 Isolation.** One fresh browser context per run; no stored cookies or profiles; one run at a time. The browser runs on the host machine in a visible window (VV-26), not inside the container.

**VV-24 CAPTCHAs and checks.** The agent never solves or bypasses a CAPTCHA. As in discovery, the run pauses, brings the window to the front and says what is needed; the user handles it in the window and the run continues on its own. If nobody acts within the wait limit (10 minutes by default), the run stops and records it.

**VV-25 Audit.** Every action, typed value and submitted payload is logged with a timestamp and a screenshot, so a person can see exactly what the agent did on someone else's site.

**VV-26 Visible browser.** The agent always drives a browser window the user can see; there is no headless mode for victim runs. Alongside the window, the user sees the current step, the action about to be taken and the agent's one-line reason. The user can pause, resume or stop the run at any moment; stopping ends the run cleanly and keeps everything recorded so far. While paused, the user may act in the window themselves (for example to get past a check), and those actions are logged as the user's, not the agent's.

## 7. Contact details

**VV-30 E-mail.** Each run gets its own address on the team's catch-all mailbox, e.g. `<persona>.<run-id>@<team-domain>`. The address is recorded with the run, so any message that arrives later can be tied to the exact site and form that received it.

**VV-31 Phone.** When a form asks for a phone number, the agent uses a number that cannot be assigned to any subscriber under the Polish numbering plan. *Open:* the exact range has to be checked against the regulator's (UKE) numbering plan before the first live run; until then the agent leaves optional phone fields empty and stops at forms that require one, recording "phone required".

**VV-32 Rejection is evidence.** If a form refuses the e-mail or phone, the agent records the field, the error text and any validation it can see (client-side pattern, server response), and stops at that form.

**VV-33 Follow-up (should).** A reader polls the mailbox (IMAP) and, for each message, records the receiving address, sender, sending domain, links, phone numbers and offer names. These become observations on the run. A message arriving at an address given to site A, but sent from infrastructure seen in campaign B, links the two.

## 8. What a run records

**VV-40** One run becomes one incident, `victim-<run-id>/`, in the bundle layout ingestion already reads, so it goes through the normal pipeline.

| Record | Content |
| --- | --- |
| Run manifest | Persona ID and version, start URL, referrer, device, egress, start and end time, stop reason, budgets, tool version |
| Stages | One page view per distinct page reached: DOM, screenshot, resources, URL |
| Steps | Ordered log: observation summary, candidate actions offered, action chosen and why (one line), typed value, URL before and after, screenshot |
| Network | One HAR for the whole session, bodies included |
| Navigation | Every hop between stages and how it happened: link click, form submit, HTTP redirect, script redirect, chat hand-off |
| Forms | For each form: fields asked, values given, where it was sent, the response, and what came next |
| Chat transcript | Every bot message and every answer, in order (case 61's "Sara") |
| Blocks | Forbidden steps reached and what they asked for; permissions requested; CAPTCHAs |
| Follow-up | Messages received at the run's address (VV-33) |

**VV-41** Edges between the run's views are `funnel` edges with their proof attached — the navigation record — so the Method track does not have to infer them.

**VV-42** New identifiers from later stages are ordinary observations with their signal IDs: form endpoints and CRM hosts (A7, I3), redirect-hop and hand-off domains (I5, A15), thank-you page domain, broker or casino platform domain, follow-up sender domains. These are identity-tier and drive campaign linking like any other.

## 9. The perception fingerprint

**VV-50** After the run, the model receives the persona, every stage's text and screenshots, the chat transcript and the form log, and answers as the persona. Every item must carry evidence: a quote that code then finds in the recorded material, or a screenshot reference. Items whose evidence cannot be found are dropped, as in the rest of the tool.

**VV-51** Fixed structure ("what are they trying to convince me of?"):

| Field | Question it answers | Example from the sample |
| --- | --- | --- |
| Asked action | What do they want me to do, and in what order? | Leave my phone; deposit; wait for a call |
| Asked data | What did they ask me to give, stage by stage? | Name → e-mail → phone |
| Money path | What would it cost me, when, how? | First deposit 1,150 zł |
| Promise | What do I get, how much, how fast? | Up to 1,800 zł profit within days |
| Belief | What do they want me to believe is true? | A famous businessman was attacked for revealing this |
| Borrowed authority | Whose name, face or logo vouches for it? | A TV station, a bank, the financial regulator |
| Levers | What feelings do they press? | Fear of missing out, outrage at banks, urgency, scarcity, health fear |
| Doubt handling | How do they answer my doubts in advance? | "This is not a scam", "regulated", testimonials |
| Tailoring | What in this felt aimed at someone like me? | Pension worries, age, region |
| Next step promised | What happens after I submit? | A manager will call within 24 hours |
| Would I fall for it | As this persona: convinced, unsure or not, and the decisive reason | — |

**VV-52** Fingerprint layers built from it (all registered like any other layer; see architecture §8):

| Layer | Kind | Built from | Tier |
| --- | --- | --- | --- |
| `victim.ask` | set | asked actions and asked data fields, normalised | Playbook |
| `victim.money` | small vector | amounts, currency, time frames (log-scaled) | Playbook |
| `victim.pitch` | vector | embedding of a short canonical retelling of the pitch, with names, brands and amounts masked | Playbook |
| `victim.levers` | vector | levers matched against the model-discovered pattern library (architecture §6) | Playbook |
| `funnel.shape` | sequence | stage kinds in order (ad → advertorial → quiz → chat → form → thank-you → hand-off) compared by edit distance | Kit / playbook |

**VV-53** Perception layers are playbook-tier: they can never merge two campaigns on their own. The identity-tier identifiers the run uncovers (VV-42) are what links campaigns.

**VV-54** The perception is tied to its persona. Runs with different personas are compared field by field, not as one blended score; a difference between personas on the same URL is itself recorded (persona-dependent content).

## 10. Interface

**VV-60** Command: `ftf victim run <url> [--persona <id>] [--max-steps N]` (always opens a visible browser window); `ftf victim personas` lists the library.

**VV-61** Interface page: choose a URL (or an ad from discovery) and a persona; watch the run step by step (screenshot, action, reason); see the funnel as a sequence of stages with the proof under each arrow; read the perception with its quotes; see what the run linked to.

**VV-62** The run is ingested and compared automatically when it ends; the match view shows which campaign it joined and through which stage's identifiers.

## 11. Demo acceptance

The demo succeeds if, on one live scam URL found that day (discovery can supply one):

1. the victim, as one persona, reaches at least one stage past the first page — a quiz, chat or form result — and stops at a forbidden step or an off-site hand-off;
2. the run shows as a chain of stages with the navigation proof for each step;
3. at least one identifier from a later stage (form endpoint, hand-off domain or thank-you domain) appears that a single-page capture would not have found;
4. the perception fills every field it can, every quote is found in the recorded material, and each empty field says why;
5. the run is matched against the corpus with a typed outcome and its evidence.

Fallback if no live funnel is reachable: run against a locally served copy of a sample funnel (case 61's article and chat pages), saying plainly that it is a replay.

## 12. Fit with the brief

| Brief criterion | What the victim visitor adds |
| --- | --- |
| Correlation and grouping (30%) | Identifiers from stages no single capture reaches; proven funnel edges instead of inferred ones; a new playbook-level fingerprint |
| Features and data sources (30%) | A new source — the funnel as experienced — and the perception layers |
| End-to-end completeness (15%) | URL in, whole funnel, verdict out |
| Interpretability (15%) | The step log and screenshots show exactly how each stage was reached; the perception says, with quotes, what the pitch is |
| Deployment potential (10%) | Persona library and action rules are data; the driver sits behind the model gateway and can change model |

It also answers open questions from the baseline report: which forms lead where, what happens after the lead form, and whether one domain shows different content to different visitors.

## 13. Open questions

1. **Phone number range** (VV-31): which range is safe under the Polish numbering plan. Blocks phone-required forms.
2. **Mailbox:** which domain and catch-all mailbox the team will use, and who sets it up.
3. **Mentor check:** confirm with the NASK mentors that submitting synthetic leads to live scam sites is acceptable for the demo.
4. **Owner:** who builds it. It touches capture (Evidence track) and perception (Perception track).
5. **Docker:** the victim needs a screen, so it runs on the host, not in the container. The rest of the tool stays in Docker and ingests the run's output from a shared folder. Confirm with the mentors that this split is acceptable for judging.
