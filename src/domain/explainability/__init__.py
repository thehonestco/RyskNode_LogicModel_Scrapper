"""
part2/explainability/__init__.py
================================
Public API for the RA Credit Risk XAI (Explainability) module.

Usage::

    from domain.explainability import CreditExplainer
"""

from .explainer import CreditExplainer

__all__ = ["CreditExplainer"]
