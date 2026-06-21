"""Weighted average engine — aggregates component scores into a domain score."""

from domain.schemas.score_components import ComponentScore, DomainScore


def compute_weighted_score(components: list[ComponentScore]) -> float:
    """
    Weighted average score = sum(score * weight) / sum(weights)
    All scores must be 0-100 normalized before calling this.
    """
    total_weight = sum(c.weight for c in components)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(c.normalized_score * c.weight for c in components)
    return round(weighted_sum / total_weight, 2)


def build_domain_score(domain: str, components: list[ComponentScore]) -> DomainScore:
    """Build a DomainScore from a list of components."""
    weighted_score = compute_weighted_score(components)
    top_reasons = [c.reason_code for c in sorted(components, key=lambda x: x.weight, reverse=True) if c.reason_code][:3]
    available = sum(1 for c in components if c.raw_value is not None)
    sufficiency = "full" if available == len(components) else "partial" if available > 0 else "insufficient"
    return DomainScore(
        domain=domain,
        components=components,
        weighted_score=weighted_score,
        top_reason_codes=top_reasons,
        data_sufficiency=sufficiency,
    )
