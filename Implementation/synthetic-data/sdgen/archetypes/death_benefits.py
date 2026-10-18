"""Archetype 1: Death Benefit Distribution Disputes.
Covers conflicting beneficiary challenges, spouse vs adult child dependency tests,
probate/letters of administration delays, and trustee distribution notices.
"""
from typing import Any, Dict, List

from .base import ArchetypeCaseContext, BaseComplaintArchetype, GeneratedArtifactSpec


class DeathBenefitArchetype(BaseComplaintArchetype):
    archetype_id = "death_benefit_distribution"
    archetype_title = "Death Benefit Distribution Dispute"

    def generate_artifact_specs(self) -> List[GeneratedArtifactSpec]:
        ctx = self.ctx
        p = ctx.parameters
        deceased_name = p.get("deceased_name", "Arthur Pendelton")
        death_date = p.get("death_date", "2025-10-15")
        claimant_1 = p.get("claimant_1", "Sarah Pendelton (Surviving Spouse)")
        claimant_2 = p.get("claimant_2", "David Pendelton (Adult Non-Dependent Child)")
        benefit_amount_cents = p.get("benefit_amount_cents", 48500000)  # $485,000.00
        case_mgr = "Alicia Romero"
        case_mgr_email = "alicia.romero@examplesuper.example"
        claimant_email = "s.pendelton@personal-mail.example"

        specs = []

        # 1. Death Certificate Submission & Certified ID (PDF)
        t_0 = self.build_iso_timestamp(day_offset=0, hour=9, minute=15)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("DOC", "death_cert"),
            filename="certified-death-certificate-extract.pdf",
            media_type="application/pdf",
            created_at_iso=t_0,
            sender_name=claimant_1,
            sender_email=claimant_email,
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Death certificate extract - certified copy",
            payload_type="pdf_doc",
            spec={
                "issuer": "Victorian Registry of Births, Deaths and Marriages",
                "semantic_type": "certificate_copy",
                "fields": {
                    "document_reference": self.get_deterministic_id("BDM", "cert"),
                    "version": "1.0",
                    "evidence_item": "Death Certificate",
                    "document_date": death_date,
                    "member_name": deceased_name,
                    "member_death_date": death_date,
                    "correspondence_contact": claimant_1,
                    "certification_statement": "Certified true copy of the original register extract",
                    "certifier": "Eleanor Vance, JP (Reg 48921)",
                    "certification_date": self.build_iso_timestamp(day_offset=-5)[:10]
                },
                "rows": []
            }
        ))

        # 2. Claimant Relationship Statement (PDF)
        t_1 = self.build_iso_timestamp(day_offset=2, hour=11, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("DOC", "rel_form"),
            filename="claimant-statutory-relationship-form.pdf",
            media_type="application/pdf",
            created_at_iso=t_1,
            sender_name=claimant_1,
            sender_email=claimant_email,
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Claimant relationship form",
            payload_type="pdf_doc",
            spec={
                "issuer": "Example Super Claims Trustee Office",
                "semantic_type": "relationship_form",
                "fields": {
                    "document_reference": self.get_deterministic_id("STAT", "rel"),
                    "version": "1.0",
                    "evidence_item": "Spouse Dependency Statement",
                    "document_date": t_1[:10],
                    "member_name": deceased_name,
                    "member_death_date": death_date,
                    "correspondence_contact": claimant_1,
                    "declared_relationship": "De facto Spouse (continuous cohabitation > 8 years)",
                    "witness_date": t_1[:10]
                },
                "rows": []
            }
        ))

        # 3. Competing Claim Notice from Adult Child (EML)
        t_2 = self.build_iso_timestamp(day_offset=14, hour=14, minute=30)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("EML", "competing_claim"),
            filename="competing-claimant-notice.eml",
            media_type="message/rfc822",
            created_at_iso=t_2,
            sender_name=claimant_2,
            sender_email="david.p@consulting-group.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Notice of Objection to Sole Distribution",
            payload_type="eml",
            spec={
                "subject": f"Notice of Competing Beneficiary Claim - Estate of {deceased_name} [Ref: {ctx.case_reference}]",
                "blocks": [
                    {"p": f"Dear Trustee Claims Committee,"},
                    {"p": f"I am writing formally as the biological son and appointed executor under the Will of {deceased_name}."},
                    {"p": "I note that a claim has been lodged seeking 100% allocation of the superannuation death benefit. I object to this distribution on the basis that financial dependency existed and the non-binding nomination in place designated equal division between surviving dependents."},
                    {"p": "Grant of Probate is currently pending in the Supreme Court of Victoria (Ref: PRO-2025-9921). I request that no funds be disbursed until procedural fairness submissions are considered."}
                ]
            }
        ))

        # 4. Trustee Procedural Fairness Letter (PDF Letter)
        t_3 = self.build_iso_timestamp(day_offset=28, hour=10, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("LTR", "procedural_fairness"),
            filename="trustee-procedural-fairness-notification.pdf",
            media_type="application/pdf",
            created_at_iso=t_3,
            sender_name=case_mgr,
            sender_email=case_mgr_email,
            recipient_name=claimant_1,
            recipient_email=claimant_email,
            title="Trustee Proposed Determination - Procedural Fairness",
            payload_type="pdf_letter",
            spec={
                "title": "Trustee Proposed Determination - Procedural Fairness",
                "letterhead": {"name": "Example Super Trustee Committee", "lines": ["GPO Box 2441", "Melbourne VIC 3001"]},
                "date_line": t_3[:10],
                "addressee": [claimant_1, "c/- Estate Representatives", "Melbourne VIC 3000"],
                "reference_block": [("Case Reference", ctx.case_reference), ("Member Number", ctx.member_reference)],
                "salutation": f"Dear {claimant_1},",
                "blocks": [
                    {"h": "Proposed Distribution of Superannuation Death Benefit"},
                    {"p": f"The Claims Review Committee of Example Super has completed its preliminary assessment regarding the death benefit for the late {deceased_name}."},
                    {"p": f"The total benefit available for distribution is ${(benefit_amount_cents/100):,.2f}."},
                    {"p": "In accordance with section 52(2) of the Superannuation Industry (Supervision) Act 1993 and the Trust Deed, the Trustee proposes to distribute the benefit as follows:"},
                    {"ul": [
                        "70% to Sarah Pendelton (Interdependent Partner / Spouse)",
                        "30% to David Pendelton (Financial Dependent / Adult Son)"
                    ]},
                    {"p": "You have 28 calendar days from the date of this letter to provide written objections or additional evidence. If no objection is received, the determination will become final."}
                ],
                "signoff": ["Yours faithfully,", "Secretary, Claims Review Committee", "Example Super Pty Ltd"],
                "footer": "Example Super Pty Ltd | ABN 12 345 678 901 | AFSL 245100"
            }
        ))

        # 5. Benefit Calculation Ledger (XLSX)
        t_4 = self.build_iso_timestamp(day_offset=28, hour=11, minute=30)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("XLSX", "calculation"),
            filename="death-benefit-distribution-worksheet.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            created_at_iso=t_4,
            sender_name="Actuarial & Benefits Administration",
            sender_email="benefits@examplesuper.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Death Benefit Actuarial Allocation Worksheet",
            payload_type="xlsx",
            spec={
                "columns": ["component", "recipient", "percentage", "gross_amount_cents", "tax_free_cents", "taxable_taxed_cents", "net_payable_cents"],
                "rows": [
                    {"component": "Member Balance", "recipient": "Sarah Pendelton", "percentage": "70.00%", "gross_amount_cents": int(benefit_amount_cents * 0.7), "tax_free_cents": int(benefit_amount_cents * 0.7 * 0.4), "taxable_taxed_cents": int(benefit_amount_cents * 0.7 * 0.6), "net_payable_cents": int(benefit_amount_cents * 0.7)},
                    {"component": "Insured Component", "recipient": "David Pendelton", "percentage": "30.00%", "gross_amount_cents": int(benefit_amount_cents * 0.3), "tax_free_cents": int(benefit_amount_cents * 0.3 * 0.1), "taxable_taxed_cents": int(benefit_amount_cents * 0.3 * 0.9), "net_payable_cents": int(benefit_amount_cents * 0.3 * 0.83)}
                ]
            }
        ))

        return specs

    def generate_native_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        ctx = self.ctx
        t_recv = self.build_iso_timestamp(0)
        t_avail = self.build_iso_timestamp(0, hour=16)
        pty_spouse = self.get_deterministic_id("P", "spouse")
        pty_child = self.get_deterministic_id("P", "child")

        case_row = self.build_case_record(
            case_id=ctx.case_reference,
            party_ids=[pty_spouse, pty_child],
            channel="portal",
            product_or_journey="death_benefit",
            manager_reference="STAFF-CM03",
            original_receipt_date=t_recv[:10],
            complaints_receipt_date=t_recv[:10],
            broad_tags=["death_benefit", "service"],
            recorded_at=t_recv,
            occurred_at=self.build_iso_timestamp(0, hour=9, minute=15),
            available_at=t_avail
        )
        party_rows = [
            self.build_party_record(
                party_id=pty_spouse,
                party_role="beneficiary",
                member_reference=ctx.member_reference,
                recorded_at=t_recv,
                available_at=t_avail
            ),
            self.build_party_record(
                party_id=pty_child,
                party_role="beneficiary",
                member_reference=None,
                recorded_at=t_recv,
                available_at=t_avail
            )
        ]
        return {
            "cases": [case_row],
            "parties": party_rows
        }
