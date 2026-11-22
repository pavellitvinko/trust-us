# Observation namespaces the Method track reads

For the Evidence and Perception tracks. A layer only sees observations whose `namespace`
is in its list; a value emitted under another name links nothing. Values are compared
within one namespace only, so the same number as a Facebook pixel and as a Google Ads ID
never matches. Emit `state=present` with a normalised `value`; keep the raw form in `raw`.
Observations with `purpose=risk` never enter a fingerprint.

Catalogue column = row in `docs/signal-catalogue.md`. Examples are from the sample.

**Accepted aliases (2026-11-21).** The Evidence and Perception tracks landed with some names
of their own; the layers read both, so nobody has to rename. Fixture name → also read as:
`campaign.label` → `aio.campaign_name` · `landing.label` → `aio.landing_name` · `brand.offer` →
`offer.name` · `tracker.host` → `third_party.host` · `api.route_shape` → `api.route` ·
`traffic_source` → `kit.cloaker`, `aio.source_name` · `url.path_convention` → `url.path_shape` ·
`form.field` → `form.hidden_name` · `text.body` → `page.body_text` · `entity.cast` →
`page.entity.person` / `page.entity.organisation` / `page.entity.persona` (`{"name", "role"}`) ·
`ocr.domain` → `image.domain` · `ocr.brand` → `image.brand_offer` · `ocr.person` →
`image.person_name` · `ad.id` → `ad.ad_id`. Also consumed: `aio.source_uuid`, `aio.folder_uuid`,
`aio.campaign_human_id`, `aio.landing_human_id` (identity, campaign group); `ad.campaign_id`,
`ad.adset_id`, `ad.account_label`, `gads_conversion`, `bing_uet`, `quora_pixel` (identity,
tracking group); `aff.*` as a prefix, `public_key`, `push_public_key` (identity, conversion
group); `url.host` (domain group); `aio.folder_name` (kit config); `http.via`, `http.powered_by`
(infra). Text layers prefer `page.body_text`, then legacy `text.body`, then
`page.clean_text` when no body observation exists. They select one representation rather
than concatenate body text with navigation, comments and widgets. `kit.dom` expands the
`dom.class_set` list into individual classes and still accepts scalar `dom.class` rows.
`kit.code` also reads `dom.comment_lang` and `dom.evasion_filter` at the corresponding
kit weights. Risk-only markup facts remain excluded from linkage.

`nav.landing_uuid` is extracted from UUID keys whose flag is `true` in
`window.aio.landed_on_landings_uuids`. No engagement times or visitor data are emitted.
A downstream capture that remembers another page's landing supports an edge from that
page to the downstream capture, independent of view-ID ordering. Its own landing alone
does not establish a transition. The stored state supports navigation, not an independently
verified HTTP redirect or exact visit sequence.

Still missing on the sample and read from fixtures only: `asset.sha256`, `image.phash`,
`creative.phash`, `video.frame_phash`, `dom.class`, `firstparty.sha256`, `code.evasion_filter`,
`expect.element`. `pattern.score` is written by `ftf patterns discover`.

## Identity tier (T1): supports *same campaign*

| Layer | Namespace | Value shape | Catalogue | Example |
| --- | --- | --- | --- | --- |
| `ids.domain` | `domain.registrable` | public-suffix-aware registrable domain, lower-case | A14 | `healthlinkq.pro` |
| | `url.host_path` | host + landing path without query, trailing slash removed | A14 | `healthlinkq.pro/L41156846fl-923e569-fkf418Z` |
| | `outbound.domain` | registrable domain of CTA links, form targets, click beacons | A15 | `crimsonbase.pro` |
| | `redirect.domain` | registrable domain of each redirect hop (live capture) | I5 | |
| `ids.campaign` | `aio.campaign_uuid` **exclusive** | UUID as found | A1 | `0b9611d9-…` |
| | `aio.owner_uuid` **exclusive** | UUID | A1 | |
| | `aio.landing_uuid`, `aio.flow_uuid` | UUID | A1 | |
| | `campaign.label`, `landing.label` | internal label, trimmed | A9 | `OPER / PL / Camp1` |
| | `nav.landing_uuid` | landing UUID found in stored navigation state | I4 | 61 chat names the article's landing |
| `ids.tracking` | `fb_pixel` | digits | A3 | `2012165449475163` |
| | `gads_id`, `gtm_id`, `ga_id` | `AW-…`, `GTM-…`, `G-…` as found | A4 | `AW-18463917747` |
| | `taboola_id`, `uet_id`, `tiktok_pixel`, `ym_id` | ID as found | A5 | |
| | `cdn.zone_id` | CDN pull-zone or video-library ID | C12 | `vz-6110f311-389` |
| `ids.conversion` | `aff.network`, `aff.user_id`, `aff.offer_id`, `aff.landing_id` | as found | A6 | `aff.user_id=13` |
| | `form_endpoint_host` | registrable domain the form posts to | A7, I3 | `shadowtracecore.com` |
| | `form_endpoint_path` | path only | A7 | `/v1/landing/form-event` |
| | `api_key_id` | key ID; never the key | A8 | `APPA-165` |
| | `tracker.host` | named third-party tracker, cloaker or CRM host | B7 | `bf1.clickline.org` |
| | `contact.phone`, `contact.email`, `contact.handle`, `contact.wallet` | normalised | A12 | |
| `brand.offer` | `brand.offer` | product or offer name; layer case-folds it | A13 | `Świtowin Zyskarnia` |

**Exclusive** namespaces: two different values on two pages are a contradiction, which caps
the pair at *same family*. Everything else may rotate between deployments.

## Artifact tier (T2): supports *duplicate* and *same campaign* (two groups needed)

| Layer | Namespace | Value shape | Catalogue |
| --- | --- | --- | --- |
| `asset.exact` | `asset.sha256` | SHA-256 hex of content images; not logos, icons or libraries | E1 |
| | `avatar.sha256` | SHA-256 hex of fake-commenter avatars | E12 |
| `text.shingle` | `page.body_text` (else `text.body`, else `page.clean_text`) | article body as one string: comments, navigation, widgets removed | F1 |
| `asset.perceptual` | `image.phash` | 64-bit grayscale pHash, 16 hex chars, one row per image | E2 |
| `ad.creative` | `creative.phash`, `creative.ocr_text` | pHash of the ad image; OCR text | L2, L3, E11 |
| `media.video` | `video.frame_phash` | pHash per sampled key frame | M1 |
| `text.semantic` | `page.body_text` | (same input as text.shingle; embedded by the layer) | F2 |

## Kit tier (T3): supports *same family*

| Layer | Namespace | Value shape | Catalogue | Example |
| --- | --- | --- | --- | --- |
| `kit.config` | `aio.template_uuid`, `aio.landing_type_uuid` | UUID | A2 | `25c6c6fd-…` |
| | `config.key_path` | one row per JSON key path of the config object | B3 | `aio.pixels.fb` |
| | `api.route_shape` | own-domain API route with IDs replaced by `<uuid>`/`<id>` | B6 | `/api/v1/trigger/field/<uuid>/` |
| | `traffic_source` | declared source or cloaker name | B8 | `AIO FB CAPI via Cloakerson` |
| `kit.url` | `url.param_name` | one row per landing-URL parameter name; emit all, the stoplist drops `utm_*`, `fbclid` … | B1 | `utm_visit` |
| | `url.param_shape` | `name:shape` with shape ∈ `uuid`, `digitsN`, `lowerN`, `alnumN`, `token20mixed` … | B2 | `utm_id:token20mixed` |
| | `url.path_convention` | lander path with slug and digits abstracted | B5 | `/lander/<slug>/index.html` |
| `kit.form` | `form.field` | one row per form field or hidden-field name | B4, I2 | `fbtoken` |
| `kit.dom` | `dom.class` | one row per CSS class name; layer drops framework and translator classes | D2 | `aio-sdk-form` |
| `kit.code` | `firstparty.sha256` | SHA-256 of first-party CSS/JS (libraries are stoplisted by hash) | D3 | `main.css` |
| | `code.comment_lang` | ISO language of developer comments | D5 | `ru` |
| | `code.residue_lang` | ISO language of leftover localisation text | D6 | `uk` |
| | `code.naming` | a naming habit worth matching | D5 | `section-banner-comments` |
| | `code.evasion_filter` | `hue-rotate` when `<html>` carries the filter (the angle is not the value) | E13 | `hue-rotate` |
| `infra.host` | `dns.ns_pair` | both name servers, sorted, `|`-joined | C3 | |
| | `rdap.registrar`, `http.server`, `origin.asn` | as found | C2, C10, C11 | `Caddy` |

## Playbook tier (T4): supports *same playbook* only, never merges campaigns

| Layer | Namespace | Value shape | Catalogue |
| --- | --- | --- | --- |
| `narrative.skeleton` | `page.body_text` | (masked and embedded by the layer) | F3 |
| `pattern.mo` | `pattern.score` | `{"pattern": "<library id>", "score": 0..1}` one row per pattern | H, §6 |
| `kit.absence` | `expect.element` | value = element id, `state` = present / absent / not_observable | K, §6 |
| `entity.cast` | `entity.cast` | `{"entity": "<name>", "role": "<role>"}` | G1, G3 |
| `visual.layout` | `screenshot.embedding` | `{"model": …, "values": [...]}` | E4 |

## Domain risk and enrichment (C1–C5, C13)

Written by `domain.shape` (script) and the `dns` / `rdap` enrichers. Every enrichment row keeps
its provenance in `raw` as JSON: `endpoint`, `lookup_at`, `cache_age_s`, `cached`, and for
domain age `captured_at` and `basis`. A failed lookup is a `not_observable` row with `error`.

| Namespace | Value shape | Purpose | Catalogue |
| --- | --- | --- | --- |
| `dns.ns_pair` | name servers, lower-case, no trailing dot, sorted, `|`-joined; RDAP delegation when the zone has none (`confidence` 0.8) | linkage (`infra.host`); risk when the servers are parking or sinkhole ones, or the RDAP stand-in for a dead domain | C3 |
| `rdap.registrar` | registrar name without corporate suffixes (`namecheap`); original and IANA ID in `raw` | both (`infra.host`) | C2 |
| `rdap.registered_at` | ISO date of the `registration` event | risk | C1 |
| `rdap.domain_age_days` | whole days from registration to capture; `basis` = `har:…`, `incident_har:…`, `manifest:…` or `lookup` (`confidence` 0.5) | risk | C1 |
| `dns.status` | `live`, `dead`, `parked`, `sinkholed`, judged on the page's own host (A and AAAA); the rule in `raw` | risk | C13 |
| `dns.a`, `dns.mx` | one row per A or AAAA address of the page's host / per mail host of the domain | risk | C13 |
| `domain.tld_risk` | `high` (`.live .pro .xyz .top .icu .life`), `low` (`.pl`), `other` | risk | C4 |
| `domain.name_shape` | `hyphen_digits`, `generated_pronounceable`, `generated`, `plain`; metrics in `raw` | risk | C5 |

## Live capture (I5, C11, C13, C14)

Written by `capture.trace` from a capture bundle's `manifest.json` (`ftf capture`), or from
an archived HAR's document redirects when there is no manifest. Locators cite
`manifest:redirects[i]`, `manifest:server.*`, or `har:entries[i].response.redirectURL`.
Observed redirects are kept apart from stored navigation history (`nav.landing_uuid`):
a hop says how the page was reached, history only that another page was visited before.

| Namespace | Value shape | Purpose | Catalogue |
| --- | --- | --- | --- |
| `redirect.domain` | registrable domain of every hop host other than the page's own | linkage (`ids.domain`) | I5 |
| `redirect.hop` | `<kind>[ <status>]: <from> -> <to>`; kind `http`, `meta_refresh`, `script` or `history` | risk | I5 |
| `origin.ip` | IP that served the final document; left out when CDN headers show an edge | linkage | C11 |
| `tls.issuer` | certificate issuer as the browser reports it | risk | C14 |
| `tls.validity_days` | days between `valid_from` and `valid_to`; both dates in `raw` | risk | C14 |
| `capture.status` | `dns_failed`, `connection_failed`, `tls_failed`, `timeout`, `http_error`, `not_in_archive`, `failed`; browser error in `raw`. Absent when the capture succeeded | risk | C13 |

## Cloaking differential (J1, J2, J5)

Written by `cloak.differential` (`grouping/cloaking.py`) after fingerprinting, for a URL
captured under several visitor profiles (`ftf capture URL --cloaking [--crawler]`): `ad_click`
(mobile, `Referer: https://l.facebook.com/`, original query, a synthetic `fbclid` when the URL
has no click id), `bare` (desktop, no referrer, no query), `crawler` (Googlebot, no query).
Each profile is its own view. Variants are paired by run (`View.meta.capture_run`, from the
manifest's `differential_run`), so a second run or a bundle left behind in the same folder
is never compared with this one; bundles without a run id pair by URL and folder. Every
profile sends client hints and `navigator.platform` matching its user agent, and
`navigator.webdriver` is false. The ad-click variant is the reference. A variant counts as
loaded when it saved a page, a timeout included. Difference = 1 − mean of text similarity
(`text.shingle` MinHash, or exact shingle/word overlap for short text) and structure
similarity (`dom.tag_paths` Jaccard, `kit.dom` MinHash); at least 0.9 when the variants land
on different registrable domains. Threshold 0.5. Locators cite `differential.json` and
`profile:<other>`.

| Namespace | Value shape | Purpose | Catalogue |
| --- | --- | --- | --- |
| `cloak.differential` | on the ad-click view, one row per other profile: `{reference, against, against_view, difference, text_similarity, structure_similarity, final_hosts, threshold}`. `present` at or above the threshold, `absent` below, `not_observable` when a variant loaded no page | risk | J1 |
| `cloak.gating` | on the ad-click view, when ad click and bare differ: `{against, difference, factors}`, the factors only the ad-click capture carried, read from the manifests (referrer, query string, synthetic click id, mobile device). Not written when bare got an HTTP error: the `cloak.differential` row then carries a `caveat` instead | risk | J5 |
| `cloak.white_page` | on a variant that differs from the ad click: `{traits, hash_like_class_share, trackers_here, trackers_on_reference}`; traits are *no trackers while the ad click loads them* and *obfuscated hash-like class names* (≥ 30 % and at least 20 points above the ad click; `kit.dom` framework classes, Emotion/MUI `css-…`, react-native-web `r-…`, StyleX `x…` and dimensions such as `300x250` are not counted) | risk | J2 |

## Funnel evidence inside an incident (ad or screenshot view → page view)

| Namespace | Value shape | Catalogue | Used for |
| --- | --- | --- | --- |
| `ocr.domain` | domain shown in the ad creative | E11 | ad domain = page host |
| `ad.id` | platform ad ID from the screenshot or ad library | A10 | ad ID present in the landing URL |
| `ocr.brand`, `ocr.person` | brand / named person read from the image | E11, G1 | shared brand, offer or person |
| `ad.advertiser` | advertiser page name | G4 | shown to the analyst |
