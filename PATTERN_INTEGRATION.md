# Pattern Library Integration Guide

## How Pattern Discovery Integrates with the Follow the Fake System

This document explains how the new pattern library system integrates with the existing architecture.

## Data Flow Integration

```
1. INGESTION & EXTRACTION
   Input: suspicious ads, pages, videos
   ↓
   Extract: facts via scripts (identifiers, kit, assets)
   Extract: technique cards via perception model (what_persuasion_techniques.py)
   ↓
   Observations table: view_id → {signal_id, namespace, value, state, source, ...}

2. PATTERN INDUCTION (New - perception track)
   Input: Observation records where namespace="technique.card"
   ↓
   Step 1: Cluster cards by embedding (patterns/clustering.py)
   Step 2: Induction model writes pattern definitions (patterns/induction.py)
   Step 3: Store versioned library (patterns/library.py)
   ↓
   Output: PatternLibraryStore with stable pattern IDs

3. FINGERPRINTING & COMPARISON
   For each view:
   ↓
   Pattern layer (layers/patterns.py):
     - Match view against library patterns
     - Produce pattern vector (presence/absence/uncertain for each pattern)
   ↓
   Evidence table: layer_id="pattern.mo" → {view_a, view_b, score, shared, differing}

4. FUSION & GROUPING
   For each view pair:
   ↓
   Fuse evidence from all layers (identity, artifact, kit, **playbook**)
   ↓
   Playbook tier includes:
     - Narrative embedding (F3, F4)
     - **Pattern vector (pattern.mo)**
     - Entity-masked embedding (F3)
   ↓
   Output: Relation with type={SAME_PLAYBOOK, ...}

5. ANALYST REVIEW (Method screen)
   Pattern library browser:
     - View all patterns in current version
     - See examples of each pattern
     - Rename, merge, retire patterns (analyst control)
     - View composite patterns (techniques appearing together)
   ↓
   Updated patterns propagate to next induction run
```

## Contract Changes

No changes to frozen contracts (`src/ftf/contracts.py`). Pattern system works within existing:

- **Observation**: technique cards stored as namespace="technique.card"
- **Fingerprint**: pattern vector stored as layer_id="pattern.mo", kind=SET
- **Evidence**: pattern comparison produces standard Evidence records
- **Relation**: playbook-tier relations include pattern similarity

## API Integration Points

### 1. Perception Track: Reading Technique Cards

Existing code in `perception/techniques.py` already extracts technique cards:

```python
# This already exists and works
technique_cards = run_techniques(view, observations, settings)  # returns TechniqueCard list

# Cards stored as:
Observation(
    view_id=view.view_id,
    signal_id="H",  # group H: patterns
    namespace="technique.card",
    value={"name": "...", "description": "...", "evidence": [...]},
    state=State.PRESENT,
    source=Source(file=..., locator=...),
    extractor="perception.technique",
    version="1",
)
```

### 2. Pattern Library: Gather and Induct

New code gathers cards and inducts library:

```python
from ftf.patterns import gather_technique_cards, cluster_cards_reproducible
from ftf.patterns.induction import run_induction
from ftf.patterns import PatternLibraryStore

# In a perception pipeline or CLI command:
library_store = PatternLibraryStore(Path(settings.data_dir) / "patterns")

cards = gather_technique_cards(store)  # from observation records
clusters = cluster_cards_reproducible(cards, seed=42)
library_version = await run_induction(settings, store, clusters)
library_store.save_version(library_version)
```

### 3. Fingerprinting: Pattern Layer

New layer called during fingerprinting pass:

```python
from ftf.layers.patterns import PatternLayer

layer = PatternLayer(library_store=library_store)

for view in store.views():
    observations = store.observations(view.view_id)
    fingerprint = await layer.fingerprint(view, observations, store)
    if fingerprint:
        store.put_fingerprint(fingerprint)
```

### 4. Comparison: Pattern Evidence

Pattern fingerprints compared like any other layer:

```python
# In the comparison stage (e.g., from Method.compare_views)
fp_a = store.get_fingerprint(view_a_id, "pattern.mo")
fp_b = store.get_fingerprint(view_b_id, "pattern.mo")

if fp_a and fp_b:
    evidence = pattern_layer.compare(fp_a, fp_b)
    # evidence.score: 0..1 based on shared patterns
    # evidence.shared: patterns both views show
    # evidence.differing: patterns only one shows
```

### 5. Fusion: Playbook Grouping

Existing fusion layer already supports playbook evidence:

```python
# In ftf/fusion/__init__.py
# Pattern evidence (layer="pattern.mo") is tier=T4 (playbook)
# Fused with other T4 signals:
#   - Narrative embedding (F3)
#   - Entity-masked embedding (F3)
#   - Page kind expectation profile (K)

relations = fuse_evidence(
    evidence_records=[...],  # includes pattern.mo Evidence
    tier_weights={T1: 0.6, T2: 0.3, T3: 0.1, T4: 0.05},  # T4 lower weight (grouping)
)
# Returns: Relation with type=SAME_PLAYBOOK, confidence, reasons
```

## Filesystem Layout

```
data/
├── ftf.sqlite                    # Store (existing)
├── model_cache/                  # Model call cache (existing)
│   └── perception.technique/     # Technique card generation
│   └── pattern.induction/        # Pattern definition generation
│
├── patterns/                     # Pattern library (NEW)
│   ├── index.json                # Version index
│   ├── versions/
│   │   ├── v1.0.0.json          # Library version snapshot
│   │   ├── v1.0.1.json
│   │   └── v1.1.0.json
│   │
│   └── [pattern definitions persist here]
│       - pattern_id: stable across versions
│       - name, description (analyst-editable)
│       - metadata: created_in, last_seen, analyst_renamed_from
│
└── novelty/                      # Candidate tracking (NEW)
    ├── candidates.json           # Cards waiting for 2nd sighting
    └── promoted.json             # Candidates promoted to library
```

## CLI Commands

```bash
# Extract technique cards (from perception track - existing)
ftf perceive sampling_sample/

# NEW: Generate pattern library
ftf patterns induct \
  --sample ./data \
  --output ./data/patterns \
  --n-clusters 15 \
  --seed 42

# NEW: List patterns
ftf patterns list --library ./data/patterns

# NEW: Show candidates ready for promotion
ftf patterns candidates --novelty-dir ./data/novelty
```

## Testing Strategy

**Existing tests** (already pass):
- `tests/test_perception_techniques.py`: Technique card extraction

**New tests** (all pass):
- `tests/test_patterns.py` (14 tests): Library, clustering, novelty
- `tests/test_group_h_discovery.py` (3 tests): Group H validation, reproducibility

**End-to-end** (future):
- Ingest sample → extract technique cards → induct patterns → fingerprint views → compare → fuse

## Configuration

Settings in `.env`:

```bash
# Existing
OPENAI_API_KEY=...
GEMINI_API_KEY=...
FTF_DATA_DIR=./data
FTF_OPENAI_MODEL=gpt-6-luna
FTF_EMBEDDING_MODEL=gemini-embedding-2
FTF_VIDEO_MODEL=gemini-4-hydrogen

# NEW (not needed yet, using mocks):
# FTF_PATTERN_LIBRARY_DIR=./data/patterns
# FTF_PATTERN_INDUCTION_K=15
```

## Extending the System

### Adding a New Pattern Source (e.g., from visual analysis)

```python
# 1. Create new extractor in ftf/perception/visual_patterns.py
def run_visual_patterns(view, images, settings):
    # Call vision model to extract visual persuasion techniques
    return [card1, card2, ...]  # TechniqueCard objects

# 2. Store as observations
for card in cards:
    obs = Observation(
        view_id=view.view_id,
        signal_id="H",
        namespace="technique.card",
        value={...},  # same card structure
        extractor="perception.visual_patterns",
        version="1",
    )
    store.put_observation(obs)

# 3. Gather and induct (same pipeline picks up new cards)
cards = gather_technique_cards(store)  # gets both text and visual cards
```

### Integrating with Composite Patterns

```python
# When two patterns frequently co-occur, create a composite:
composite = CompositePattern(
    composite_id="composite.1_investment_scam",
    technique_ids=["pattern.1_celebrity", "pattern.2_fake_news", "pattern.6_profit_proof"],
    name="Classic Investment Scam",
    description="Celebrity + news framing + profit screenshot",
    frequency=12,  # seen together in 12 views
)

# Composites appear in library version and help explain playbook similarity
```

## Performance Considerations

**Clustering**:
- Current: ~50 technique cards, 15 clusters, <0.1s
- Scales: K-means O(n*k*i) where n=cards, k=clusters, i=iterations
- For 1000s of cards: use hierarchical clustering or approximate methods

**Matching**:
- Current: mock embedding, ~1ms per view
- With real API: ~100ms per view (network latency)
- Batch embeddings to reduce API calls

**Storage**:
- Library versions: ~10KB per version (50 patterns)
- Fingerprints: ~5KB per view (50 patterns × metadata)
- Candidates: grows slowly (~1KB per unique card)

## Troubleshooting

**Q: "No library available" when fingerprinting**
A: Run `ftf patterns induct` first to generate and save library version

**Q: Clustering produces different results on second run**
A: Check if using `cluster_cards_reproducible()` with same seed=42

**Q: Technique cards not appearing in observations**
A: Run `ftf perceive` first to extract technique cards via perception model

**Q: Pattern matching always returns "uncertain"**
A: Mock implementation; needs integration with actual text model

## References

- **Architecture**: `docs/architecture.md` §6 (Patterns) and §8 (Fingerprints)
- **Contracts**: `src/ftf/contracts.py` (Observation, Fingerprint, Evidence, Relation)
- **Implementation**: `PATTERN_LIBRARY.md` and `PATTERN_LIBRARY_IMPLEMENTATION.md`
- **Tests**: `tests/test_patterns.py`, `tests/test_group_h_discovery.py`
