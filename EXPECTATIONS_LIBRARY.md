"""Expected-but-absent elements: User guide and architecture overview.

This document describes the page-kind expectations system: how the model generates
compliance profiles for different kinds of pages, how profiles are grounded in real
pages, and how to check views against profiles.

For technical implementation details, see EXPECTATIONS_IMPLEMENTATION.md.
For integration into the existing pipeline, see EXPECTATIONS_INTEGRATION.md.
"""

# Expected-but-absent elements: Compliance profiles and verification

## Overview

What a page *should* contain is worked out by the model, not written by hand. The system
implements **architecture §6, requirement d**: expected-but-absent elements.

### Why this matters

Operators who run fraudulent kits often omit things that legitimate pages always have:
- Real investment brokers show KNF registration and risk warnings
- Real pharmacies show ingredient lists and withdrawal policies
- Real news publishers host articles on their own domain and have author pages

A single missing element is weak evidence (many legitimate pages skip things). But the
*pattern* of omissions is a fingerprint: operators reuse the same incomplete template kit,
so they omit the same things every time.

The expected-elements vector is a **playbook-tier fingerprint** (`expected-elements.mo`).
It never groups campaigns directly (tier limit), but flags suspicion: a view whose expected
elements are mostly absent counts as risk evidence in the suspicion summary.

## How it works

### Step 1: Identify the page kind

The model receives the page text and names its kind in its own words.

> Examples: *"investment offer"*, *"supplement sales page"*, *"online casino listing"*,
> *"news article"*, *"app store listing"*, *"event page"*

Names are canonicalised into a small library. A new page kind creates a new entry instead
of being forced into an old category, so the system discovers genuinely novel page types
organically.

### Step 2: Generate an expectation profile (once per kind)

For each page kind, the model drafts what a legitimate operator addressing Polish consumers
would normally show. Each expected element has:

- **Name**: short identifier (e.g., `legal_entity_name`)
- **Description**: what the element is (English)
- **Basis**: why it is expected:
  - **Legal requirement**: Polish law or regulator mandate
  - **Regulator practice**: What regulated operators consistently do
  - **Customary**: Standard business practice (not legally mandated, but universal)
- **Typical Polish wordings**: 2–3 example texts that might appear for this element
- **Typical location**: Where on the page it usually sits (footer, header, form, etc.)

The profile is generated once, cached, and versioned. Caching makes profiles deterministic:
the same kind always produces the same profile.

### Step 3: Ground profiles in real pages (manual step)

A profile drafted from model knowledge alone can be wrong. So:

1. Capture a few genuine pages of the same kind
   - A real outlet's news article
   - A licensed broker's regulatory disclosures
   - A real pharmacy's product page

2. For each element, check whether genuine pages carry it:
   - *Element consistently present*: Keep in profile, mark as `promoted=True`
   - *Element consistently absent*: Drop from profile or downgrade
   - *Element absent from draft but present in all genuine pages*: Add it

This grounding makes the profile a description of what legitimate pages **actually**
carry, and it gives the false-alarm rate of each element.

> Example: "Ingredient list" might seem universally required for supplements, but
> if a quick check shows legitimate product pages often omit it (because the main
> page links to a PDF), the element is downgraded or removed.

### Step 4: Check a view against the profile

The model receives the profile and the view's visible text. For every element in the
profile, it returns:

- **`present`**: The element is in the page, with a quoted span
- **`absent`**: The element is nowhere in the page; places looked listed
- **`not_observable`**: Would sit below the visible area (screenshot-only view)

Only `absent` counts as a signal. `present` is neutral (expected pages always have
the element). `not_observable` is ignored (can't check visibility from text).

### Step 5: Code verifies

Every quoted span must actually occur in the view's visible text, or the judgement
is discarded:

```python
if quote not in page_text:
    discard the "present" claim
```

For each `absent`, the system re-searches for the profile's example wordings:

```python
if any typical wording found in page_text:
    send the element back for re-judgement
```

External facts are checked against regulator lists, not asserted by the model:

> Example: A page claims "supervised by KNF". The model does not decide if this is
> true. Instead, code checks the KNF's own published list of regulated entities.

### Output: Element vector

The result is a **three-state vector** for each view:

- **Element name** → (**state**, **quote** or **places looked**)

This vector is a fingerprint layer: `expected-elements.mo` (playbook-tier, **T4**).

Fingerprints are compared only between views of the same page kind, since they answer
different questions. Two investment-offer views that both lack risk warnings are related;
an investment offer and a news article can't be compared on elements.

## Group K: Expected elements in the signal catalogue

The system is tested against **signal-catalogue.md, group K**: the expected elements
that a manual review identified.

### By vertical

| Vertical | Expected elements |
|----------|-------------------|
| **Investment / trading** | Legal entity name and registered address; KNF authorisation or registry reference; risk warning; terms and privacy policy; data-controller clause and consent next to the form |
| **Supplement / medical product** | Mandatory "suplement diety" or medicine-leaflet notice; manufacturer or distributor; ingredient list; seller identity; withdrawal and returns terms; lowest-30-day-price note next to a discount |
| **Gambling** | Polish licence holder; 18+ and responsible-gambling notice; operator identity |
| **News article** | Host equals the publisher's real domain; working section navigation; imprint; author page; canonical / `og:url` on the publisher domain |
| **App-store listing** | Host is the real store domain; real developer page |
| **Event / conference** | Organiser legal entity; venue; ticket terms; dates in the future |
| **Any page with a form** | Privacy policy link; controller identity; consent checkbox; cookie notice |
| **Any ad** | Advertiser identity consistent with the destination |

### Verification

- **Generated profiles contain these elements** (automatically discovered, not given to model)
- **Every "present" quote is found** in the page text
- **Every "absent" is re-checked** by searching for example wordings

The expected elements themselves are not given to the model; they are the test of
whether the system discovers what it should.

## Wording: "Expected element not found", never "Law broken"

Signals in output use neutral language:

```
✗ RISK: expected element not found (legal requirement): legal entity name
  (looked in: footer, company info section)
```

Never:

```
✗ RISK: page violates Polish law by not showing company name
```

The system flags compliance **observations**, not legal violations. Analysts interpret
them, possibly with legal counsel. The code has no authority to declare laws broken.

## Related code

- `src/ftf/expectations/library.py`: Data models and versioned storage
- `src/ftf/expectations/induction.py`: Profile generation and caching
- `src/ftf/expectations/verification.py`: Quote verification and re-search
- `src/ftf/expectations/matching.py`: Per-view element checking
- `src/ftf/layers/expected_elements.py`: Fingerprint layer (`expected-elements.mo`)
- `src/ftf/expectations/cli.py`: Administrative commands

## CLI commands

```bash
# Generate a profile for a page kind
ftf expectations induct <kind_name> [--vertical <category>]

# List all known page kinds and their profiles
ftf expectations list [--version <version_id>]

# Check a view against a profile
ftf expectations check <view_id> <kind_name> [--version <version_id>]

# Ground a profile against genuine pages
ftf expectations ground <kind_name> <genuine_pages_dir>
```

## Related architecture

- **architecture.md §6**: Full design and rationale
- **architecture.md §7**: Enrichment layer (license verification checks regulator lists)
- **architecture.md §10**: Suspicion summary includes expected-element absences
- **signal-catalogue.md group K**: The test set of expected elements
