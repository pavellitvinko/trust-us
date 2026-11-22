"""Value weights for the shared-value and set-overlap comparators.

w(ns, value) = base weight of the namespace x rarity(value)

Rarity is counted over independent holders, not views: fifty deployments of one campaign
do not make its own pixel look common. Before campaigns exist, the registrable domains of
the views holding a value stand in for campaigns; once grouping has produced campaigns,
`fit` is called again with the view -> campaign map. A value held by many holders falls to
weight 0 and is reported as stoplisted. Demoted values (found on both sides of a
contradiction, docs/architecture.md section 9) are recorded here so fusion can move them
from identity to kit tier.

Comparators have no store access, so this is a module-level table that `fusion.run`
fits before comparing. Unfitted, it falls back to base weights, which keeps the
reference slice working on its own.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..contracts import Purpose, State
from .stoplist import is_seed_stoplisted

if TYPE_CHECKING:
    from ..store import Store

Key = tuple[str, str]

DEFAULT_BASE = 0.9

# Namespace base weights. Identity namespaces near 1; kit and infra namespaces lower, since a
# match there means "same toolkit", not "same operator". Unlisted namespaces get DEFAULT_BASE.
BASE_WEIGHTS: dict[str, float] = {
    # identity: campaign records
    "aio.campaign_uuid": 0.95,
    "aio.owner_uuid": 0.9,
    "aio.landing_uuid": 0.9,
    "aio.flow_uuid": 0.9,
    "nav.landing_uuid": 0.9,
    "aio.source_uuid": 0.85,
    "aio.folder_uuid": 0.8,
    "aio.campaign_human_id": 0.9,
    "aio.landing_human_id": 0.85,
    "aio.campaign_name": 0.85,
    "aio.landing_name": 0.85,
    "campaign.label": 0.85,
    "landing.label": 0.85,
    # identity: trackers
    "fb_pixel": 0.9,
    "gads_id": 0.9,
    "gtm_id": 0.85,
    "ga_id": 0.85,
    "taboola_id": 0.85,
    "uet_id": 0.85,
    "tiktok_pixel": 0.85,
    "ym_id": 0.85,
    "cdn.zone_id": 0.85,
    "gads_conversion": 0.85,
    "bing_uet": 0.85,
    "quora_pixel": 0.85,
    "ad.ad_id": 0.8,
    "ad.campaign_id": 0.8,
    "ad.adset_id": 0.8,
    "ad.account_label": 0.6,
    # identity: conversion
    "api_key_id": 0.85,
    "form_endpoint_host": 0.8,
    "form_endpoint_path": 0.5,
    "tracker.host": 0.7,
    "third_party.host": 0.7,
    "public_key": 0.85,
    "push_public_key": 0.85,
    "aff.offer_uuid": 0.6,
    "aff.tracker": 0.4,
    "aff.api_key": 0.85,
    "aff.sid": 0.5,
    "aff.x_id": 0.5,
    "aff.user_id": 0.8,
    "aff.offer_id": 0.6,
    "aff.landing_id": 0.6,
    "aff.network": 0.3,
    "contact.phone": 0.9,
    "contact.email": 0.9,
    "contact.handle": 0.9,
    "contact.wallet": 0.95,
    # identity: where the page lives
    "domain.registrable": 0.9,
    "url.host_path": 0.95,
    "outbound.domain": 0.7,
    "redirect.domain": 0.8,
    "ct.shared_cert": 0.6,  # a certificate the owner chose to share; hosters can do it too
    "brand.offer": 0.8,
    "offer.name": 0.8,
    "url.host": 0.9,
    # artifact
    "asset.sha256": 0.7,
    "asset.sha256.content": 0.7,
    "avatar.sha256": 0.6,
    "asset.sha256.avatar": 0.6,
    "asset.sha256.code": 0.6,
    "asset.name_shape": 0.3,
    "image.phash": 0.7,
    # kit
    "aio.template_uuid": 0.7,
    "aio.landing_type_uuid": 0.6,
    "config.key_path": 0.3,
    "api.route_shape": 0.5,
    "api.route": 0.5,
    "traffic_source": 0.4,
    "kit.cloaker": 0.5,
    "aio.source_name": 0.4,
    "aio.folder_name": 0.4,
    "url.path_shape": 0.5,
    "form.hidden_name": 0.3,
    "http.via": 0.2,
    "http.powered_by": 0.2,
    "url.param_name": 0.5,
    "url.param_shape": 0.4,
    "form.field": 0.3,
    "firstparty.sha256": 0.6,
    "code.comment_lang": 0.3,
    "code.naming": 0.3,
    "code.evasion_filter": 0.6,
    "dom.comment_lang": 0.3,
    "dom.evasion_filter": 0.6,
    # playbook
    "entity.cast": 0.6,
    "entity.role": 0.3,
    "layout.block": 0.4,
    # infra (weak)
    "dns.ns_pair": 0.4,
    "rdap.registrar": 0.2,
    "http.server": 0.2,
}


@dataclass(frozen=True)
class RarityCurve:
    """1 while a value has at most `full_until` holders, 0 from `zero_from` on, linear between."""

    full_until: int = 2
    zero_from: int = 6

    def __call__(self, holders: int) -> float:
        if holders <= self.full_until:
            return 1.0
        if holders >= self.zero_from:
            return 0.0
        return (self.zero_from - holders) / (self.zero_from - self.full_until)


# Views sharing a value here are one campaign record by definition, so they count as one
# holder from the first pass on. Otherwise a campaign deployed on six domains would make its
# own UUID look common and suppress it before grouping could ever see it.
BOOTSTRAP_NAMESPACES = ("aio.campaign_uuid",)


class WeightTable:
    def __init__(
        self,
        base: Mapping[str, float] | None = None,
        curve: RarityCurve | None = None,
        bootstrap_namespaces: tuple[str, ...] = BOOTSTRAP_NAMESPACES,
    ):
        self.base: dict[str, float] = dict(BASE_WEIGHTS if base is None else base)
        self.curve = curve or RarityCurve()
        self.bootstrap_namespaces = bootstrap_namespaces
        self.reset()

    def reset(self) -> None:
        try:  # coverage denominators follow the corpus the weights were fitted on
            from ..fusion.fuse import set_active_groups

            set_active_groups(None)
        except ImportError:  # pragma: no cover - import order during package init
            pass
        self.holders: dict[Key, int] = {}
        self.learned_stoplist: set[Key] = set()
        self.demoted: set[Key] = set()
        self.fitted = False

    # -- fitting ---------------------------------------------------------------------

    def fit(self, store: Store, campaigns: Mapping[str, str] | None = None) -> None:
        """Count independent holders per value. `campaigns` maps view_id -> campaign id;
        without it, registrable domains (falling back to the incident) stand in."""
        views = store.views()
        domains_by_view: dict[str, set[str]] = {}
        domains_by_incident: dict[str, set[str]] = defaultdict(set)
        observations = {v.view_id: store.observations(v.view_id) for v in views}
        for v in views:
            ds = {
                str(o.value)
                for o in observations[v.view_id]
                if o.namespace == "domain.registrable" and o.state is State.PRESENT
            }
            domains_by_view[v.view_id] = ds
            domains_by_incident[v.incident_id] |= ds

        provisional = self._bootstrap_campaigns(views, observations) if not campaigns else {}

        def holders_of(view) -> set[str]:
            if campaigns and view.view_id in campaigns:
                return {f"campaign:{campaigns[view.view_id]}"}
            if view.view_id in provisional:
                return {f"record:{provisional[view.view_id]}"}
            ds = domains_by_view[view.view_id] or domains_by_incident[view.incident_id]
            return {f"domain:{d}" for d in ds} or {f"incident:{view.incident_id}"}

        holders: dict[Key, set[str]] = defaultdict(set)
        for v in views:
            hs = holders_of(v)
            for o in observations[v.view_id]:
                if o.state is not State.PRESENT or o.purpose is Purpose.RISK:
                    continue
                holders[(o.namespace, str(o.value))] |= hs
        demoted = self.demoted
        self.reset()
        self.demoted = demoted
        self.holders = {k: len(hs) for k, hs in holders.items()}
        self.learned_stoplist = {k for k, n in self.holders.items() if self.curve(n) == 0.0}
        self.fitted = True

    def _bootstrap_campaigns(self, views, observations) -> dict[str, str]:
        """view -> provisional campaign id: views sharing a bootstrap-namespace value are one."""
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.setdefault(x, x) != x:
                x = parent[x]
            return x

        by_value: dict[Key, list[str]] = defaultdict(list)
        for v in views:
            for o in observations[v.view_id]:
                if o.namespace in self.bootstrap_namespaces and o.state is State.PRESENT:
                    by_value[(o.namespace, str(o.value))].append(v.view_id)
        for members in by_value.values():
            for other in members[1:]:
                ra, rb = find(members[0]), find(other)
                if ra != rb:
                    parent[rb] = ra
        return {v: find(v) for v in parent}

    # -- lookups ---------------------------------------------------------------------

    def rarity(self, namespace: str, value: str) -> float:
        if not self.fitted:
            return 1.0
        return self.curve(self.holders.get((namespace, value), 1))

    def is_stoplisted(self, namespace: str, value: str) -> bool:
        return is_seed_stoplisted(namespace, value) or (namespace, value) in self.learned_stoplist

    def base_weight(self, namespace: str) -> float:
        return self.base.get(namespace, DEFAULT_BASE)

    def weight(self, namespace: str, value: str) -> float:
        if self.is_stoplisted(namespace, value):
            return 0.0
        return self.base_weight(namespace) * self.rarity(namespace, value)

    def is_demoted(self, namespace: str, value: str) -> bool:
        return (namespace, value) in self.demoted


weights = WeightTable()
