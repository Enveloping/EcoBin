"""On-demand, root-only helpers for the permanent device management plane.

Only the local ``ecobin-updater`` account can invoke these narrow primitives.
The updater does not call them in stage three, so no cloud or remote trigger
path is active while the permanent layer is installed and inspected.
"""
