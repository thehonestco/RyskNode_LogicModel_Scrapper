"""
part1/scorecards/identity_score.py
===================================
Identity Consistency Score — measures cross-source identity confidence.

DIRECTION: Higher score = BETTER identity match (good score).

Formula (per spec)
------------------
  Component                          Weight   Signal source
  ────────────────────────────────── ──────   ──────────────────────────────────────────────
  1. Legal name match                  30 %   resolver name_match_grade
  2. GST–CIN/Udyam linkage             20 %   resolver linkage_status
  3. Address consistency               15 %   resolver address_match_grade
  4. Entity type consistency           10 %   resolver entity_type_match (bool)
  5. Registration date consistency     10 %   resolver reg_date_variance_days
  6. PAN / submitted ID consistency    15 %   resolver pan_match_grade

  identity_score = Σ (weight_i × component_score_i) / 100

resolver_result dict keys expected
-----------------------------------
  name_match_grade          : str  — "exact" | "strong_fuzzy" | "weak_fuzzy" | "mismatch"
  linkage_status            : str  — "full" | "partial" | "conflict"
  address_match_grade       : str  — "full" | "state_city" | "state_only" | "conflict"
  entity_type_match         : bool — True if entity class agrees across sources
  reg_date_variance_days    : int  — absolute day difference in registration dates
  pan_match_grade           : str  — "full" | "partial" | "mismatch"

  Legacy key still supported:
  identity_consistency_score : float — used as fallback if new keys absent (backward compat)

Reason codes
------------
  LEGAL_NAME_MISMATCH               — name_match_grade == mismatch
  IDENTIFIER_LINKAGE_CONFLICT       — linkage_status == conflict
  IDENTIFIER_LINKAGE_INCOMPLETE     — linkage_status == partial
  ADDRESS_INCONSISTENCY             — address_match_grade in {state_only, conflict}
  ENTITY_TYPE_CONFLICT              — entity_type_match == False
  REGISTRATION_DATE_CONFLICT        — reg_date_variance_days > 365
  PAN_ID_MISMATCH                   — pan_match_grade == mismatch
"""

from __future__ import annotations

from typing import Optional

from domain.schemas.normalized_record import NormalizedRecord
from domain.schemas.score_components import ComponentScore, DomainScore
from domain.scorecards.weighted_average import build_domain_score
from domain.compute.safe_math import clamp


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _score_name_match(grade: Optional[str]) -> tuple[float, str]:
    mapping = {
        "exact":        (100.0, "NAME_EXACT_MATCH"),
        "strong_fuzzy": (75.0,  "NAME_STRONG_FUZZY_MATCH"),
        "weak_fuzzy":   (40.0,  "NAME_WEAK_FUZZY_MATCH"),
        "mismatch":     (0.0,   "LEGAL_NAME_MISMATCH"),
    }
    return mapping.get(str(grade).lower(), (50.0, "NAME_MATCH_UNKNOWN"))


def _score_linkage(status: Optional[str]) -> tuple[float, str]:
    mapping = {
        "full":     (100.0, "LINKAGE_FULL_MATCH"),
        "partial":  (50.0,  "IDENTIFIER_LINKAGE_INCOMPLETE"),
        "conflict": (0.0,   "IDENTIFIER_LINKAGE_CONFLICT"),
    }
    return mapping.get(str(status).lower(), (50.0, "LINKAGE_STATUS_UNKNOWN"))


def _score_address(grade: Optional[str]) -> tuple[float, str]:
    mapping = {
        "full":       (100.0, "ADDRESS_FULL_MATCH"),
        "state_city": (70.0,  "ADDRESS_CITY_MATCH"),
        "state_only": (40.0,  "ADDRESS_INCONSISTENCY"),
        "conflict":   (0.0,   "ADDRESS_INCONSISTENCY"),
    }
    return mapping.get(str(grade).lower(), (50.0, "ADDRESS_MATCH_UNKNOWN"))


def _score_entity_type(match: Optional[bool]) -> tuple[float, str]:
    if match is True:
        return 100.0, "ENTITY_TYPE_CONSISTENT"
    if match is False:
        return 0.0,   "ENTITY_TYPE_CONFLICT"
    return 50.0, "ENTITY_TYPE_UNKNOWN"


def _score_reg_date(variance_days: Optional[int]) -> tuple[float, str]:
    if variance_days is None:
        return 50.0, "REG_DATE_UNKNOWN"
    if variance_days <= 7:
        return 100.0, "REG_DATE_CONSISTENT"
    if variance_days <= 90:
        return 50.0,  "REG_DATE_MINOR_VARIANCE"
    return 0.0, "REGISTRATION_DATE_CONFLICT"


def _score_pan(grade: Optional[str]) -> tuple[float, str]:
    mapping = {
        "full":    (100.0, "PAN_FULL_MATCH"),
        "partial": (50.0,  "PAN_PARTIAL_MATCH"),
        "mismatch":(0.0,   "PAN_ID_MISMATCH"),
    }
    return mapping.get(str(grade).lower(), (50.0, "PAN_MATCH_UNKNOWN"))


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def compute_identity_score(
    record: NormalizedRecord,
    resolver_result: dict,
) -> DomainScore:
    """
    Compute Identity Consistency Score (0–100; higher = better match).

    Parameters
    ----------
    record          : NormalizedRecord — canonical entity record
    resolver_result : dict — output from entity resolution step.
                      Expected keys: name_match_grade, linkage_status,
                      address_match_grade, entity_type_match,
                      reg_date_variance_days, pan_match_grade.
                      Falls back gracefully to legacy identity_consistency_score
                      key if new keys are absent.

    Returns
    -------
    DomainScore with 6 components, weights summing to 100.
    """
    # Backward-compatibility fallback — if resolver still returns old format
    _legacy = resolver_result.get("identity_consistency_score")
    _new_keys_present = "name_match_grade" in resolver_result

    components = []

    # 1. Legal name match (weight 30)
    if _new_keys_present:
        s, r = _score_name_match(resolver_result.get("name_match_grade"))
    else:
        s = clamp(float(_legacy or 50))
        r = "NAME_MATCH_LEGACY_FALLBACK"
    components.append(ComponentScore(
        component_name  = "legal_name_match",
        raw_value       = resolver_result.get("name_match_grade") or _legacy,
        normalized_score= s,
        weight          = 30.0,
        reason_code     = r,
    ))

    # 2. GST–CIN/Udyam linkage consistency (weight 20)
    if _new_keys_present:
        s, r = _score_linkage(resolver_result.get("linkage_status"))
    else:
        # Legacy fallback: derive from presence of identifiers
        ids_present = sum([
            1 if record.gstin else 0,
            1 if record.cin else 0,
            1 if record.udyam_no else 0,
        ])
        s = min(100.0, ids_present * 34.0)
        r = "LINKAGE_DERIVED_FROM_PRESENCE"
    components.append(ComponentScore(
        component_name  = "gst_cin_udyam_linkage",
        raw_value       = resolver_result.get("linkage_status"),
        normalized_score= s,
        weight          = 20.0,
        reason_code     = r,
    ))

    # 3. Address consistency (weight 15)
    if _new_keys_present:
        s, r = _score_address(resolver_result.get("address_match_grade"))
    else:
        s = 50.0
        r = "ADDRESS_MATCH_UNKNOWN"
    components.append(ComponentScore(
        component_name  = "address_consistency",
        raw_value       = resolver_result.get("address_match_grade"),
        normalized_score= s,
        weight          = 15.0,
        reason_code     = r,
    ))

    # 4. Entity type consistency (weight 10)
    if _new_keys_present:
        s, r = _score_entity_type(resolver_result.get("entity_type_match"))
    else:
        s = 50.0
        r = "ENTITY_TYPE_UNKNOWN"
    components.append(ComponentScore(
        component_name  = "entity_type_consistency",
        raw_value       = resolver_result.get("entity_type_match"),
        normalized_score= s,
        weight          = 10.0,
        reason_code     = r,
    ))

    # 5. Registration date consistency (weight 10)
    if _new_keys_present:
        s, r = _score_reg_date(resolver_result.get("reg_date_variance_days"))
    else:
        s = 50.0
        r = "REG_DATE_UNKNOWN"
    components.append(ComponentScore(
        component_name  = "registration_date_consistency",
        raw_value       = resolver_result.get("reg_date_variance_days"),
        normalized_score= s,
        weight          = 10.0,
        reason_code     = r,
    ))

    # 6. PAN / submitted ID consistency (weight 15)
    if _new_keys_present:
        s, r = _score_pan(resolver_result.get("pan_match_grade"))
    else:
        pan_score = 100.0 if record.pan else 0.0
        s = pan_score
        r = "PAN_PRESENT" if record.pan else "PAN_ID_MISMATCH"
    components.append(ComponentScore(
        component_name  = "pan_id_consistency",
        raw_value       = resolver_result.get("pan_match_grade") or record.pan,
        normalized_score= s,
        weight          = 15.0,
        reason_code     = r,
    ))

    return build_domain_score("identity", components)
