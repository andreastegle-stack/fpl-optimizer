"""Projection methods. Each maps history to expected points per player per gameweek.

All methods share one signature::

    project(matches, fixtures, players, target_gws, deadline) -> DataFrame
        index: element id, columns: target gameweeks, values: expected points
"""

from . import baseline, form, model


def methods(include_model: bool = True) -> dict:
    found = {"form": form.project, "baseline": baseline.project}
    if include_model and model.project is not None:
        found["model"] = model.project
    return found
