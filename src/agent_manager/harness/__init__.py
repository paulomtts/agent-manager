"""Harness adapters and the launcher seam (design §4 lines 129-132, §8 lines
306-318, decisions D4 and D7).

A package marker only. `base.py` owns the adapter Protocol and the value types;
`launcher.py` owns the one launcher that is implemented. Re-exporting either
from here would give two import paths for one name, and a later adapter card
would pick whichever it saw first.
"""
