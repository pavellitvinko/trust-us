"""Kit-tier layers: the toolkit behind a page, which survives when every identifier is rotated.

A match supports *same family*, never same campaign. Each layer is its own correlation
group so that two independent kit signals (a config schema and a URL schema, say) count
twice, while two measures of the same thing do not.
"""

from __future__ import annotations

from ..compare.minhash import default_hasher
from ..contracts import Fingerprint, FingerprintKind, Observation, Purpose, State, Tier, View
from ..registry import layers
from ._set_layer import linkage_items, set_layer

set_layer(
    name="kit.config",
    tier=Tier.KIT,
    group="kit_config",
    comparator="set_overlap",
    namespaces=(
        "aio.template_uuid",
        "aio.landing_type_uuid",
        "aio.folder_name",
        "aio.source_name",
        "config.key_path",
        "api.route_shape",
        "api.route",
        "traffic_source",
        "kit.cloaker",
    ),
    doc="A2, B3, B6, B8: config-object key schema, template IDs, own-domain API route shapes.",
)

set_layer(
    name="kit.url",
    tier=Tier.KIT,
    group="kit_url",
    comparator="set_overlap",
    namespaces=("url.param_name", "url.param_shape", "url.path_convention", "url.path_shape"),
    doc="B1, B2, B5: non-standard landing-URL parameter names and value shapes; platform params stoplisted.",
)

set_layer(
    name="kit.form",
    tier=Tier.KIT,
    group="kit_form",
    comparator="set_overlap",
    namespaces=("form.field", "form.hidden_name"),
    doc="B4, I2: lead-form and hidden-field name set.",
)

set_layer(
    name="kit.code",
    version="2",
    tier=Tier.KIT,
    group="kit_code",
    comparator="set_overlap",
    namespaces=(
        "firstparty.sha256",
        "asset.sha256.code",
        "asset.name_shape",
        "code.comment_lang",
        "code.naming",
        "code.evasion_filter",
        "code.residue_lang",
        "dom.comment_lang",
        "dom.evasion_filter",
    ),
    doc="D3-D6, E5, E13: first-party file hashes, comment language, naming habits, evasion filter.",
)

set_layer(
    name="infra.host",
    tier=Tier.KIT,
    group="infra",
    comparator="shared_value",
    namespaces=(
        "dns.ns_pair",
        "rdap.registrar",
        "http.server",
        "http.via",
        "http.powered_by",
        "origin.asn",
        "video.hls_host",
    ),
    doc="C2, C3, C10, C11, M7: hosting fingerprints and the host a page streams video from. "
    "Weak: shared providers are common.",
)

# Classes added by frameworks, translators and browser extensions say nothing about the kit.
_FRAMEWORK_CLASS_PREFIXES = (
    "col-",
    "row",
    "container",
    "btn",
    "d-",
    "mt-",
    "mb-",
    "p-",
    "px-",
    "py-",
    "text-",
    "bg-",
    "flex",
    "grid",
    "w-",
    "h-",
    "goog-",
    "skiptranslate",
    "VIpgJd",
    "notranslate",
    "translated-",
)


@layers.register
class KitDom:
    """D2: CSS class-name set as a MinHash signature, framework and translator classes removed."""

    name = "kit.dom"
    version = "2"
    kind = FingerprintKind.HASH
    tier = Tier.KIT
    group = "kit_dom"
    comparator = "minhash"
    namespaces = ("dom.class", "dom.class_set")

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        values = [value for _, value in linkage_items(observations, ("dom.class",))]
        for o in observations:
            if (
                o.namespace == "dom.class_set"
                and o.state is State.PRESENT
                and o.purpose is not Purpose.RISK
                and isinstance(o.value, list)
            ):
                values.extend(v.strip() for v in o.value if isinstance(v, str))
        classes = {
            value for value in values if value and not value.startswith(_FRAMEWORK_CLASS_PREFIXES)
        }
        if len(classes) < 3:
            return None
        signature = default_hasher.signature(classes)
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={
                "algo": "minhash",
                "values": [str(x) for x in signature],
                "size": len(classes),
            },
            version=self.version,
        )
