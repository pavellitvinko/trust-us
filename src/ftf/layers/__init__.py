"""Fingerprint layers: one per semantic layer of a view. Method track.

Register with `@layers.register`. A layer returns None when its inputs are missing, so
the comparison leaves it out instead of treating it as a difference.
"""
