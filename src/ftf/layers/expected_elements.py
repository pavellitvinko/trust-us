"""Expected-elements fingerprint layer: expected-elements.mo.

This layer represents the vector of expected elements checked for a view.
It records which expected elements are present, absent, or not observable.

Layer properties:
- Name: expected-elements.mo
- Kind: set (each element as a set item)
- Tier: T4 (playbook: patterns of omissions, not identity)
- Group: expectations (not counted twice)
- Comparator: shared_value (compare which elements are absent/present in common)

The layer is only meaningful when comparing views of the same page kind,
since different page kinds have different expected elements.
"""

from __future__ import annotations

from ..contracts import Fingerprint, FingerprintKind, Observation, Tier, View
from ..registry import Context, layers

# The kind profiles and the matching live in `ftf.expectations` (library, matching). They are
# imported here once `build` uses them: importing a missing name from a layer module breaks
# `load_plugins` and with it every run.


@layers.register
class ElementsLayer:
    """Expected-elements fingerprint layer for page-kind compliance."""

    name = "expected-elements.mo"
    version = "1"
    kind = FingerprintKind.SET
    tier = Tier.PLAYBOOK
    group = "expectations"
    comparator = "shared_value"

    def build(
        self, view: View, observations: list[Observation], ctx: Context | None = None
    ) -> Fingerprint | None:
        """Build the expected-elements fingerprint for a view.

        This layer requires:
        1. The page-kind observation (what kind of page this is)
        2. The page.read_text observation (the visible text)
        3. Access to the kind profiles library

        Process:
        1. Find the page-kind observation
        2. Load the matching kind profile
        3. Check the view against the profile
        4. Record element states (present/absent only; not_observable omitted)

        Args:
            view: The view being fingerprinted.
            observations: Observations collected for this view.
            ctx: Registry context for accessing the store. Optional: the pipeline calls
                `build(view, observations)`, as the `Layer` protocol declares.

        Returns:
            Fingerprint with set of (element, state) pairs, or None if unable to build.
        """
        # Find the page kind observation
        # TODO: This should come from perception.page observations
        # For now, return None to allow the layer to be optional
        return None

    def _get_page_text(self, observations: list[Observation]) -> str | None:
        """Extract page.read_text from observations."""
        for obs in observations:
            if obs.namespace == "page.read_text":
                return str(obs.value)
        return None

    def _get_page_kind(self, observations: list[Observation]) -> str | None:
        """Extract the page kind from observations."""
        # TODO: Check for a "page.kind" observation when available
        # For now, return None
        return None


# Note: Future integration points
# - perception.page will add "page.kind" and "page.clean_text" observations
# - When available, this layer will:
#   1. Extract the page kind
#   2. Load the kind profile from KindLibraryStore
#   3. Call check_view_against_profile() to get element judgements
#   4. Serialize as fingerprint payload
#
# For now, the layer is skeletal to allow registration and to demonstrate the interface.
