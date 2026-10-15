"""Our own expected-points model (not built yet).

Implement ``project`` with the same signature as ``baseline.project``. As soon as
``project`` is not ``None`` the CI model gate (``python -m fpl_optimizer.gate``)
backtests it on a full past season and fails the PR unless it beats both FPL form
and the baseline. Advice only switches to the model when ``projection_source`` in
``config/settings.json`` is ``"model"``, which the gate also refuses unless the model
passes.
"""

project = None
