"""Observation-plan lifecycle shared by event-driven structure decisions."""

from .lifecycle import advance_observation_state, observation_state

__all__ = ["advance_observation_state", "observation_state"]
