"""Expected-elements integration: how the system fits into the Follow the Fake pipeline.

This document covers integration points with existing code and how to wire the
expectations library into the perception and fingerprinting tracks.
"""

# Expected-elements: Integration guide

## High-level integration

The expected-elements system fits into **architecture §6, requirement d** (Method track):

```
Perception       Evidence track extracts facts (page kind, text, entities)
    ↓
Expectations     This system: generate profiles, check views, verify results
    ↓
Fingerprint      Layer: "expected-elements.mo" (playbook-tier vector)
    ↓
Compare          Fingerprints compared within same page kind
    ↓
Group            Absent-element patterns help identify kit reuse
```

## Integration points

### 1. Perception track (page reading)

The perception track reads pages and extracts facts. The expectations system needs:
- **Page text** (visible text, not HTML)
- **Page kind** (what kind of page this is)

#### Current state

`src/ftf/perception/page.py` reads pages and returns observations:
- Entities, story skeleton, numeric claims, keywords
- Currently stores as `page.read_text` namespace
- No "page kind" observation yet

#### Integration needed

Add to `src/ftf/perception/page.py`:

```python
# In the page reading output model, add:
page_kind: str | None = None  # e.g., "investment offer", "supplement sales page"

# In SYSTEM_PROMPT, add instruction:
# "kind: the kind of page, in a few English words, such as 'fake news article'."
# (Already present in SYSTEM_PROMPT; extract and store as observation)

# After page reading, store observations:
observations.append(
    Observation(
        view_id=view.view_id,
        namespace="page.kind",
        value=result.kind,
        state=State.PRESENT,
        # ... metadata
    )
)
```

### 2. Expectations library initialization

Before fingerprinting, initialize the library:

```python
from pathlib import Path
from ftf.expectations import KindLibraryStore

# In pipeline initialization or config:
expectations_store_dir = Path(settings.cache_dir) / "expectations" / "profiles"
expectations_store = KindLibraryStore(expectations_store_dir)

# Store in a global or pass to fingerprinting stage
```

### 3. Fingerprinting (expected-elements.mo layer)

The `ElementsLayer` builds fingerprints during the fingerprint stage:

#### Current state

`src/ftf/layers/expected_elements.py` has a skeletal layer:
- Registered with `@layers.register`
- `build()` method returns None (placeholder)

#### Integration needed

Wire layer into fingerprinting:

```python
# In src/ftf/layers/expected_elements.py

from ..expectations import KindLibraryStore, check_view_against_profile
from ..registry import Context

@layers.register
class ElementsLayer:
    # ... metadata ...

    def build(self, view: View, observations: list[Observation], ctx: Context) -> Fingerprint | None:
        # 1. Extract page text and page kind from observations
        page_text = self._get_page_text(observations)
        page_kind = self._get_page_kind(observations)
        
        if not page_text or not page_kind:
            return None  # Can't check without both
        
        # 2. Load expectations store (from ctx or settings)
        # TODO: Pass store via ctx or settings
        store = KindLibraryStore(settings.cache_dir / "expectations" / "profiles")
        profile = store.get_kind_profile(page_kind)
        
        if not profile:
            return None  # No profile for this kind yet
        
        # 3. Call model to judge each element (async context available)
        from ..models import gateway  # or however model access is done
        vector, checks = await check_view_against_profile(
            view.view_id,
            page_text,
            page_kind,
            profile,
            settings=ctx.settings,
        )
        
        # 4. Build fingerprint from vector
        payload = vector.as_fingerprint_payload()
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload=payload,
            version=self.version,
        )

    def _get_page_text(self, observations: list[Observation]) -> str | None:
        for obs in observations:
            if obs.namespace == "page.read_text":
                return str(obs.value)
        return None

    def _get_page_kind(self, observations: list[Observation]) -> str | None:
        for obs in observations:
            if obs.namespace == "page.kind":
                return str(obs.value)
        return None
```

### 4. Pipeline runner

Expectations induction step runs before fingerprinting:

```python
# In src/ftf/pipeline.py or run script

from ftf.expectations import KindLibraryStore, generate_profile, cache_profile
from ftf import contracts

def run_induction(store: Store, settings: Settings) -> None:
    """Generate and cache page-kind profiles."""
    
    expectations_store = KindLibraryStore(
        Path(settings.cache_dir) / "expectations" / "profiles"
    )
    
    # For each view, identify kinds and generate profiles as needed
    for view in store.list_views():
        # Get page kind from perception observations
        obs = store.get_observations(view.view_id, "page.kind")
        if not obs:
            continue  # Perception didn't identify a kind
        
        page_kind = obs[0].value
        
        # Check if profile already exists
        profile = expectations_store.get_kind_profile(page_kind)
        if profile:
            continue  # Already have this profile
        
        # Generate new profile
        profile = await generate_profile(
            page_kind,
            settings,
            cache_dir=Path(settings.cache_dir) / "expectations",
        )
        
        # Cache it
        version_id = "1.0.0"  # TODO: Determine version numbering
        cache_profile(profile, expectations_store, version_id)
```

### 5. Comparator integration

The expectations vector uses the existing "shared_value" comparator:

```python
# In src/ftf/compare/shared_value.py (existing)

# The comparator already handles set fingerprints:
# compare([{element: "e1", state: "absent"}, {element: "e2", state: "present"}],
#        [{element: "e1", state: "absent"}, {element: "e3", state: "present"}])
# → returns Evidence with shared items

# No changes needed if fingerprints are only compared between same-kind views.
# (Comparison logic filters by page kind or the analyst limits comparison.)
```

**Important:** Fingerprints should only be compared between views of the same page
kind. The comparator should check:

```python
def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
    # For expected-elements fingerprints, check kind match
    if a.layer_id == "expected-elements.mo":
        # Extract page kinds from somewhere (needs design)
        # and only compare if kinds match
        pass
    # ... rest of comparison
```

Alternatively, the calling code (grouping or analyst UI) can enforce kind matching.

### 6. Analyst UI integration (future)

The Method screen in Streamlit will show element vectors:

```python
# In src/ftf/perception/ or UI code
from ftf.expectations import KindLibraryStore

def show_method_panel(view):
    """Display method observations and patterns."""
    
    store = KindLibraryStore(...)
    
    # Get fingerprints for this view
    fingerprints = ...  # from storage
    
    # Find the expectations fingerprint
    elem_fp = next(
        (fp for fp in fingerprints if fp.layer_id == "expected-elements.mo"),
        None
    )
    
    if elem_fp:
        st.markdown("### Expected elements")
        
        for item in elem_fp.payload:
            element_name = item["element"]
            state = item["state"]
            
            icon = "✓" if state == "present" else "✗"
            st.write(f"{icon} {element_name}: {state}")
```

## Testing integration

### Unit tests

Existing tests in `tests/test_expectations.py` are standalone:
- No dependency on Store, pipeline, or perception
- Can run independently with pytest

```bash
pytest tests/test_expectations.py -v
```

### Integration tests

New tests (to be added) would test the full pipeline:

```python
# tests/test_expectations_integration.py (future)

from ftf.perception.page import read_page
from ftf.expectations import KindLibraryStore, check_view_against_profile
from ftf.layers.expected_elements import ElementsLayer

def test_full_pipeline():
    """Test: perception → expectations → fingerprint."""
    
    # 1. Read a page
    view, observations = read_page(sample_view)
    
    # 2. Find page kind
    page_kind = next(o.value for o in observations if o.namespace == "page.kind")
    
    # 3. Generate and cache profile
    store = KindLibraryStore(tmpdir)
    profile = generate_profile(page_kind, settings, cache_dir)
    cache_profile(profile, store, "1.0.0")
    
    # 4. Check view
    vector, checks = check_view_against_profile(
        view.view_id,
        page_text,
        page_kind,
        profile,
        settings,
    )
    
    # 5. Build fingerprint
    layer = ElementsLayer()
    fp = layer.build(view, observations, ctx)
    
    assert fp is not None
    assert fp.layer_id == "expected-elements.mo"
    assert len(fp.payload) > 0
```

## Group K validation

After full integration, run the final test:

```bash
pytest tests/test_group_k_discovery.py -v
```

This validates that:
1. Generated profiles contain all Group K elements ✓
2. Every "present" quote is found in page text ✓
3. Every "absent" is re-checked by search ✓

## Troubleshooting

### "No profile for page kind"

**Symptom**: Layer returns None; no fingerprint built.

**Cause**: Page kind not in library.

**Solution**: Run induction first to generate profiles.

### "Quote not found in page text"

**Symptom**: Verification failed for a "present" claim.

**Cause**: Model provided a quote that doesn't occur in the page.

**Solution**:
1. Check if page text was truncated (MAX_TEXT_CHARS limit)
2. Check if model added/removed characters during quoting
3. Enable logging to see exact page text sent to model

### "Typical wording found, but was claimed absent"

**Symptom**: Re-search found typical wording; element sent back for re-judgement.

**Cause**: Model missed an element or typical wordings are too generic.

**Solution**:
1. Review typical wordings; remove if too broad
2. Ground profile against genuine pages to remove false-positive elements
3. Add notes to element for analyst reference

## Future enhancements

1. **Composite elements**: "Legal entity name AND registered address together"
   (currently just individual elements)

2. **Analyst grounding UI**: Web interface to ground profiles against genuine pages

3. **Element confidence scores**: Model's confidence in presence/absence judgement

4. **Multi-language profiles**: Support for other target-consumer languages

5. **Custom elements**: Analysts can add their own elements to profiles
