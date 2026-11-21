"""Enrichers: optional, cached lookups in outside sources (DNS, RDAP, regulator lists). Evidence track.

Register with `@enrichers.register`. A failed lookup yields a NOT_OBSERVABLE observation,
never a negative finding.
"""
