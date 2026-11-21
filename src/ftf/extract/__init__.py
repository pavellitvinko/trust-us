"""Extractors: scripted readers that turn a view's files into observations. Evidence track.

One module per signal family. Register with `@extractors.register`. An extractor must
never raise on bad input: return what it found, and nothing for what it could not read.
"""
