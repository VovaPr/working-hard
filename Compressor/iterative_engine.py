"""Shared iterative search engine for compression loops.

This module contains only orchestration mechanics. Format-specific encode,
decision, and persistence logic remains in per-format adapters.
"""


def run_iterative_search(*, max_iterations, run_step_fn, handle_outcome_fn, on_max_iterations_fn):
    """Run a bounded iterative loop.

    Contract:
    - run_step_fn(step) returns step_result or None (None stops immediately).
    - handle_outcome_fn(step_result) returns an action string.
    - action == "done" stops immediately.
    - any other action continues.
    - if loop exhausts iterations, on_max_iterations_fn() is called once.
    """
    for step in range(1, max_iterations + 1):
        step_result = run_step_fn(step)
        if step_result is None:
            return

        action = handle_outcome_fn(step_result)
        if action == "done":
            return

    on_max_iterations_fn()
