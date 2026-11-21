# HackNation — relationships, patterns, and approaches in the supplied materials

**Challenge:** Kolekcjoner fejków / Follow the Fake  
**Analysis date:** 21 November 2026  
**Scope:** the ten incident folders in `do_udostępnienia`, interpreted against the supplied five-page challenge description.  
**Status:** evidence-based analysis of archived material; proposed campaign groupings are analytical conclusions, not organizer ground-truth labels or attribution to real people.

## 1. Main findings

The collection contains several distinct kinds of relationship. Treating all similarity as “the same campaign” would lose the most useful information.

1. **Cases 1 and 2 are strongly linked as deployments of the same internally identified campaign and landing page.** They share a campaign UUID, owner UUID, landing UUID, template UUID, flow UUID, Facebook pixel, custom JavaScript, and distinctive images. Their hostnames and Facebook advertising campaign IDs differ. This is the clearest cross-folder campaign match.
2. **The healthlinkq page archived inside case 2 matches the standalone page in case 3.** The captured HAR response and saved case-3 page contain essentially the same Monika Olejnik narrative, with small date and numeric changes. This is a concrete content connection across folders. It does not establish that the Brzoska page and Olejnik page were consecutive stages of one journey.
3. **Cases 48 and 61 share an explicitly identified landing-page template and reused assets, while declaring different campaign and owner UUIDs.** They should be linked as a template/production family, with separate campaign records unless further evidence supports merging them.
4. **Case 61 contains a multi-stage investment funnel and an alternative page on the same domain.** Its purported news article and chat/quiz share campaign, owner, template, flow, and Google advertising identifiers. The chat page’s stored history explicitly names both landing pages. A separate capture of the domain shows a media-analysis business site. This supports a multi-view domain finding; the precise switching mechanism remains unverified.
5. **Case 14 contains two variants of a Metabo RX landing page.** Both use the same product, domain, price, and affiliate-system fields, but have different landing IDs and offer UUIDs. These are variants within one product/funnel family rather than exact duplicate files.
6. **Cases 14 and 47 share a health-product sales formula, not demonstrated common ownership.** Both use video, medical framing, embedded testimonials, a name/phone order form, a 50% discount, and “17 remaining” stock language. Their products, identifiers, video infrastructure, and integration details differ.
7. **Cases 5, 10, and 36 need distinct treatment.** Case 5 is a medical-themed video with no captured destination. Case 10 imitates an app marketplace for a casino offer. Case 36 pairs a political/financial ad with a conference-themed destination. None should be forced into an existing campaign merely because they promise financial or health benefits.

The strongest implementation direction is an **evidence graph with separate campaign, template, content, infrastructure, and behavioral relationships**. A single undifferentiated cluster or scam score would hide distinctions the materials actually demonstrate.

## 2. What was examined and what “complete” means here

The examination covered the source-file inventory; primary saved HTML and rendered DOM text; page configuration objects; HTML links, forms, and scripts; all 11 HAR-format captures, including the unusually named case-61 article capture; exact file hashes across folders; 21 top-level screenshot/ad images; the capture manifest in case 14; and six distributed sample frames from each of the three MP4 videos.

The source collection contains **329 non-`.DS_Store` files**, including **22 HTML files**, **11 HAR-format captures containing 321 request entries**, **153 raster image files**, **three MP4 videos**, one MHTML archive, and an HLS playlist with two saved transport-stream segments. The filesystem extension count alone finds only ten `.har` files because one capture is named exactly `.har`.

Not every file is an independent incident: browser support documents, repeated assets, DOM snapshots, original responses, and MHTML copies overlap. File counts therefore must not become incident counts or evidence weights.

This is a complete structured synthesis of the observable relationships found through those methods. It is **not** a full audio transcription, exhaustive video-frame examination, forensic face/deepfake assessment, live-domain investigation, or proof that no additional hidden relationships exist. MHTML content was treated as an archival counterpart, not an additional independently analyzed incident. Archived claims about public figures, financial returns, regulation, and medical results are described as claims made by the material, not established facts.

### Evidence vocabulary

| Label | Meaning |
| --- | --- |
| Observed | A value, asset match, URL, text, or behavior description exists in the supplied artifact. |
| Strong inference | Several specific, independent observations support a relationship. |
| Candidate | A plausible relationship that needs additional evidence. |
| Unresolved | The collection cannot establish the relationship or explain the mechanism. |

Confidence refers to the **specific relationship**, not a numerical probability of criminality or a verified real-world operator identity. An owner UUID is an internal software identifier, not a named person.

## 3. Inventory and incident-level interpretation

| Case | Main material | Captured destination / identity | Apparent approach | Evidence limits |
| --- | --- | --- | --- | --- |
| 1 | Sponsored ad screenshot, Gazeta-styled article, HAR, assets | `civicdaybook.live`; Świtowin Zyskarnia | Purported shooting of Rafał Brzoska becomes an investment endorsement and deposit invitation | No downstream deposit/callback verified |
| 2 | Same Brzoska article on another host; two HARs | `acrosscommunities.live`; additionally `healthlinkq.pro` | Reused Brzoska campaign plus a separately captured Olejnik page | Co-location of the two pages does not prove a redirect between them |
| 3 | Sponsored ad screenshot and TVN24-styled article | `healthlinkq.pro` | Purported attack on Monika Olejnik; confrontation with Adam Glapiński; investment-platform promotion | No standalone HAR in this folder; case 2 supplies a related response |
| 5 | 198.13-second MP4 and platform download metadata | Facebook Ad Library ID `1982065065827084` | Pain/anatomy footage followed by a white-coat spokesperson | No destination, product, order form, or campaign ID established |
| 10 | Six screenshots, marketplace-styled page, HAR, scripts | `shop-77vault.xyz`; Sweet Bonanza / Słodka Bonanza | Casino promotion using Cristiano Ronaldo imagery and an App Store imitation | Actual app installation, payout, and marketplace publication not verified |
| 14 | Ten screenshots, 249.16-second screen recording, two main page variants, archive bundle | `ineptsolidoval.top`; Metabo RX | Weight-loss advertorial/video, scarcity, discount and callback/order capture | Two landing variants; saved media is partial; timestamps differ by artifact |
| 36 | Sponsored political/financial ad, conference-themed page, HAR, scripts | `meroporepufulo.life`; Poland Invest Summit 2026 | Strong ad–destination mismatch; conference/event presentation | Decoy/cloaking is a hypothesis, not an observed switching rule |
| 47 | 165.77-second screen recording, page, HAR | `shryxla-mynx.icu`; Uromexil Forte | Prostate-health fear framing, video, testimonials, discounted order | Recorded ad and landing are different artifacts; order success not established |
| 48 | Sponsored ad screenshot, long news-styled article, HAR, scripts | `novumfy.com`; Orzeł Fundaris | Tomasz Lis arrest/suppression story, purported TV discussion and investment returns | Separate campaign from case 61 despite template similarity |
| 61 | Ad image; article, chat page and business-site capture; three HARs | `glowpixel.org`; Trwały Działunek; media-analysis portal | Investment/pension article followed by chat/lead capture, plus alternative business presentation | Same-domain multi-view evidence is strong; selection conditions remain unknown |

The displayed advertiser names are also heterogeneous: the screenshots show names such as Formal Favorable (1), EasyJoy (3), Sweet Bonanza (10), Sustain Studio (14), El John Production (36), Sharon Coleman (48), and Glowpixel (61); the case-47 recording shows Evelyn Johnson. These labels are acquisition-layer observations, not verified legal business identities. No repeated advertiser-name bridge was established across the separate numbered cases.

## 4. Strongest relationship: case 1 ↔ case 2

### Concrete shared identifiers

| Feature | Shared value |
| --- | --- |
| Internal campaign UUID | `0b9611d9-df5e-44e6-acea-ef85847dc3f1` |
| Campaign label | `OPER / PL / Camp1` |
| Internal owner UUID | `edcdf254-3b20-4bcb-bd7f-3cddaf77d9da` |
| Landing UUID | `a7b9f833-7f9b-4bd4-ac5a-5d6d5743fbba` |
| Landing label | `1038 \| PL - Brzoska Crime 2 - Świtowin Zyskarnia` |
| Template UUID | `9b614b03-f138-4536-8265-b92e478e62a7` |
| Flow UUID | `962e0d71-e579-449c-bbcf-1fcd657988f4` |
| Campaign human ID | `214711` |
| Landing human ID | `354163` |
| Facebook pixel | `2012165449475163` |

**Content evidence:** the visible article texts have **0.9894 word-five-gram Jaccard similarity** after lowercasing and whitespace normalization. This is a reproducible text-overlap score, not “98.94% probability of the same operator.” Date/time details differ.

**Asset evidence:** eleven substantive raster images match byte-for-byte across folders, including the lead image, hospital image, interview images, bank-transfer illustrations, and avatars. The custom `main.js` and `main.css` also match exactly. Reuse of the publisher logo alone would be much weaker evidence.

**Observed variation:** the host changes from `civicdaybook.live` to `acrosscommunities.live`. The saved Facebook campaign IDs change from `120251407066590682` to `120255973907970348`; corresponding ad IDs also differ. The internal campaign/landing identifiers remain the same.

**Conclusion:** strong evidence for one internally identified campaign using multiple domain and advertising deployments. Preserve both acquisition records. Merge their campaign relationship, not their original observations. Different hostnames or advertising campaign IDs would otherwise create a false split.

**Sources:** primary case-1 and case-2 HTML configuration objects, their HARs, and matching asset files.

## 5. The bridge between case 2 and case 3

Case 2 contains a HAR for `healthlinkq.pro`. Its first document response contains the Olejnik/Glapiński story, rather than the Brzoska article saved beside it. The standalone HTML in case 3 was saved from the same host and landing path:

`healthlinkq.pro/L41156846fl-923e569-fkf418Z/`

Whitespace-normalized visible text differs only at a small number of date/numeric fragments. Both versions describe an attack after a television confrontation and promote an investment platform. Case 3 also contains links to an offer-style path on the same host and click-beacon references to `crimsonbase.pro`.

This produces **two distinct edges**:

- Case-2 healthlinkq response ↔ case-3 page: strong near-duplicate/content-deployment relationship.
- Brzoska article ↔ Olejnik article: related narrative mechanism, with common capture-folder context; a single campaign or sequential navigation relationship remains unproven.

The folder is a container of evidence, not a guarantee that every artifact belongs to the same campaign. There is no captured HTTP redirect chain demonstrating `acrosscommunities.live → healthlinkq.pro`. Do not manufacture that edge from their shared folder.

**Sources:** `2/healthlinkq.pro.har`, document entry 0; case-3 HTML saved-from metadata, visible text, outgoing links and beacon code.

## 6. Cases 48 and 61: shared production template, distinct campaigns

| Feature | Case 48 | Case 61 article |
| --- | --- | --- |
| Domain | `novumfy.com` | `glowpixel.org` |
| Offer field | Orzeł Fundaris | Trwały Działunek |
| Internal campaign UUID | `ad8555cd-ad11-493f-8c31-c8f0d4799975` | `849a04c0-c067-4d3f-8317-6c4cb0a89923` |
| Internal owner UUID | `2050d8e9-de7b-4ac0-8df1-594df4b9bf02` | `c786ef45-015f-42c4-b575-e5b452b54107` |
| Landing UUID | `307973dd-e974-4729-8a73-f32195a80f42` | `5c68be98-5565-4610-bf12-736a77480c11` |
| Template UUID | `25c6c6fd-73af-48cb-b5cf-752154ebc474` | Same |
| Landing-type UUID | `25024431-7a50-4282-959d-700a82522e5c` | Same |
| Internal landing label | Tomasz Lis — Liberation v2 — MVT | Donald Tusk — Pension gate v6 |
| Declared acquisition source | AIO FB CAPI via Cloakerson | Google |

Both saved pages retain the same “37 minutes after the broadcast … handcuffs … recording removed” title, yet their body narratives differ substantially. Case 48 centers on Tomasz Lis, Anita Werner, Mateusz Morawiecki and a claimed Brzoska endorsement. Case 61 changes the story toward Donald Tusk, Agnieszka Dziemianowicz-Bąk, pensions, ZUS, and purported KNF supervision.

**Exact reusable elements:** `news1.webp` through `news5.webp`, `avatar.webp`, `logo.svg`, and a `bundle.umd.min.js` file match across the two cases. These asset matches reinforce the explicit template ID.

**Template residue:** a sensational title survives a substantial body-content rewrite. Title similarity is useful for candidate discovery but insufficient for determining campaign identity.

**Additional shared code:** both pages configure `shadowtracecore.com` for `/v1/landing/form-event`, key ID `APPA-165`, and the same public encryption-key text. They also contain references to `showflashdaily.com`. These are observations of shared embedded integration code. Their provenance and role require validation: they could represent shared campaign infrastructure, a service/integration distributed across clients, or modifications introduced elsewhere in the material pipeline. They must not independently establish operator attribution.

**Conclusion:** strong template/production-family relationship; separate campaign and internal-owner identifiers. A common provider is plausible. Common real-world control is unresolved. Different owner UUIDs are evidence of distinct internal records, not proof of unrelated real people.

## 7. Case 61: article, conversation, and alternative domain presentation

The strongest within-folder funnel relationship is in case 61:

1. The captured ad promotes a wealth/inheritance-themed message and names Glowpixel as sponsor.
2. The article adopts news styling and a pension/investment story. Its configuration identifies the offer as Trwały Działunek.
3. The chat page introduces “Sara,” asks an initial gender question, promises a personalized strategy, then requests registration and a telephone callback.
4. A separate root-domain capture presents a media-production and digital-content research business.

### Why the article and chat belong together

They share campaign UUID `849a04c0-c067-4d3f-8317-6c4cb0a89923`, owner UUID `c786ef45-015f-42c4-b575-e5b452b54107`, flow UUID `ba10cdcf-2638-48cb-b655-bbc3be7f73da`, template UUID `25c6c6fd-73af-48cb-b5cf-752154ebc474`, and Google tag `AW-18463917747`.

Their landing UUIDs differ: the article is `5c68be98-5565-4610-bf12-736a77480c11`; the chat is `91837506-95eb-4263-946d-73d5d4f62c73`. The chat’s stored navigation state lists both and records approximately 60 seconds on the article and 254 seconds on the chat. These are values stored by the page, not independently measured user engagement statistics.

The chat claims that a 1,150 zł investment could generate up to 1,800 zł profit within days. Its forms request first name, surname, email, and telephone. The news page also contains lead-form fields. The evidence therefore supports a **lead-acquisition funnel with a callback handoff**, not a demonstrated completed investment or payment.

### Why the third page matters

The alternative `glowpixel.org` capture describes media-analysis services, staff, reports and prices, using a different site presentation. Its content is not a near-duplicate of the article or chat. The shared domain connects it operationally to the captured domain record; it should remain a separate page/view node.

Possible explanations include contextual routing, a benign-facing landing variant, a changed deployment, or collection-time differences. “Confirmed cloaking” would require observing the same endpoint return different content under controlled changes in referrer, query, browser, location or session. That experiment has not been performed.

## 8. Case 14: multiple Metabo RX variants and affiliate structure

The top-level `Metabo RX.html` and the nested archive’s `dom.html` differ in headline, comments and interaction. One advertises an ideal figure in 27 days; the archived variant contains a stronger personal guarantee and a door-selection discount interaction. The screenshots introduce additional claims such as weight loss in seven days without dieting. These are inconsistent promotional claims within one evidence bundle, not verified product effects.

| Field | Top-level saved page | Nested archived DOM |
| --- | --- | --- |
| Product | Metabo RX | Metabo RX |
| Price | 69 PLN, displayed against 138 PLN | Same |
| Offer UUID | `de0b70b8-4266-47ff-997d-04ed9019023f` | `ab12ff9d-5f14-4da6-a85b-a054c0865f10` |
| Landing ID | `25714` | `23501` |
| Network | `149` | Same |
| User field | `13` | Same |
| Tracker | `bf1` | Same |
| Theme / vertical IDs | `7` / `1` | Same |
| Country / language | PL / pl | Same |

The saved metadata includes an upstream pixel parameter `1071875908635809`. HAR requests show `bf1.clickline.org` and `eu-vi-stream.org`; saved scripts additionally reference metrics and CRM integrations, including `metrics.clickline.org` and `goods-crm.com`. A script reference is not automatically a completed network request or evidence of a successful submission.

The form’s `theme` value is Ukrainian-language text for weight loss, while Polish content coexists with French and Dutch remnants. This is evidence of localization/template reuse. It does not identify the operator’s nationality or location.

The capture manifest identifies `page-capture 0.1.0`, capture time `2026-10-12T09:48:17+00:00`, autoscroll, disabled cache and bypassed service workers. It notes that only the HAR is sanitized; DOM/resources/MHTML are raw. Capture behavior may affect what was loaded and recorded. Secrets and raw visitor identifiers are omitted from this report.

The HLS playlist and two `.ts` segments are partial media evidence, not a full recovered sales video. The MP4 screen recording separately documents an Instagram ad, a woman’s testimonial, navigation to the domain, and a doctor-style landing video in sampled frames.

**Conclusion:** strong product/funnel-family relationship between two captured variants; keep separate landing and offer identifiers to make the variation visible.

## 9. Health-related cases 5, 14 and 47

### Shared persuasive structure

All three use medical or bodily concerns and authority cues. Case 5 shows pain/anatomy imagery and a white-coat spokesperson. Case 14 focuses on weight loss. Case 47 emphasizes prostate symptoms and male-health fear; its recording shows a clerical-looking spokesperson in the ad, while the landing comments discuss a doctor/video.

Cases 14 and 47 are more closely related at the **sales-mechanism** level:

- a prominent video with sound/continuation prompts;
- dramatic health-result or risk framing;
- embedded social-style testimonials;
- a displayed 50% discount;
- a claim that only 17 products/packages remain;
- a countdown or urgency display;
- name and telephone fields for order/callback capture.

### Why this does not establish one campaign

Case 47 uses Uromexil Forte, a price of 137 PLN against 274 PLN, and an order action at `shryxla-mynx.icu/lander/order.php`. Hidden fields include pixel `4322849547991901`, `sub2=Yosha`, `sid=1-48`, `x_id=4084` and country PL. These are opaque application fields; `sid=1-48` must not be equated with dataset case 48.

Its captured video host is `vz-6110f311-389.b-cdn.net`, while case 14 uses `eu-vi-stream.org`. The two share an exact jQuery file, but that is a common library and contributes essentially no campaign-specific evidence.

Case 5 lacks the destination and identifiers needed to compare the downstream funnel. Its medical-video format is a broad thematic relationship only. No reliable person identification or video-generation attribution was performed.

**Conclusion:** group these under a medical-persuasion taxonomy; preserve case 14 and case 47 as separate campaign candidates and case 5 as incompletely resolved.

## 10. Case 10: casino offer and marketplace impersonation

The page presents Sweet Bonanza as an iPhone/iPad app, uses an App Store-style layout, names Cristiano Ronaldo as developer, displays a 4.8 rating, and claims casino bonuses, quick winnings and withdrawals. The browser host is `shop-77vault.xyz`, not an Apple marketplace host. The imitation relationship is directly visible in the saved page and screenshots.

The promotion combines celebrity imagery, a purported news-presenter segment, urgency, localized bank/payment references, and reviews describing successful payouts. Some reviews include minor usability complaints alongside payout claims. This creates a more varied credibility presentation than uniformly positive testimonials, but no review’s authenticity was independently established.

The supplied screenshots also show an important mismatch: the ad-library presentation contains bath-rug product text/imagery while the observed ad and landing promote a casino. This could involve creative variants, changes over time or contextual presentation. The mismatch itself is observed; deliberate moderation evasion is an inference requiring more evidence.

Technical traces include a manifest link, service-worker link, iOS-app script and a back-navigation script. These indicate a web-based app-installation/navigation implementation; they do not prove that an install succeeded or a native app existed. Three numeric Facebook pixels are initialized in the saved HTML: `1549107073347817`, `2070071404384324`, and `1376242401352160`. No exact match to the other cases’ extracted numeric pixels was established.

**Conclusion:** a distinct casino/marketplace-imitation family. Its similarity to the investment cases is the broader money/authority/social-proof formula, not a demonstrated shared campaign.

## 11. Case 36: ad–destination mismatch and unusual scripts

The sponsored ad shows a Polish parliamentary/political scene with a financial message and a “99 places remaining” counter. The saved destination is a Poland Invest Summit 2026 conference page with speakers, agenda, tickets and a Warsaw setting.

The conference copy advertises 14–16 October 2026, whereas the screenshot filename indicates 19 October. That apparent staleness is useful triage evidence, but filenames and page copy do not independently establish when an event happened or prove fraud.

The HAR’s initial request carries Facebook/Instagram-style acquisition parameters and pixel parameter `1536860380829193`. It records the conference view, not a verified investment-registration or payment stage.

A separate static-code finding deserves preservation: the saved `main.js`/`script.js` contains an `/admin`-conditional login overlay that sends entered credentials to `lerandbigdicker.com/kei`. A modified `lazysizes` file contains additional login-overlay code and a Binom-related support reference. The code’s credential-transmission logic is observable; execution, successful credential capture, injection origin, and connection to the consumer-facing campaign are not established. Treat this as an **artifact-level script anomaly** requiring investigation, not as a proven link to another numbered case.

**Conclusion:** retain as a separate candidate. The ad mismatch is stronger than any cross-case attribution signal presently available.

## 12. Cross-case narrative and conversion patterns

| Pattern | Cases | Observable implementation | Appropriate relationship |
| --- | --- | --- | --- |
| Purported news coverage | 1, 2, 3, 48, 61 | Publisher styling, bylines, interviews, timestamps, news imagery | Presentation/template family |
| Exposure followed by retaliation | 1, 2, 3, 48; retained title in 61 | Shooting, arrest, suppression or deleted-recording story | Narrative family |
| Famous-person authority | 1, 2, 3, 10, 48, 61 | Public figures portrayed as endorsers, participants or authority references | Entity-role relationship, not ownership |
| Conflict with banks/elites | 1, 2, 3, 48 | Alleged suppression of ordinary people’s access to profit | Narrative mechanism |
| Claimed state/regulatory legitimacy | 61; other investment stories invoke regulators differently | ZUS/KNF references and purported official validation | Claimed-authority feature |
| Extraordinary financial upside | 1, 2, 3, 10, 48, 61 | Investment profit or casino payout claims | Broad lure category |
| Health fear / bodily improvement | 5, 14, 47 | Pain, symptoms, weight loss, medical authority | Broad lure category |
| Social proof inside the page | 1, 2, 3, 10, 14, 47, 48, 61 | Comments, testimonials, payout claims and counters | Persuasion mechanism |
| Scarcity and urgency | 1, 2, 10, 14, 36, 47; urgency also in suppression stories | Deadlines, remaining places/stock, offer-ending language | Persuasion mechanism |
| Callback/lead handoff | Explicit in 1/2 instructions, 14, 47, 61; form fields also in 48 | Name/phone or full registration data | Funnel-stage relationship |
| Ad–destination inconsistency | 10, 36; same-domain view inconsistency in 61 | Rug vs casino; political ad vs conference; article/chat vs business portal | Mismatch/contextual-view finding |
| Reusable acquisition runtime | 1, 2, 48, 61 | `window.aio`, landing/campaign/visit structures, conversion and session endpoints | Software ecosystem relationship |

A key narrative mechanism is **turning evidence that should provoke caution into claimed proof of credibility**: regulatory resistance or alleged suppression is presented as proof that the opportunity threatens powerful interests. Another is **borrowed verification**: the page claims that a journalist, television presenter, auditor, bank screenshot or state body has already checked the offer. These are analytical interpretations of the copy, not findings that those institutions participated.

The same real person can serve different roles across variants. For example, Brzoska is a central purported founder/victim in cases 1/2 and a supporting endorsement figure in case 48. Entity extraction should preserve those roles rather than connecting everything containing his name into one campaign.

## 13. Complete pairwise relationship map

The matrix covers all 45 unordered pairs of the ten case folders. It deliberately records relationship type instead of implying that every edge means common campaign membership.

**C:** strong shared campaign. **A:** strong shared page/content artifact. **T:** strong shared template/production family. **N:** narrative similarity. **H:** broad health-persuasion similarity. **M:** broad financial lure, acquisition mismatch, or credibility mechanism only. **—:** no specific substantive relationship established beyond commonplace web dependencies or general advertising context. Multiple letters describe different evidence levels.

| Case | 1 | 2 | 3 | 5 | 10 | 14 | 36 | 47 | 48 | 61 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | — | C | N | — | M | — | M | — | N | N |
| 2 | C | — | A, N | — | M | — | M | — | N | N |
| 3 | N | A, N | — | — | M | — | M | — | N | N |
| 5 | — | — | — | — | — | H | — | H | — | — |
| 10 | M | M | M | — | — | — | M | — | M | M |
| 14 | — | — | — | H | — | — | — | H | — | — |
| 36 | M | M | M | — | M | — | — | — | M | M |
| 47 | — | — | — | H | — | H | — | — | — | — |
| 48 | N | N | N | — | M | — | M | — | — | T |
| 61 | N | N | N | — | M | — | M | — | T | — |

“N” for links involving case 61 refers to investment/news narrative reuse; its body changes the scenario and its retained headline supplies part of the suppression motif. “M” is a taxonomy-level connection only and must not influence campaign assignment as if it were an identifier match. Absence of an established edge means “not demonstrated here,” not “unrelated in reality.”

### Important additional edges below the case-folder level

| Nodes | Relationship | Confidence |
| --- | --- | --- |
| 1 Brzoska page ↔ 2 Brzoska page | Same internal campaign and landing; different domains/ad deployments | High |
| 2 healthlinkq response ↔ 3 Olejnik page | Same landing path and near-identical content | High |
| 14 top-level page ↔ 14 archived DOM | Same product and affiliate fields; different landing/offer IDs | High |
| 61 article ↔ 61 chat | Shared campaign/flow and stored navigation history | High |
| 61 article/chat ↔ 61 business page | Same domain with different captured content | High for same-domain association; unresolved for switching mechanism |
| 1/2 ↔ 61 article | Exact `main.css` reuse; broader AIO-style runtime structure | High for asset/runtime reuse, weak for campaign attribution |
| 48 ↔ 61 | Shared explicit template ID, images, logo and bundled library | High for template-family relationship |
| 14 ↔ 47 | Same generic jQuery bytes | Observed, negligible campaign value |
| 1/2 ↔ 3 | Same saved Facebook SDK bytes | Observed, negligible campaign value |

## 14. Infrastructure relationships and false-link risks

The network traces repeatedly contain Facebook, Google, jsDelivr, cdnjs and font-service hosts. Several IP addresses also recur, including `188.114.96.11` across cases 14, 36 and 48, and `151.101.193.229` across 48 and 61. These shared addresses span requests to different hosts and do not demonstrate a shared origin server. No DNS-history or origin-hosting investigation was performed.

Extract and compare the **specific identifier and its namespace**, not simply the presence of a tracker. A Facebook pixel value is much more discriminative than the Facebook SDK hostname. A campaign UUID is more specific than an AIO runtime. A video-hosting service shared by many clients would be weaker than an identical video asset.

The HARs do not contain a demonstrated cross-case HTTP redirect chain. Observed redirects include library-version resolution and Google conversion traffic; those are not consumer funnel transitions. Links, JavaScript routing, stored navigation history, referrers and HTTP redirects must remain separate edge types.

Additional false-link traps:

- repeated publisher logos can reflect impersonation of the same publisher;
- exact common-library matches are expected on unrelated sites;
- unchanged template assets do not imply unchanged offer or controller;
- one screenshot can capture multiple app/UI layers and neighboring feed content;
- date strings in page copy can be dynamically refreshed or simply fabricated;
- a script’s unused capability is not observed behavior;
- analytics/session failures returning 404 do not prove successful data collection;
- a source name mentioning a cloaking service is configuration evidence, not a tested cloaking result;
- placeholder macros such as `{{aio.visit.destination_uuid}}` and `{lang}` are unresolved values, not usable identifiers;
- connected components through generic providers would incorrectly collapse much of the dataset into one cluster.

## 15. Recommended analysis approach

These recommendations are derived from this collection and the challenge brief. They are not a benchmark of currently marketed tools.

### A. Build a provenance-preserving evidence model

Use separate records for incident/container, source artifact, captured page/view, ad, campaign candidate, template, domain, identifier, image/video asset, entity mention, and funnel stage. Every extracted value should point to its source file and, where possible, HTML selector/configuration key or HAR entry.

Suggested graph predicates include `same_campaign_id`, `same_landing_id`, `same_template_id`, `exact_asset_match`, `near_duplicate_text`, `mentions_person_as`, `links_to`, `redirects_to`, `requests_resource_from`, `same_domain_different_view`, `navigation_history_contains`, and `similar_persuasion_pattern`.

Do not make a large person/domain/asset graph the only output. Analysts also need a plain-language pair explanation and a timeline/view of the funnel.

### B. Extract deterministic evidence first

1. Read saved HTML and HAR bodies offline so dead domains remain analyzable.
2. Parse `window.aio` and comparable embedded configuration objects.
3. Normalize hostnames, URL paths, link destinations, form fields, tracker namespaces and identifiers.
4. Compute exact asset hashes and canonical text fingerprints.
5. Preserve both raw and normalized values, with sensitive values excluded from the presentation.
6. Detect duplicate representations of one capture before scoring similarities.

This stage alone should recover the strongest relationships in cases 1/2, 2/3, 14 and 61 without an LLM inventing links.

### C. Add text, visual and behavioral comparison

Use word-shingle similarity for near-duplicates; semantic comparison for stories rewritten around different people/products; image hashing or embeddings for resized/cropped assets; OCR for ad screenshots; and sampled video/audio extraction for media-only cases. These are proposed next methods where not already performed in this report.

Separate article content, comments, navigation, registration controls and widget-generated country lists. Otherwise a shared phone-country selector or footer can dominate text similarity. For the five-gram comparison reported above, lowercased whitespace-normalized visible text was used; country-list text was removed where present during exploratory comparisons.

### D. Make multiple decisions rather than one score

For each new artifact, answer separately:

- Is it an exact duplicate representation?
- Is it a variant of a known page or offer?
- Is there sufficient evidence for the same campaign?
- Does it share a template or software ecosystem with another campaign?
- Does it share only a narrative or persuasion pattern?
- Is it currently unmatched, or is evidence insufficient?

A strong matching campaign UUID plus matching landing and content supports campaign membership. A matching template UUID with different campaign IDs supports a template edge. Generic health/financial language supports a category, not a campaign merge. Missing metadata should reduce certainty, not become negative evidence.

### E. Explain and validate each proposed grouping

Show the top specific evidence, meaningful differences, unavailable evidence, and rejected weak signals. Avoid adding several highly correlated features as if they were independent: headline similarity, article embedding and text-shingle overlap may all reflect the same underlying text.

Use constrained or hierarchical grouping with an explicit unmatched option. Allow a strong page match even when an incident folder is mixed. Human review should be able to accept a template relationship while rejecting a campaign merge.

## 16. Validation cases and success measures

The supplied brief assigns 30% to correlation/grouping quality, 30% to useful features and sources, 15% to end-to-end completeness, 15% to interpretability, and 10% to deployment potential/portability. It explicitly includes hidden test material with variants of existing campaigns and new campaigns. The following acceptance examples are proposed from this analysis; they are not official labels.

| Test | Expected behavior |
| --- | --- |
| Hold out case-2 Brzoska page; compare to case 1 | Recover the campaign match across changed domain and ad IDs |
| Compare case-2 healthlinkq response with case 3 | Detect the near-duplicate page despite minor date/number changes |
| Compare case 48 and 61 article | Explain the shared template; preserve distinct campaign records |
| Compare case-61 article and chat | Recover one funnel with different page roles and shared internal campaign |
| Compare the two case-14 landing variants | Recognize product/funnel continuity without erasing changed IDs |
| Compare cases 14 and 47 | Explain health-sales pattern; abstain from unsupported campaign merge |
| Process case 5 | Produce useful media features and an insufficient-evidence result |
| Process case 36 | Surface ad/content mismatch and script anomaly; avoid forced assignment |
| Remove live access | Continue using archived HTML/HAR/images |
| Add shared CDN/library evidence | Avoid merging otherwise unlinked cases |

Useful measured outcomes are pairwise campaign precision/recall, template-relation precision/recall separately, false-merge rate, correct unmatched/new-campaign decisions, evidence-source coverage, and explanation traceability. Report performance by available input type and missing-data condition.

A ten-folder collection is too small and too uneven to claim generalization or calibrated confidence. Prevent leakage by grouping duplicate captures and variants when preparing evaluation splits. Do not train and test on byte-identical assets from the same page without disclosing that overlap.

The best demonstration sequence is: ingest → recover 1/2 across domains → show 2/3 content bridge → distinguish 48/61 template reuse from campaign identity → reconstruct case 61 → handle the partial video-only case 5.

## 17. Unresolved questions and next evidence to collect

| Question | Why unresolved | Most useful next evidence |
| --- | --- | --- |
| Do the Brzoska and Olejnik pages share an operator? | Shared capture context and narrative are insufficient | Exact upstream/downstream IDs, redirect/referrer chain, or matching campaign configuration |
| Are cases 48 and 61 operated by one party or template customers? | Shared template but different internal owner/campaign IDs | Provider provenance, destination account/lead-routing identifiers, additional deployments |
| Why does Glowpixel show three different presentations? | Multiple snapshots do not isolate the switching variable | Controlled repeated captures with preserved time, URL, referrer, browser and session context |
| Is case 36 a benign-facing view of a different funnel? | Captured ad mismatch; no alternate consumer page observed | A documented journey from the same ad and contextual comparison |
| Where did the unusual shared integration scripts originate? | Embedded code alone does not establish provenance | Original response vs DOM/script comparison and capture-tool documentation |
| Does case 5 connect to either health-product offer? | Destination and product are absent | Full transcript, CTA frame, destination URL and matching video assets |
| Are public-figure appearances synthetic or edited? | Visual resemblance is not forensic verification | Original source footage, temporal/audio analysis and provenance evidence |
| Do any forms lead to the same downstream CRM/account? | Integration references and form schemas differ or remain incomplete | Authorized observation of routing metadata without real personal-data submission |
| Which dates describe acquisition, capture or fabricated copy? | Filenames, article timestamps and page metadata serve different roles | Original capture metadata and platform ad records with source-specific timestamps |

The supplied brief restricts organizer materials to the challenge and prohibits their public redistribution. This report is an internal challenge-analysis deliverable; source screenshots, raw HAR contents and secrets have not been reproduced as attachments.

## Appendix A. Selected exact fingerprints

| Artifact | Matching cases | SHA-256 |
| --- | --- | --- |
| `main.css` | 1, 2, 61 article | `f0269f85ac5085af7fe89a074d85d1176e12be144dfbdcd5cb8206e2631119c9` |
| `01-hero.webp` | 1, 2 | `a8c3663b9c190c321e2945b33ce23c15202b6226e47bb8c1847aeca4eb7898f6` |
| `news1.webp` | 48, 61 article | `0d1bb6520083e5f1bf85190cf66d8f3401f8aadb4462096b473c52be795e26e8` |
| `bundle.umd.min.js` | 48, 61 article/chat | `c1506fbd06e4be752c3a862c28ac58b9e0145909e197870ea939b30800ed1cd0` |
| Shared embedded public-key text, UTF-8 hash | 48, 61 article/chat | `b74212a23b39762a040e2d72401ddfe765982314e8e597edd17d02555e2d7a21` |

The public-key fingerprint establishes identical embedded key text only. The bundled JavaScript fingerprint may describe a reusable library. Neither should be treated as independent proof of common control.

## Appendix B. Source index

The source index below identifies the original local artifacts used to ground this report. References are relative to the supplied materials folder. Paths identify evidence, not live destinations to visit.

**Challenge brief:** `KolekcjonerFejków-opis(1).pdf`, five pages, supplied by the user; current full text read for this analysis. It defines the campaign-correlation task, hidden-set evaluation, scoring weights and restrictions.

### Case 1

- `1/Zrzut ekranu 2026-10-09 082411.png`
- `1/civicdaybook.live.har`
- `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl.html`
- `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.css`
- `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.js.pobrane`

### Case 2

- `2/acrosscommunities.live.har`
- `2/healthlinkq.pro.har`
- `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl.html`
- `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.css`
- `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.js.pobrane`

### Case 3

- `3/Zrzut ekranu 2026-10-09 105708.png`
- `3/«Próbowali mnie zniszczyć od lat — ale się nie zamknęłam»_ po ataku na jej samochód Monika Olejnik powiedziała jedno zdanie, które powtarza teraz cała Polska — TVN24.html`

### Case 5

- `5/Czytać [1982065065827084_3].info.json`
- `5/Czytać [1982065065827084_3].mp4`

### Case 10

- `10/App Store – Słodka Bonanza.html`
- `10/App Store – Słodka Bonanza_files/back-nav.js.pobrane`
- `10/IMG_3722.PNG`
- `10/IMG_3723.PNG`
- `10/IMG_3724.PNG`
- `10/IMG_3725.PNG`
- `10/IMG_3726.PNG`
- `10/IMG_3727.PNG`
- `10/shop-77vault.xyz.har`

### Case 14

- `14/IMG_3625.PNG`
- `14/IMG_3626.PNG`
- `14/IMG_3627.PNG`
- `14/IMG_3628.PNG`
- `14/IMG_3629.PNG`
- `14/IMG_3630.PNG`
- `14/IMG_3631.PNG`
- `14/IMG_3632.PNG`
- `14/IMG_3633.PNG`
- `14/IMG_3634.PNG`
- `14/Metabo RX.html`
- `14/Metabo RX_files/ajax_send.js.pobrane`
- `14/Metabo RX_files/reviews.js.pobrane`
- `14/ScreenRecording_08-12-2026 08-38-33_1.MP4`
- `14/ineptsolidoval_top/ineptsolidoval_top/dom.html`
- `14/ineptsolidoval_top/ineptsolidoval_top/manifest.json`
- `14/ineptsolidoval_top/ineptsolidoval_top/page.har`

### Case 36

- `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość.html`
- `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość_files/lazysizes.min.js.pobrane`
- `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość_files/main(1).js.pobrane`
- `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość_files/main.js.pobrane`
- `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość_files/script.js.pobrane`
- `36/Zrzut ekranu 2026-10-19 133115.png`
- `36/meroporepufulo.life.har`

### Case 47

- `47/Nagrywanie ekranu 2026-10-27 152823.mp4`
- `47/Uromexil Forte.html`
- `47/Uromexil Forte_files/video.js.pobrane`
- `47/shryxla-mynx.icu.har`

### Case 48

- `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą.html`
- `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main(1).js.pobrane`
- `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main.css`
- `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main.js.pobrane`
- `48/Zrzut ekranu 2026-10-28 122734.png`
- `48/novumfy.com.har`

### Case 61

- `61/reklama_1.png`
- `61/strona_1/.har`
- `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą.html`
- `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main(1).js.pobierz`
- `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main.css`
- `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main.js.pobierz`
- `61/strona_2/Trwały Działunek.html`
- `61/strona_2/Trwały Działunek_files/main(1).js.pobierz`
- `61/strona_2/Trwały Działunek_files/main.css`
- `61/strona_2/Trwały Działunek_files/main.js.pobierz`
- `61/strona_2/trwaly_dzialunek.har`
- `61/strona_3/Glowpixel.org — Portal analiz produkcji medialnej i treści cyfrowych.html`
- `61/strona_3/glowpixel.org.har`

## Appendix C. HAR coverage

| Case | Capture | Request entries |
| --- | --- | --- |
| 1 | `1/civicdaybook.live.har` | 31 |
| 10 | `10/shop-77vault.xyz.har` | 38 |
| 14 | `14/ineptsolidoval_top/ineptsolidoval_top/page.har` | 35 |
| 2 | `2/acrosscommunities.live.har` | 31 |
| 2 | `2/healthlinkq.pro.har` | 17 |
| 36 | `36/meroporepufulo.life.har` | 15 |
| 47 | `47/shryxla-mynx.icu.har` | 43 |
| 48 | `48/novumfy.com.har` | 40 |
| 61 | `61/strona_1/.har` | 13 |
| 61 | `61/strona_2/trwaly_dzialunek.har` | 34 |
| 61 | `61/strona_3/glowpixel.org.har` | 24 |

**Total: 11 captures, 321 request entries.** Request counts include resources, analytics, errors and other background traffic; they do not count independent user actions.

## Appendix D. All exact cross-case file-match groups found

Groups below exclude files of 100 bytes or less. The SHA-256 comparison covered all remaining supplied files. Same-case copies are listed where they occur in a cross-case group. This ledger includes routine dependencies so they can be explicitly discounted.

| SHA-256 prefix | Matching artifacts | Interpretation |
| --- | --- | --- |
| `00225bd1c540c66a` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/journalist-avatar.jpg`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/journalist-avatar.jpg` | Reusable asset; assess alongside contextual evidence |
| `0d1bb6520083e5f1` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news1.webp`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news1.webp` | Reusable asset; assess alongside contextual evidence |
| `100391ec2e837f8e` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/logo.svg`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/logo.svg` | Reusable asset; assess alongside contextual evidence |
| `125c484ab4cc16c4` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/avatar.webp`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/avatar.webp` | Reusable asset; assess alongside contextual evidence |
| `1bfc0456c8b2fc07` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news4.webp`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news4.webp` | Reusable asset; assess alongside contextual evidence |
| `31a24ebea13afd53` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/brzoska-avatar.jpg`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/brzoska-avatar.jpg` | Reusable asset; assess alongside contextual evidence |
| `7395269ab9869b17` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/nav-forum.svg`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/nav-forum.svg` | Reusable asset; assess alongside contextual evidence |
| `751feecde175d015` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/shot-pekao.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/shot-pekao.webp` | Reusable asset; assess alongside contextual evidence |
| `77bd3f93a8e61ed3` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/morawiecki-avatar.jpg`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/morawiecki-avatar.jpg` | Reusable asset; assess alongside contextual evidence |
| `8d3987dc0aad5a90` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/04-wywiad-2.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/04-wywiad-2.webp` | Reusable asset; assess alongside contextual evidence |
| `8e84b9894d9a38d5` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/2012165449475163`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/2012165449475163` | Same tracker configuration asset; corroborate numeric pixel match |
| `96af8dd4d3e69a1a` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/05-kacper.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/05-kacper.webp` | Reusable asset; assess alongside contextual evidence |
| `96b9eb0787c215dc` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news5.webp`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news5.webp` | Reusable asset; assess alongside contextual evidence |
| `97f9b10039b05e1a` | `14/Metabo RX_files/saved_resource(1).html`; `14/Metabo RX_files/saved_resource.html`; `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość_files/saved_resource(1).html`; `36/Poland Invest Summit 2026 – Gdzie Kapitał Spotyka Przyszłość_files/saved_resource.html`; `10/App Store – Słodka Bonanza_files/saved_resource(1).html` | Browser/support document; do not count as an incident link |
| `a64f052a56707838` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.js.pobrane`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.js.pobrane` | Identical application/runtime file; pair with explicit campaign IDs |
| `a8c3663b9c190c32` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/01-hero.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/01-hero.webp` | Reusable asset; assess alongside contextual evidence |
| `b188a5c4a50aa7b9` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/02-szpital.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/02-szpital.webp` | Reusable asset; assess alongside contextual evidence |
| `b7663a9fd0eb96ad` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/shot-pko.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/shot-pko.webp` | Reusable asset; assess alongside contextual evidence |
| `c13a5f2257ddf389` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/03-wywiad.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/03-wywiad.webp` | Reusable asset; assess alongside contextual evidence |
| `c1506fbd06e4be75` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/bundle.umd.min.js.pobrane`; `61/strona_2/Trwały Działunek_files/bundle.umd.min.js.pobierz`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/bundle.umd.min.js.pobierz` | Shared bundled library; supporting template evidence only |
| `cbdf0712673ad947` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/fbevents.js.pobrane`; `3/«Próbowali mnie zniszczyć od lat — ale się nie zamknęłam»_ po ataku na jej samochód Monika Olejnik powiedziała jedno zdanie, które powtarza teraz cała Polska — TVN24_files/fbevents.pobrane`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/fbevents.js.pobrane` | Common third-party dependency; negligible campaign specificity |
| `d43d7492f0e97c79` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/logo-gazeta.svg`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/logo-gazeta.svg` | Reusable asset; assess alongside contextual evidence |
| `d7119530896e9bbb` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news3.webp`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news3.webp` | Reusable asset; assess alongside contextual evidence |
| `e729addcfc1e4d81` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/06-bank-kacper.webp`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/06-bank-kacper.webp` | Reusable asset; assess alongside contextual evidence |
| `e87b32630cad07c9` | `48/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news2.webp`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/news2.webp` | Reusable asset; assess alongside contextual evidence |
| `f0269f85ac5085af` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.css`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/main.css`; `61/strona_1/37 minut po emisji wyprowadzono go w kajdankach. Nagranie usunięto przed północą_files/main.css` | Shared styling; template/runtime relationship |
| `fd6e7dce9fffe2f6` | `2/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/nav-email.svg`; `1/«Wiem, kto strzelał»_ zamach na Rafała Brzoskę, założyciela InPostu — Gazeta.pl_files/nav-email.svg` | Reusable asset; assess alongside contextual evidence |
| `ff1523fb7389539c` | `14/Metabo RX_files/jquery-3.6.0.min.js.pobrane`; `14/ineptsolidoval_top/ineptsolidoval_top/resources/script/jquery-3.6.0.min_ff1523fb.js`; `47/Uromexil Forte_files/jquery.min.js.pobrane` | Common third-party dependency; negligible campaign specificity |

No public-figure endorsement, medical efficacy, financial return, successful conversion, operator identity or live-domain status is established by this hash ledger.
