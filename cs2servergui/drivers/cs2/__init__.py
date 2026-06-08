"""
cs2servergui/drivers/cs2/ — Counter-Strike 2 driver.

The first concrete GameDriver subclass.  For v0.13.0 this is a thin
identity shell — the heavy logic still lives in core.py / web.py /
veto.py and is being moved into this directory one seam at a time.

See cs2servergui/drivers/__init__.py for the migration strategy.
"""
from .driver import CS2Driver

__all__ = ["CS2Driver"]
