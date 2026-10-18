"""Registry of complaint archetypes for Phase 2 bulk scaling."""
from typing import Dict, Type

from .admin_fees import AdminFeesAndRolloversArchetype
from .base import ArchetypeCaseContext, BaseComplaintArchetype
from .death_benefits import DeathBenefitArchetype
from .pension_disputes import PensionDisputeArchetype
from .tpd_insurance import TPDInsuranceArchetype

ARCHETYPE_REGISTRY: Dict[str, Type[BaseComplaintArchetype]] = {
    DeathBenefitArchetype.archetype_id: DeathBenefitArchetype,
    TPDInsuranceArchetype.archetype_id: TPDInsuranceArchetype,
    PensionDisputeArchetype.archetype_id: PensionDisputeArchetype,
    AdminFeesAndRolloversArchetype.archetype_id: AdminFeesAndRolloversArchetype,
}


def get_archetype_class(archetype_id: str) -> Type[BaseComplaintArchetype]:
    if archetype_id not in ARCHETYPE_REGISTRY:
        raise KeyError(f"Unknown archetype '{archetype_id}'. Available: {list(ARCHETYPE_REGISTRY.keys())}")
    return ARCHETYPE_REGISTRY[archetype_id]


def create_archetype_instance(archetype_id: str, context: ArchetypeCaseContext) -> BaseComplaintArchetype:
    cls = get_archetype_class(archetype_id)
    return cls(context)
