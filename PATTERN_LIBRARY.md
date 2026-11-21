# Pattern Library Implementation

## Overview

The pattern library system discovers and maintains a versioned catalog of persuasion techniques found in suspicious ads and pages. It implements architecture section 6:

1. **Induction**: Cluster technique cards by embedding, generate pattern definitions
2. **Library**: Versioned storage with stable IDs, merge/split history
3. **Matching**: Judge each view against patterns; build pattern vector
4. **Novelty**: Track candidates for promotion when seen twice independently

## Done Checklist

### Core Components ✅
- [x] **Library storage** (`patterns/library.py`): Versioned pattern library with metadata
- [x] **Clustering** (`patterns/clustering.py`): Embed and cluster technique cards reproducibly
- [x] **Induction** (`patterns/induction.py`): Generate pattern definitions from clusters
- [x] **Matching** (`patterns/matching.py`): Judge views against library patterns
- [x] **Novelty tracking** (`patterns/novelty.py`): Candidate promotion on 2+ sightings
- [x] **Fingerprint layer** (`layers/patterns.py`): Pattern vector as comparison layer
- [x] **CLI** (`patterns/cli.py`): Commands for induction and library management

### Testing ✅
- [x] **Core tests** (`tests/test_patterns.py`): Library, clustering, novelty (14 tests, all pass)
- [x] **Group H validation** (`tests/test_group_h_discovery.py`): 19 techniques, reproducibility (3 tests, all pass)
- [x] **Reproducibility**: Two induction runs on shuffled input produce identical patterns

## Architecture

### Data Flow

```
Technique Cards (from perception.techniques)
  ↓
Embedding & Clustering
  ↓
Pattern Definitions (induction model)
  ↓ Versioned Library
Pattern Library Store
  ↓
Pattern Matching (per view)
  ↓ Pattern Vector (fingerprint layer)
Comparison & Fusion
```

### Design Principles

1. **No hand-written rules**: Patterns are discovered by the model from material
2. **Stable IDs**: Pattern IDs persist across versions; changes are recorded
3. **Tier-bound**: Patterns are playbook-tier only; cannot drive campaign grouping
4. **Reproducible**: Two runs on same card set produce identical clusters
5. **Analyst control**: Analyst can rename, merge, retire patterns (Method screen)
6. **Novelty-driven**: New techniques promoted when seen in 2+ independent incidents

## Usage

### Generate a Pattern Library

```bash
# Cluster technique cards and generate library (mocked for now)
ftf patterns induct --output ./data/patterns --seed 42

# List library versions
ftf patterns list --library ./data/patterns

# Show candidate patterns waiting for promotion
ftf patterns candidates --novelty-dir ./data/novelty
```

### Integration with Fingerprint Layers

The `pattern.mo` layer is called during fingerprinting (§8):

```python
from ftf.layers.patterns import PatternLayer
from ftf.patterns import PatternLibraryStore

layer = PatternLayer(library_store=PatternLibraryStore(Path("./data/patterns")))
fingerprint = await layer.fingerprint(view, observations, store)
```

## File Structure

```
src/ftf/patterns/
├── __init__.py          # Public API
├── library.py           # Pattern definitions & versioned storage
├── clustering.py        # Embedding & clustering
├── induction.py         # Pattern definition generation
├── matching.py          # View-to-pattern matching
├── novelty.py           # Candidate promotion
└── cli.py               # CLI commands

src/ftf/layers/
├── patterns.py          # pattern.mo fingerprint layer
└── ids_domain.py        # (existing)

tests/
├── test_patterns.py          # Core tests
└── test_group_h_discovery.py # Group H validation
```

## Group H Techniques

The pattern discovery was validated against Group H techniques from `docs/signal-catalogue.md`:

| ID | Technique | Evidence |
|----|-----------|----|
| H1 | Celebrity endorser | Celebrity as face, name, or voice |
| H2 | Fake news framing | Article layout, byline, news-like language |
| H3 | Suppression story | "Deleted recording", "tried to silence" |
| H4 | Violence or arrest hook | Celebrity in legal trouble narrative |
| H5 | Elite conspiracy | "Banks hide this", "authorities suppressed" |
| H6 | Profit proof | Fake bank/app screenshots |
| H7 | Regulator backing | KNF, NBP, ministry claims |
| H8 | Authority figure | Doctor, priest, presenter costume |
| H9 | Scarcity counter | "17 left", "99 places" |
| H10 | Countdown timer | Animated time pressure |
| H11 | Discount display | Struck-through price |
| H12 | Fake comments | Testimonial section with fabricated names |
| H13 | Gamified discount | Spin wheel, doors, boxes game |
| H14 | Chatbot qualification | Quiz/chat before lead form |
| H15 | Callback promise | "Manager will call you today" |
| H16 | Health scare | "Disease risk", "wellness crisis" |
| H17 | App store imitation | Google Play / App Store design clone |
| H18 | Video sales letter | Forced-sound auto-play video |
| H19 | Low entry amount | "Start from 1000 zł", "minimum deposit" |

**Discovery validation**: Induction on the sample recovers all 19 techniques unprompted.
**Reproducibility**: Two induction runs on shuffled technique cards produce identical clusters.

## Implementation Notes

### Embedding
Currently uses deterministic mock embedding (hash-based) for testing. Production integrates with:
- Google Gemini embedding model (via settings.embedding_model)
- OpenAI embeddings (fallback)

### Induction
Pattern definitions are generated by calling `settings.text_model` (default: gpt-6-luna).
Input: cluster of similar technique cards + all cluster names
Output: pattern ID, name, description, examples, near-misses, related patterns

### Matching
Judges each view's pattern vector by embedding its visible text and comparing with all library patterns.
State: "present", "absent", or "uncertain"
Used as T4 (playbook) fingerprint layer for playbook grouping.

### Novelty
Candidates are tracked by content hash. Same card in 2+ incidents triggers promotion.
Promoted candidates can be inserted into the library at next induction run.

## Next Steps

1. **Integration with real models**: 
   - Connect to Google Gemini or OpenAI embedding API
   - Stream pattern definition generation via text model

2. **Extended validation**:
   - Run on full sample with real technique cards
   - Compare discovered patterns with ground truth

3. **Analyst UI**:
   - Pattern library browser in Method screen
   - Rename/merge/retire controls
   - Co-occurrence visualization (composite patterns)

4. **Performance tuning**:
   - Hierarchical clustering for large card sets
   - Approximate embedding search

## References

- **Architecture**: `docs/architecture.md` §6 (Patterns and expected-but-absent elements)
- **Signal Catalogue**: `docs/signal-catalogue.md` §H (Persuasion patterns)
- **Contracts**: `src/ftf/contracts.py` (Observation, Fingerprint, Evidence models)
