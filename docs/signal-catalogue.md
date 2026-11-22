# Follow the Fake — signal catalogue

Criteria and signals for detecting suspicious advertising material and for linking it into campaigns.
This catalogue is the input to the architecture in [architecture.md](architecture.md): every row is meant to become one entry in the extractor registry.

Sources: the challenge brief, [baseline-report.md](baseline-report.md), and a direct probe of `testing_sample/` on 2026-11-21. "Seen in" lists sample cases where the signal was actually observed; an empty cell means the signal is proposed but not yet observed in the sample.

## How to read the tables

**Purpose** — what the signal is good for. The brief grades linkage (60% of the score), not single-ad detection, so the two must not be mixed.

| Tag | Meaning |
| --- | --- |
| **L** | Linkage: helps decide that two materials are related. Drives clustering. |
| **R** | Risk: helps decide that one material is suspicious. Never drives clustering. |
| **B** | Both. |

**Tier** — how strong a link a match implies. Tiers map to the relation types the system outputs.

| Tier | A match means | Example | Relation it supports |
| --- | --- | --- | --- |
| **T1** identity | Same account or record in some system the operator controls | Campaign UUID, pixel ID | Same campaign / same operator account |
| **T2** artifact | Same or near-same piece of content | Image hash, text shingles | Duplicate / variant |
| **T3** kit | Same toolkit, template or tracker software | `window.aio` runtime, URL parameter schema | Same production family |
| **T4** playbook | Same way of persuading | Narrative skeleton, pattern tags | Same modus operandi |
| **T0** noise | Nothing; shared by unrelated sites | Cloudflare IP, jQuery hash | None — stoplisted |

**By** — who extracts it: `S` script (deterministic), `M` non-generative model (embedding, OCR, ASR, face), `V` vision-language model, `T` text LLM, `E` external enrichment source.

---

## A. Embedded identifiers (the strongest evidence in the sample)

Harvest from every location, not one regex: inline scripts, config blobs, hidden inputs, URL parameters, `_files/` file names, HAR request URLs and POST bodies. A single-location regex missed the pixel in cases 1, 36 and 47 during the probe.

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| A1 | Tracker-platform campaign / owner / flow / landing UUIDs (`window.aio`, comparable config objects) | L | T1 | S | 1, 2, 48, 61 |
| A2 | Template UUID and landing-type UUID in the same config | L | T3 | S | 48 = 61 |
| A3 | Facebook pixel ID (script init, `pxl=`, `fbpx=`, `pixel_fb=`, hidden `fb_pixel`, `_files/<id>`) | L | T1 | S | 1 = 2; 10, 14, 36, 47, 48 |
| A4 | Google Ads / GTM / GA IDs (`AW-`, `GTM-`, `G-`), conversion labels | L | T1 | S | 61 article = 61 chat |
| A5 | Other ad-network pixels: Taboola, Quora, Bing UET, Twitter, TikTok, Yandex Metrica | L | T1 | S | 48, 61 (presence; IDs to extract) |
| A6 | Affiliate-network fields: network, user/webmaster ID, offer ID, landing ID, `sub1..n`, `sid`, `x_id` | L | T1–T3 | S | 14 (`user_id=13`, `_trk=bf1`), 47 (`sub2=Yosha`) |
| A7 | Form endpoint host and path (`/lander/order.php`, `/v1/landing/form-event`) | L | T1–T3 | S | 47, 48 = 61 |
| A8 | Embedded public keys, API key IDs (`APPA-165`), encrypted blobs' static parts | L | T1 | S | 48 = 61 |
| A9 | Internal human-readable labels (`1038 \| PL - Brzoska Crime 2 - …`, `OPER / PL / Camp1`) | B | T1 | S | 1, 2, 48, 61 |
| A10 | Ad platform IDs in the landing URL (`ad_id`, `adset_name`, `utm_id`, account naming conventions such as `hk77_tv6_…`) | L | T1–T3 | S | 14, 36, 47 |
| A11 | Leaked secrets in URLs (ad-platform access tokens). Stored as found, like any other identifier (team decision 2026-11-21). | B | T1 | S | 36 |
| A12 | Contact identifiers: phone, e-mail, Telegram/WhatsApp handle, payment account, crypto wallet | L | T1 | S | — |
| A13 | Product / brand / offer name (invented names like "Świtowin Zyskarnia", "Orzeł Fundaris", "Trwały Działunek") | L | T1–T2 | S+T | 1, 2, 14, 47, 48, 61 |
| A14 | Registrable domain of the page (public-suffix aware), and host + landing path | L | T1 | S | `healthlinkq.pro` + same path in 2 and 3; `glowpixel.org` in all three 61 views; `ineptsolidoval.top` in both 14 variants |
| A15 | Domains the page sends the visitor or their data to: CTA link targets, form targets, click beacons | L | T1, rarity-weighted | S | 3 (`crimsonbase.pro`), 36 (`lerandbigdicker.com`) |

## B. Tracker-kit and URL fingerprints

These identify the *software* behind a page even when every ID differs.

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| B1 | Landing-URL parameter names, **after removing the ad platforms' standard ones** (`utm_source/medium/campaign/content/term/id`, `fbclid`, `ad_id`, `adset_name`…), which every Meta-sourced page shares | L | T3 | S | `utm_visit` in 1, 2, 48, 61 and nowhere else. Hypotheses only: `clickid` + `key`/`lpkey` in 3 and 14; `media_type` + `utm_placement` in 36 and 47 |
| B2 | Parameter value shapes (length, alphabet, UUID form) | L | T3 | S | In 1, 2, 48, 61 `utm_id` is a 20-character mixed-case token where Meta's is an 18-digit number, and `utm_visit` is a UUID. Hypothesis: 20-character lower-case `clickid` and a 42-character key in both 3 and 14 |
| B3 | Config-object key schema (set of JSON key paths in `window.aio` etc.) | L | T3 | S | 1, 2, 48, 61 |
| B4 | Hidden-form-field name set | L | T3 | S | 47 `{sid, sub1, sub2, x_id, fbtoken…}` |
| B5 | Lander path conventions (`<base href="…/landers/…">`, `/lander/<slug>/index.html`, `/L<digits>fl-…/`) | L | T3 | S | 36, 47; 2/healthlinkq = 3 |
| B6 | Own-domain API route shapes (`/api/v1/trigger/field/<uuid>/`, `/api/v1/agent/persist-session-record/`) | L | T3 | S | 1, 2, 48 |
| B7 | Named third-party tracker / cloaker / CRM hosts (non-generic): `crimsonbase.pro`, `shadowtracecore.com`, `showflashdaily.com`, `bf1.clickline.org`, `goods-crm.com`, `g3g.run` | L | T1–T3, rarity-weighted | S | 3, 14, 47, 48, 61 |
| B8 | Declared traffic source / cloaker name in config ("AIO FB CAPI via Cloakerson") | B | T3 | S | 48 |
| B9 | Session-recording or anti-bot scripts shipped by the kit | B | T3 | S | 1, 2 (`persist-session-record`) |

## C. Domain and hosting

Mostly risk signals. Almost every sample sits behind Cloudflare, so IP, ASN, issuer and "free SSL" carry no linkage weight.

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| C1 | Domain age at capture time (registration date vs first-seen date) | R | — | E (RDAP) | `glowpixel.org` registered 39 days before capture |
| C2 | Registrar | B | T0–T3 (weak) | E (RDAP) | NameSilo for `glowpixel.org` |
| C3 | Name-server pair (Cloudflare assigns a fixed pair per account) | L | T3 (weak–medium) | E (DNS) | pairs resolved for 61, 48 |
| C4 | TLD risk class (`.live .pro .xyz .top .icu .life` vs `.pl`) | R | — | S | all |
| C5 | Domain name shape: dictionary-word pairs, random pronounceable strings, hyphen+digits, entropy | B | T4 (weak) | S | `civicdaybook`, `acrosscommunities` (two-word); `meroporepufulo`, `shryxla-mynx` (generated) |
| C6 | Look-alike of a known brand: edit distance, homoglyphs, brand token in sub-domain or path | R | — | S | — |
| C7 | Domain name ↔ content topic mismatch (a "health" domain serving a news story) | R | — | S+T | `healthlinkq.pro` serving the Olejnik article |
| C8 | Certificate history: issuer, first-seen, SAN siblings, wildcard use. A certificate shared with another registrable domain links the two (`ct.shared_cert`) | B | T0–T3 | E (CT logs) | `glowpixel.org`: 54 certificates, sibling `a.glowpixel.org`; no shared certificate in the sample |
| C9 | Domain prior life: certificates or archive snapshots older than the current registration (re-registered / drop-caught domain) | R | — | E (CT, Wayback) | `glowpixel.org` has certificates from 2025-05-16, registered 2026-09-11: `ct.prior_life_days` = 483 |
| C10 | Server / proxy headers (`via: Caddy`, `server`, header-name order) | L | T3 (weak) | S (HAR) | Caddy in 2/healthlinkq and 36 |
| C11 | Origin IP / ASN when not behind a CDN | L | T3 | S (HAR), E | 47 video on BunnyCDN pull zone `vz-6110f311-389` |
| C12 | CDN pull-zone / storage bucket / video-library IDs | L | T1 | S | 47 (`b-cdn.net` zone + video GUID) |
| C13 | Domain currently dead, parked, or sinkholed | R | — | E (DNS) | `civicdaybook.live` no longer resolves to its host |
| C14 | Free or short-lived DV certificate, no organisation in subject | R (weak) | T0 | E | all |
| C15 | Shared CDN IP, common library host, Google/Facebook hosts | — | T0 | — | stoplist |

## D. Page structure, template and code

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| D1 | DOM skeleton fingerprint (tag-path shingles, MinHash) with text removed | L | T3 | S | — |
| D2 | CSS class-name set (MinHash), after removing framework and translator classes | L | T3 | S | 1 = 2; 48 ≈ 61 (`aio-sdk-*`) |
| D3 | Exact hash of first-party CSS/JS | L | T2–T3 | S | `main.css` in 1, 2, 61; `main.js` 1 = 2 |
| D4 | Fuzzy hash of first-party JS/CSS (ssdeep/TLSH) for edited copies | L | T3 | S | — |
| D5 | Developer comments and their language | L | T3 | S | Russian comments in 14; section-banner style in 3 and 36 |
| D6 | Leftover text in other languages (localisation residue), form `theme` values | L | T3 | S+T | 14 (Ukrainian, French, Dutch) |
| D7 | Template residue: title, meta description or HTML comments that contradict the body | B | T3 | S+T | 48 = 61 title; 61 comment names a different celebrity than the body |
| D8 | Cloned publisher markup: class names and comments copied from the real site (`DFP-…`, `portal-chrome`) | B | T3 | S | 1, 2, 48 |
| D9 | Navigation links that go nowhere (`#`, same page, or all to the offer) | R | — | S | — |
| D10 | Share of outbound links pointing to one conversion target | R | — | S | — |
| D11 | Scripts with capability unrelated to the page (credential overlay on `/admin`, exfiltration host) | B | T1 for the host | S+T | 36 (`lerandbigdicker.com`) |
| D12 | PWA / fake-install machinery (manifest, service worker, install script, back-button hijack) | B | T3 | S | 10 |
| D13 | Capture pollution to strip before extraction (browser translate widgets, extension markup) | — | — | S | 10 |
| D14 | `lang` attribute vs detected language | R | — | S | 61 chat (`en` on a Polish page) |

## E. Assets and visual layer

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| E1 | Exact hash of content images (not logos, not icons) | L | T2 | S | 11 images 1 = 2; `news1–5.webp` 48 = 61 |
| E2 | Perceptual hash of images (colour-invariant: computed on grayscale so `hue-rotate` does not break it) | L | T2 | S | — |
| E3 | Image embedding (CLIP/SigLIP/DINOv2) for crops, re-encodes, re-compositions | L | T2–T4 | M | — |
| E4 | Screenshot layout embedding of the whole page / ad | L | T3–T4 | M | — |
| E5 | Asset file-naming convention (`01-hero.webp`, `shot-pko.webp`, `ava-coment10.jpeg`, `news1.webp`) | L | T3 | S | 1, 2, 14, 48, 61 |
| E6 | Image metadata: EXIF, software tag, dimensions set, AI-generation markers (C2PA, known generator signatures) | B | T3 | S | — |
| E7 | Faces: identity against a small reference set of frequently abused public figures | B | T4 (entity), T2 if same crop | M | 1, 2, 3, 10, 48, 61 |
| E8 | Same face *crop* reused (embedding near-identity + pHash), stronger than "same person" | L | T2 | M | — |
| E9 | Logos: publisher, bank, regulator, app-store, TV-station marks | B | T4 | M/V | Gazeta.pl, TVN24, App Store, PKO, Pekao |
| E10 | Depicted "proof" objects: bank-transfer screenshot, balance/profit screen, cheque, certificate, lab coat | B | T4 | V | 1, 2 (`shot-pko`, `shot-pekao`) |
| E11 | OCR text from screenshots and creatives (advertiser name, CTA, counters, captions) | B | T2–T4 | M/V | all screenshot cases |
| E12 | Avatar set of fake commenters (hash set) | L | T2 | S | 14 |
| E13 | Visual-evasion filter applied to the page (`filter: hue-rotate(Ndeg)` on `<html>`). The angle varies per page, so the signal is the filter's presence, not its value. | B | T3 | S | 7° in 1, 2; 8° in 48, 61 article; 6° in 61 chat |

## F. Text and narrative

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| F1 | Exact / near-duplicate body text (word 5-gram shingles, MinHash or SimHash) on boilerplate-stripped article text | L | T2 | S | 1 = 2 (0.99); 2/healthlinkq = 3 |
| F2 | Semantic embedding of the article body | L | T2–T4 | M | — |
| F3 | **Entity-masked narrative embedding**: persons, organisations, products, amounts and dates replaced by role placeholders before embedding, so rewrites around a different celebrity still align | L | T4 | T+M | expected: 1/2 ↔ 3 ↔ 48 |
| F4 | Narrative skeleton as structured fields: trigger event, victim/hero, villain, revealed "secret", offer, proof, call to action | L | T4 | T | 1, 2, 3, 48, 61 |
| F5 | Headline formula ("N minut po emisji…", "«quote»: event") | L | T3–T4 | S+T | 48 = 61 |
| F6 | Comment / testimonial block: commenter names, texts, like counts, timestamps pattern | L | T2–T3 | S | 14, 47, 10 |
| F7 | Numeric claims: entry amount, promised return, price, discount, stock left, time window | L | T2–T4 | S+T | 1,150 zł → 1,800 zł (61); 50% and "17 left" (14, 47) |
| F8 | Stylometry: punctuation habits (« » quotes, em-dashes), sentence length, machine-translation artefacts | L | T3–T4 | S | « » in 1, 2, 3 |
| F9 | Fabricated dates that auto-refresh (script-written "today") | B | T3 | S | — |
| F10 | Language and target country | L | T4 (weak) | S | PL throughout |

## G. Entities and impersonation

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| G1 | Named public figures **with role** (victim, endorser, accuser, interviewer, "developer") | L | T4 | T | Brzoska is protagonist in 1/2, supporting endorser in 48 |
| G2 | Impersonated publisher / brand (claimed in title, logo, layout) vs actual host | R | — | S+V | Gazeta.pl, TVN24, App Store |
| G3 | Institutions invoked for legitimacy (KNF, ZUS, NBP, banks, ministries, hospitals) | B | T4 | T | 1, 2, 3, 61 |
| G4 | Advertiser page name on the ad, and its relation to the content | B | T1 if repeated | V/S | Formal Favorable, EasyJoy, Sustain Studio, Glowpixel… |
| G5 | Fake staff / persona names (chat agent "Sara", journalist bylines, doctors) | L | T2–T3 | T | 61, 1, 2 |

## H. Persuasion patterns (modus operandi)

Patterns are discovered by the model from the material (architecture §6); nothing in this table is given to it. These are the techniques a manual reading of the sample found. They are the yardstick: discovery run on the sample should recover them unprompted.

| # | Technique found by manual reading | Evidence comes from | Seen in |
| --- | --- | --- | --- |
| H1 | Celebrity presented as endorser / user / founder | T+V | 1, 2, 3, 10, 48, 61 |
| H2 | Fake news-article framing | T+V | 1, 2, 3, 48, 61 |
| H3 | Suppression story ("recording deleted", "they tried to silence") | T | 1, 2, 3, 48 |
| H4 | Violence or arrest as hook | T | 1, 2, 3, 48 |
| H5 | "Banks / elites hide this from you" | T | 1, 2, 3, 48 |
| H6 | Profit / payout proof image | V | 1, 2, 10 |
| H7 | Regulator or state backing claimed | T | 61 |
| H8 | Authority figure in uniform (doctor, priest, presenter) | V | 5, 14, 47 |
| H9 | Scarcity counter ("17 left", "99 places") | S+T | 14, 36, 47 |
| H10 | Countdown timer / deadline | S | 14, 47 |
| H11 | Deep discount with crossed-out price | S | 14, 47 |
| H12 | Embedded fake social comments | S+T | 1, 2, 3, 10, 14, 47, 48, 61 |
| H13 | Gamified discount (wheel, doors, boxes) | S+V | 14 |
| H14 | Chat-bot / quiz qualification before the form | S+T | 61 |
| H15 | Callback promise ("a manager will call you") | T | 1, 2, 14, 47, 61 |
| H16 | Miracle health claim / fear of illness | T | 5, 14, 47 |
| H17 | Marketplace or app-store imitation | V+S | 10 |
| H18 | Video sales letter with forced-sound prompt | S+V | 14, 47 |
| H19 | Low entry threshold stated as exact amount | T | 61 |

All of group H: **Purpose B, Tier T4.** Combinations the discovery step is expected to surface on its own: a celebrity, a fake news article and a profit image appearing together (H1, H2, H6 or H19); a health-product video offer with scarcity, discount and a callback (H16, H18, H9, H11, H15).

## I. Funnel and conversion mechanics

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| I1 | Funnel shape as a stage sequence (ad → advertorial → chat → lead form → callback) | L | T4 | S+T | 61 |
| I2 | Lead-form field set and order, validation, phone-country widget | L | T3 | S | 14, 47, 48, 61 |
| I3 | Where the data goes: form action host, XHR endpoints, CRM host | L | T1–T3 | S | 47, 48, 61 |
| I4 | Stored navigation state naming other landings | L | T1 | S | 61 chat lists the article's landing UUID |
| I5 | Redirect chain and its hop hosts (HTTP, meta-refresh, JS) | L | T1–T3 | S (HAR) | none captured cross-case |
| I6 | Payment / deposit method named | B | T4 | T | 1, 2 |
| I7 | Video hosting and player (HLS on own domain, BunnyCDN, shaka, video.js) | L | T3 | S | 14, 47 |
| I8 | Push-notification subscription prompts and their provider | B | T3 | S | 14 (`cdn.puuuuuush-king.org`) |

## J. Evasion and cloaking

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| J1 | Same URL returns different content across captures (white page vs offer) | B | — | S | 61 (article / chat / business portal) |
| J2 | White-page traits: obfuscated hash-like class names, generic business copy, stock imagery, no trackers | B | T3 | S+T | 61 portal page (`a1a02dd7`-style classes) |
| J3 | Ad creative ↔ landing semantic mismatch | B | T4 | M/T | 10 (bath rug vs casino), 36 (politics vs conference) |
| J4 | Ad-library creative ↔ delivered creative mismatch | B | — | V | 10 |
| J5 | Referrer / parameter gating (page requires `fbclid` or a key to show the offer) | B | T3 | S, E (controlled re-fetch) | hypothesised for 61, 36 |
| J6 | Visual-hash evasion (E13), text-as-image, zero-width characters, homoglyph text | B | T3 | S | E13 seen |
| J7 | Stale decoy content (event dates already past at capture) | R | — | S+T | 36 |

## K. Expected-but-absent elements

The expected elements are generated by the model per kind of page (architecture §6); this table is not given to it and serves as the yardstick for what the generated profiles should contain. Evaluated on **visible text and links only**. Each check returns `present`, `absent`, or `not observable` (for example, a screenshot-only case). Only `absent` counts as a signal. The resulting vector is also a fingerprint: operators who reuse a kit omit the same things.

| Vertical | Must normally be present | By |
| --- | --- | --- |
| Investment / trading | Legal entity name and registered address; KNF authorisation or registry reference; risk warning; terms and privacy policy; data-controller clause and consent next to the form | S+T, E (KNF lists) |
| Supplement / medical product | Mandatory "suplement diety" or medicine-leaflet notice; manufacturer or distributor; ingredient list; seller identity; withdrawal and returns terms; lowest-30-day-price note next to a discount | S+T |
| Gambling | Polish licence holder; 18+ and responsible-gambling notice; operator identity | S+T, E (Ministry of Finance register of prohibited gambling domains) |
| News article | Host equals the publisher's real domain; working section navigation; imprint; author page; canonical / `og:url` on the publisher domain | S, E (publisher lookup) |
| App-store listing | Host is the real store domain; real developer page | S |
| Event / conference | Organiser legal entity; venue; ticket terms; dates in the future | S+T |
| Any page with a form | Privacy policy link; controller identity; consent checkbox; cookie notice | S |
| Any ad | Advertiser identity consistent with the destination | V+S |

**Purpose B, Tier T3–T4** (as a vector); individual absences are **R**.

## L. Ad layer

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| L1 | Ad-library ID, page ID, start date, platforms, active status | L | T1 | S, E | 5 (`1982065065827084`) |
| L2 | Ad primary text, headline, CTA button label | L | T2–T4 | V (OCR) | 1, 3, 36, 48, 61 |
| L3 | Creative image / video perceptual fingerprint | L | T2 | S/M | — |
| L4 | Placement and device hints (Instagram feed, iOS in-app browser) | L | T0–T4 | S | 10, 14, 47 |
| L5 | Ad account naming conventions in URL parameters | L | T3 | S | 14 (`1208_pl_w_tvtextdc3_…`), 47 (`PL78T`) |
| L6 | Number of concurrent ads / pages running the same creative | B | — | E (ad library) | — |

## M. Video and audio

| # | Signal | Purpose | Tier | By | Seen in |
| --- | --- | --- | --- | --- | --- |
| M1 | Key-frame perceptual hashes (scene-change sampled) | L | T2 | S | 5, 14, 47 |
| M2 | Speech transcript → all text signals (F, G, H) | B | T2–T4 | M (ASR) | 5, 14, 47 |
| M3 | Audio fingerprint (same voice-over under different visuals) | L | T2 | M | — |
| M4 | On-screen text and CTA frame (OCR) | B | T2–T4 | M/V | 5 |
| M5 | Container metadata: encoder string, duration, resolution, creation time | L | T3 (weak) | S (ffprobe) | 5, 14, 47 |
| M6 | Lip-sync / synthetic-voice indicators | R | — | M | — (out of MVP scope) |
| M7 | HLS playlist structure and segment host | L | T3 | S | 14, 47 |

## N. External verification and enrichment

All optional, cached, and timestamped. A failed lookup yields `not observable`, never a negative.

| # | Check | Source | Status on 2026-11-21 |
| --- | --- | --- | --- |
| N1 | Domain registration date, registrar, name servers | RDAP (IANA bootstrap) | In MVP. Confirmed 2026-11-21 for every sample TLD: `.live .pro .life` (Identity Digital), `.xyz .icu` (CentralNic), `.top` (ZDNS), `.org` (PIR), `.com` (Verisign) |
| N2 | Certificate history, sibling host names | crt.sh (CT logs) | In MVP (`enrich/ct.py`). Responded, JSON, 2026-11-22: 0.5–60 s per domain, intermittent 502, 404, timeouts, and `[]` for a domain that has certificates (none of them trusted or cached as a fact) |
| N3 | Domain on the national phishing warning list | CERT Polska list (`hole.cert.pl`) | In MVP. Responded, JSON (~22 MB) and plain text; downloaded once and matched locally |
| N4 | Entity on the financial regulator's public warning list | KNF | In MVP. Public page responded and parses as tables, about 680 rows |
| N5 | Domain in the register of prohibited gambling sites | Ministry of Finance | In MVP from a manual snapshot. Scripted requests were refused by a bot check |
| N5b | Product in the supplement notification register | Sanitary inspectorate | In MVP if an export exists; address redirects to a portal, search not tested |
| N6 | Is the "news article" really published by the claimed outlet? | Publisher site search / sitemap; headline and quote lookup | Not tested; best-effort |
| N7 | Is the ad still running; what else does the page run? | Meta Ad Library, Google Ads Transparency Center | Not tested; official APIs need verified access |
| N8 | Other sightings of the same pixel ID, hash, or page title | urlscan.io search | Not tested |
| N9 | Earlier content on the same domain | Wayback Machine | Not tested |
| N10 | Domain popularity / traffic rank | Tranco or similar public top lists | Not tested |
| N11 | Pivot on named third-party hosts (B7): their own registration, certificates, siblings | N1–N2 on those hosts | Possible with N1–N2 |
| N12 | Live re-fetch under controlled conditions (with/without referrer, mobile/desktop) to test cloaking | Headless browser | Not tested; domains are short-lived |

## O. Temporal

| # | Signal | Purpose | Tier | By |
| --- | --- | --- | --- | --- |
| O1 | Capture time, ad start time, domain registration time, certificate issue time — kept as separate fields | B | — | S, E |
| O2 | Burst pattern: several domains registered or certified within hours | L | T3 | E |
| O3 | Campaign lifetime and domain rotation interval | R | — | derived |

## Stoplist (never link on these)

Shared CDN IPs and ASNs; Cloudflare itself; Google, Facebook, jsDelivr, cdnjs, unpkg, Google Fonts hosts; unmodified public libraries (jQuery, SDK files) by hash; browser "saved resource" stubs; publisher logos taken from the impersonated outlet; unresolved template macros (`{{aio.visit…}}`, `{lang}`); values that are placeholders or empty. The stoplist is seeded by hand and then maintained automatically by document frequency: anything appearing in a large share of unrelated incidents loses its weight.
