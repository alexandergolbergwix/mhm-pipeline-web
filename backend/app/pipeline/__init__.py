"""Pipeline core — MARC parse + authority matching + run execution.

Deliberately a thin interface so the real Mazal / VIAF / Wikidata / KIMA
adapters from the desktop pipeline can be swapped in one at a time
without touching the run lifecycle or the Review UI.
"""
