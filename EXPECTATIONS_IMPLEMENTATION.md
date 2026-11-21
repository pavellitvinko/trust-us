"""Expected-elements implementation details and technical architecture.

This document covers the internal design of the expectations library: data structures,
algorithms, storage format, and integration points.
"""

# Expected-elements: Technical implementation

## Data structures

### Core models (library.py)

#### ElementBasis (enum)
Why an element is expected:
- `legal_requirement`: Polish law or regulator mandate
- `regulator_practice`: What regulated operators consistently do (practice, not law)
- `customary`: Standard business practice, universal but not legally mandated

#### TypicalWording (Pydantic model)
Example text that commonly appears for an element:
```python
TypicalWording(
    text="Polityka prywatności",
    location_hint="footer"
)
```

The model's judgement checks for these wordings when deciding if an element is `absent`.

#### ExpectedElement (Pydantic model)
One expected element for a page kind:
- `name`: Short identifier (snake_case), e.g., `legal_entity_name`
- `description`: What it is (English)
- `basis`: ElementBasis (legal/regulator/customary)
- `typical_wordings`: List of TypicalWording examples
- `typical_location`: Where it usually sits (e.g., "footer", "header", "form")
- `notes`: Optional notes for analysts

#### ElementProfile (dataclass)
Wraps an ExpectedElement with grounding metadata:
- `element`: The ExpectedElement
- `seen_in_genuine`: Count of genuine pages where this was present
- `total_genuine_checked`: Count of genuine pages checked
- `false_alarm_rate`: (1 - seen_in_genuine / total_genuine_checked)
- `promoted`: True if kept in profile (False if removed during grounding)
- `added_by`: Who added it ("model" or analyst name)
- `added_at`: When it was added

#### PageKind (Pydantic model)
A canonicalised page kind (e.g., "investment offer"):
- `kind_id`: Stable ID like `kind.1_investment_offer`
- `name`: Short name, e.g., `investment offer`
- `description`: What pages of this kind do
- `vertical`: Category for grouping (e.g., "Investment / trading", "Gambling")

#### KindProfile (Pydantic model)
Complete profile for one page kind:
- `kind`: The PageKind
- `elements`: List of ElementProfile entries
- `generated_from`: "model_knowledge" or "patterns"
- `grounded_on_genuine`: Count of genuine pages checked
- `created_at`: When profile was first generated
- `grounded_at`: When grounding was completed
- `notes`: Optional analyst notes

#### KindLibraryVersion (Pydantic model)
One version of the page-kind profiles library:
- `version_id`: Semantic version (e.g., "1.0.0")
- `profiles`: List of KindProfile entries
- `created_at`: Creation timestamp
- `input_hash`: Optional hash of input for reproducibility
- `merge_notes`: Notes on merges/splits from prior version

### Storage (KindLibraryStore class)

Persistent versioned storage in JSON files:

```
<store_dir>/
  kind-profiles-1.0.0.json
  kind-profiles-1.1.0.json
  kind-profiles-2.0.0.json
  ...
```

Each file is immutable once written. Versions are retrieved by lexicographic sort of
version IDs, so semantic versioning works correctly (1.0.0 < 1.1.0 < 2.0.0).

Methods:
- `save_version(library)`: Write a new version (raises if exists)
- `load_version(version_id)`: Load a specific version
- `latest_version()`: Get the most recent version
- `list_versions()`: List all version IDs
- `get_kind_profile(kind_name)`: Find a profile by kind name
- `find_profile_by_id(kind_id)`: Find a profile by stable ID

## Verification (verification.py)

### Quote verification

```python
def verify_quote_in_text(quote: str, page_text: str) -> bool
```

- Exact match first
- If no match, normalise whitespace and try again
- Used to validate every "present" claim

### Element text search

```python
def search_for_element_in_text(element: ExpectedElement, page_text: str) -> tuple[bool, Optional[str]]
```

- Searches for each typical wording as a case-insensitive regex
- Used to re-check "absent" claims
- Returns (found, matched_text)

### Element verification

```python
def verify_element(element, claimed_state, quote, page_text) -> VerificationResult
```

**For `PRESENT` state:**
- Quote must be provided
- Quote must be found in page text
- Verification fails if quote missing or not found

**For `ABSENT` state:**
- Re-search for typical wordings
- If any wording found, verification fails (needs re-judgement)
- Passes if no typical wordings found

**For `NOT_OBSERVABLE` state:**
- Always passes (can't verify visibility from text alone)

Returns `VerificationResult` with `verified` (bool) and `reason` (str).

### Profile verification

```python
def verify_profile_checks(profile, page_text, checks) -> ProfileCheckResult
```

Verify all element checks for a profile against page text.

Arguments:
- `profile`: KindProfile
- `page_text`: Visible page text
- `checks`: List of (element_name, state, quote) tuples from model

Returns `ProfileCheckResult` with:
- `checks`: List of verified checks
- `passed_elements()`: Elements that passed verification
- `failed_elements()`: Elements that failed verification
- `present_count()`, `absent_count()`, `not_observable_count()`

## Induction (induction.py)

### Profile generation

```python
async def generate_profile(page_kind, settings, cache_dir, vertical=None) -> KindProfile
```

Calls the model to generate a profile for a page kind. Currently a placeholder;
integration with the real model API pending.

**Prompts:**
- `induction_system_prompt()`: Expert persona (legal/regulatory compliance specialist)
- `induction_prompt(page_kind, vertical)`: Request for profile generation

The model responds with JSON containing expected elements. Code parses and creates
ExpectedElement objects.

### Kind ID generation

```python
def generate_kind_id(page_kind, vertical=None) -> str
```

Creates stable IDs like `kind.1_investment_offer` from page kind names.
Deterministic: same kind always gets same ID.

### Profile caching

```python
def cache_profile(profile, store, version_id, input_hash=None) -> KindLibraryVersion
```

Create a new library version with the profile and save it.

### Profile grounding

```python
def ground_profile_on_genuine_pages(profile, genuine_pages) -> KindProfile
```

Compare profile against genuine pages of the same kind. Update ElementProfile
metadata:
- `seen_in_genuine`: How many genuine pages had the element
- `false_alarm_rate`: 1 - (seen_in_genuine / total)
- `promoted`: False if false_alarm_rate too high

Placeholder implementation; full logic pending.

## Matching (matching.py)

### Element judgement

```python
class ElementJudgement
```

One element's judgement for a view:
- `element_name`: The element
- `state`: ElementChecked (present/absent/not_observable)
- `quote`: Quoted span if present
- `looked_in`: Where searched if absent

### View element vector

```python
class ViewElementVector
```

Complete element vector for one view:
- `view_id`: The view
- `page_kind`: Page kind this profile applies to
- `profile`: KindProfile
- `judgements`: List of ElementJudgement

Methods:
- `add_judgement(element_name, state, quote, looked_in)`
- `present()`, `absent()`, `not_observable()`: Filter by state
- `as_fingerprint_payload()`: Convert to set fingerprint format

**Fingerprint payload** (for `expected-elements.mo`):
```json
[
  {"element": "legal_entity_name", "state": "absent"},
  {"element": "risk_warning", "state": "present"},
  ...
]
```

Only PRESENT and ABSENT elements included; NOT_OBSERVABLE omitted.

### Element checking

```python
async def check_view_against_profile(view_id, page_text, page_kind_observed, profile, settings) -> (ViewElementVector, list[ElementCheckRecord])
```

Check a view against a profile:
1. Call model with `check_system_prompt()` and `check_prompt()`
2. Parse response as JSON with "checks" list
3. For each check, call `verify_element()` to validate against page text
4. Return vector and list of verified checks

Currently a placeholder; full model integration pending.

### Check prompts

**System prompt** (`check_system_prompt()`):
Instructs model to respond with ONLY JSON (no explanation):
```json
{
  "checks": [
    {"element_name": "legal_entity_name", "state": "absent", "quote": null, "looked_in": "footer, company info"}
  ]
}
```

**Check prompt** (`check_prompt(profile, page_text, page_kind_observed)`):
Provides profile elements and page text; asks model to judge each element.

## Fingerprint layer (layers/expected_elements.py)

### ElementsLayer class

Implements the `Layer` protocol:
- `name = "expected-elements.mo"`
- `version = "1"`
- `kind = FingerprintKind.SET` (set of items)
- `tier = Tier.PLAYBOOK` (T4: pattern of omissions, not identity)
- `group = "expectations"` (not counted twice)
- `comparator = "shared_value"` (compare which elements match)

**Method: `build(view, observations, ctx)`**

Builds fingerprint for a view:
1. Find "page.kind" observation (what kind of page is this)
2. Load matching KindProfile from library
3. Call `check_view_against_profile()` to get element judgements
4. Serialize as fingerprint payload (set of element/state pairs)
5. Return Fingerprint or None if unable to build

**Future integration:**
- perception.page will add "page.kind" and "page.clean_text" observations
- Layer will then be fully operational
- For now, skeletal (returns None) to allow registration

## Testing

### test_expectations.py

14 tests covering:
- Data model creation and serialization
- Library versioning and storage
- Quote verification and element searching
- Element checking (present/absent/not_observable)
- Profile check results and aggregation
- Element vectors and fingerprint payloads

### test_group_k_discovery.py

5 integration tests validating Group K elements:
- Investment offer profile contains 5 Group K elements
- Supplement product profile contains 6 Group K elements
- Gambling profile contains 3 Group K elements
- News article profile contains 5 Group K elements
- Form profile contains 4 Group K elements
- All profiles are versionable and storable
- Elements are verifiable in realistic page text

## Model integration

The system is designed to integrate with the existing gateway.py model API.

**Pending integration points:**

1. **Profile generation** (`induction.py`):
   ```python
   result = await gateway.call_structured_model(
       system_prompt=induction_system_prompt(),
       user_prompt=induction_prompt(page_kind, vertical),
       response_model=ProfileGenerationResponse,
       cache_dir=cache_dir,
   )
   ```

2. **Element checking** (`matching.py`):
   ```python
   result = await gateway.call_structured_model(
       system_prompt=check_system_prompt(),
       user_prompt=check_prompt(profile, page_text, page_kind_observed),
       response_model=ElementCheckResponse,
       cache_dir=cache_dir,
   )
   ```

Both callsites have `cache_dir` parameter ready for integration.

## Performance and scalability

**Versioning:**
- O(1) lookup by version ID
- O(n) to list all versions (n = number of versions, typically small)

**Profile lookup:**
- O(n) search by kind name (n = number of kinds per version, 10–100)
- O(1) lookup by kind_id (Pydantic dict key)

**Verification:**
- Quote search: O(m) where m = page text length
- Typical wording search: O(w * m) where w = number of wordings (2–3)

**Storage:**
- One JSON file per version (typically 50–500 KB)
- Immutable versioning allows safe concurrent reads

## Migration and compatibility

**Version numbering:** Semantic versioning (1.0.0, 1.1.0, 2.0.0, etc.)

**Breaking changes:**
- Change in ElementBasis values → major version bump
- New required field in ExpectedElement → major version bump
- Addition of optional field → minor version bump

**Upgrade path:**
When a breaking change occurs, old versions remain readable. Code can support
multiple major versions if needed (e.g., load 1.x and 2.x in the same run).
