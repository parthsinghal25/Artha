"""Base archetype definition and generator framework for Phase 2 bulk scaling."""
import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ..tz import MELBOURNE, melbourne_abbr

DIAGNOSTIC_LEAK_PATTERNS = [
    re.compile(r"\b(ROOT_CAUSE|CAUSE_ID|DIAGNOSTIC|METRIC_ORACLE|GOLD_LABEL|INCIDENT_ID|WORLD_TRUTH)\b", re.IGNORECASE),
    re.compile(r"\b(SYSTEMIC_DEFECT_ID|EVIDENCE_ASSERTION_ID|ORACLE_ANSWER)\b", re.IGNORECASE),
    re.compile(r"\b(C-\d+-D\d+)\b"),  # Private authoring staging card IDs like 11-D08
]


@dataclass
class ArchetypeCaseContext:
    case_reference: str
    member_reference: str
    request_reference: str
    member_name: str
    start_date: str  # YYYY-MM-DD
    observation_end: str = "2026-10-01"
    seed: int = 42
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedArtifactSpec:
    artifact_id: str
    filename: str
    media_type: str
    created_at_iso: str
    sender_name: str
    sender_email: str
    recipient_name: str
    recipient_email: str
    title: str
    payload_type: str  # 'pdf_doc', 'pdf_letter', 'xlsx', 'csv', 'eml', 'call_txt', 'jpeg_receipt'
    spec: Dict[str, Any]
    attachments: List[str] = field(default_factory=list)


class BaseComplaintArchetype(ABC):
    """Abstract base class for all superannuation complaint archetypes."""

    archetype_id: str = "base"
    archetype_title: str = "Base Complaint Archetype"

    def __init__(self, context: ArchetypeCaseContext):
        self.ctx = context
        self._rng = self._init_prng(context.seed)

    def _init_prng(self, seed: int):
        import random
        r = random.Random()
        r.seed(seed)
        return r

    def get_deterministic_id(self, prefix: str, suffix: str) -> str:
        h = hashlib.sha256(f"{self.ctx.case_reference}:{prefix}:{suffix}:{self.ctx.seed}".encode()).hexdigest()[:12]
        return f"{prefix}-{h}"

    def build_iso_timestamp(self, day_offset: int, hour: int = 10, minute: int = 0) -> str:
        """Construct exact Melbourne-time ISO 8601 string with authentic AEST/AEDT offset."""
        base_str = self.ctx.start_date.split("T")[0]
        base_parts = [int(p) for p in base_str.split("-")]
        base_dt = datetime(base_parts[0], base_parts[1], base_parts[2], hour, minute, 0, tzinfo=MELBOURNE)
        dt = base_dt + timedelta(days=day_offset)
        iso = dt.isoformat()
        # Verify against melbourne_abbr to ensure authentic offset
        melbourne_abbr(iso)
        return iso

    def build_envelope(
        self,
        record_id: str,
        logical_id: str,
        recorded_at: str,
        available_at: Optional[str] = None,
        occurred_at: Optional[str] = None,
        occurrence_date: Optional[str] = None,
        view_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Construct a schema-compliant envelope conforming to runtime.schema.json 0.2.0."""
        if available_at is None:
            available_at = recorded_at
        return {
            "record_id": record_id,
            "logical_id": logical_id,
            "supersedes_record_id": None,
            "recorded_at": recorded_at,
            "available_at": available_at,
            "occurred_at": occurred_at,
            "occurrence_date": occurrence_date,
            "access": {
                "view_ids": view_ids or ["complaints-leadership"],
                "classification": "standard",
                "redaction_state": "none"
            },
            "evidence_refs": []
        }

    def build_case_record(
        self,
        case_id: str,
        party_ids: List[str],
        channel: str,
        product_or_journey: str,
        manager_reference: str,
        original_receipt_date: str,
        complaints_receipt_date: str,
        broad_tags: List[str],
        recorded_at: str,
        occurred_at: Optional[str] = None,
        available_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """Construct a schema-compliant native case record conforming to runtime.schema.json 0.2.0."""
        return {
            "schema_version": "0.2.0",
            "kind": "case",
            "envelope": self.build_envelope(
                record_id=f"NAT-{case_id}-case-r1",
                logical_id=f"CASE-{case_id}",
                recorded_at=recorded_at,
                available_at=available_at or recorded_at,
                occurred_at=occurred_at,
                occurrence_date=None
            ),
            "data": {
                "case_id": case_id,
                "party_ids": party_ids,
                "channel": channel,
                "product_or_journey": product_or_journey,
                "manager_reference": manager_reference,
                "original_receipt_date": original_receipt_date,
                "complaints_receipt_date": complaints_receipt_date,
                "broad_tags": broad_tags
            }
        }

    def build_party_record(
        self,
        party_id: str,
        party_role: str,
        member_reference: Optional[str],
        recorded_at: str,
        available_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """Construct a schema-compliant native party record conforming to runtime.schema.json 0.2.0."""
        return {
            "schema_version": "0.2.0",
            "kind": "party",
            "envelope": self.build_envelope(
                record_id=f"NAT-{party_id}-party-r1",
                logical_id=f"PARTY-{party_id}",
                recorded_at=recorded_at,
                available_at=available_at or recorded_at,
                occurred_at=None,
                occurrence_date=None
            ),
            "data": {
                "party_id": party_id,
                "party_role": party_role,
                "member_reference": member_reference,
                "support_preferences": []
            }
        }

    @abstractmethod
    def generate_artifact_specs(self) -> List[GeneratedArtifactSpec]:
        """Generate the ordered chronological sequence of artifacts."""
        pass

    @abstractmethod
    def generate_native_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate the native database records (case, party rows conforming to runtime.schema.json 0.2.0)."""
        pass

    def validate_zero_leakage(self, text: str, source_identifier: str = "") -> List[str]:
        """Ensures NO diagnostic tags, root cause labels, or private gold appear in generated text."""
        findings = []
        for pat in DIAGNOSTIC_LEAK_PATTERNS:
            matches = pat.findall(text)
            if matches:
                findings.append(f"Leak detected in {source_identifier}: {matches}")
        return findings
