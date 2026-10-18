"""Archetype 2: Total & Permanent Disability (TPD) Insurance Disputes.
Covers insurer vs trustee claim disputes, IME medical appointment delays,
conflicting specialist medical opinions, and procedural fairness rounds.
"""
from typing import Any, Dict, List

from .base import ArchetypeCaseContext, BaseComplaintArchetype, GeneratedArtifactSpec


class TPDInsuranceArchetype(BaseComplaintArchetype):
    archetype_id = "tpd_insurance_dispute"
    archetype_title = "Total & Permanent Disability (TPD) Dispute"

    def generate_artifact_specs(self) -> List[GeneratedArtifactSpec]:
        ctx = self.ctx
        p = ctx.parameters
        insured_benefit_cents = p.get("insured_benefit_cents", 32000000)  # $320,000
        injury_condition = p.get("injury_condition", "Lumbar spine arthrodesis and bilateral radiculopathy")
        treating_doctor = p.get("treating_doctor", "Dr. Alistair Finch (Consultant Orthopaedic Surgeon)")
        ime_doctor = p.get("ime_doctor", "Dr. Evelyn Vance (Occupational Medicine Specialist)")
        insurer_name = "Pacific Life Assurance Co"
        case_mgr = "Marcus Thorne"
        case_mgr_email = "marcus.thorne@examplesuper.example"
        member_email = "member.case@secure-mail.example"

        specs = []

        # 1. Treating Specialist Attending Medical Report (PDF Document)
        t_0 = self.build_iso_timestamp(day_offset=0, hour=10, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("DOC", "treating_report"),
            filename="treating-specialist-medical-assessment.pdf",
            media_type="application/pdf",
            created_at_iso=t_0,
            sender_name=treating_doctor,
            sender_email="admin@finch-ortho.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Attending Specialist Medical Report - TPD Assessment",
            payload_type="pdf_doc",
            spec={
                "issuer": "Melbourne Orthopaedic & Spine Institute",
                "semantic_type": "requirement_assessment",
                "fields": {
                    "document_reference": self.get_deterministic_id("MED", "specialist"),
                    "version": "1.0",
                    "evidence_item": "Treating Medical Assessment",
                    "document_date": t_0[:10],
                    "member_name": ctx.member_name,
                    "member_account_number": ctx.member_reference,
                    "request_reference": ctx.request_reference,
                    "requirement": "Permanent Incapacity Medical Assessment (SIS Reg 1.03C)",
                    "decision": f"Unlikely ever to engage in gainful employment for which reasonably qualified by education, training or experience due to {injury_condition}",
                    "assessment_date": t_0[:10],
                    "original_validity_decision": "Supported - Total & Permanent Disability Criteria Satisfied"
                },
                "rows": [
                    {"parameter": "Maximum Medical Improvement (MMI)", "status": "Reached - Static deficit"},
                    {"parameter": "Sedentary Capacity", "status": "Restricted < 2 hours continuous sitting"},
                    {"parameter": "Prognosis", "status": "Guarded, permanent loss of spinal mobility"}
                ]
            }
        ))

        # 2. Independent Medical Examination (IME) Report (PDF Document)
        t_1 = self.build_iso_timestamp(day_offset=45, hour=14, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("DOC", "ime_report"),
            filename="insurer-appointed-ime-assessment.pdf",
            media_type="application/pdf",
            created_at_iso=t_1,
            sender_name=ime_doctor,
            sender_email="assessments@medico-legal-network.example",
            recipient_name="Group Insurance Claims, Pacific Life",
            recipient_email="claims@pacific-life.example",
            title="Independent Medical Examination Report",
            payload_type="pdf_doc",
            spec={
                "issuer": "Independent Medico-Legal Valuations",
                "semantic_type": "requirement_assessment",
                "fields": {
                    "document_reference": self.get_deterministic_id("IME", "assessment"),
                    "version": "1.0",
                    "evidence_item": "Independent Medical Examination",
                    "document_date": t_1[:10],
                    "member_name": ctx.member_name,
                    "member_account_number": ctx.member_reference,
                    "request_reference": ctx.request_reference,
                    "requirement": "Group Life Policy TPD Clause 4.2 ('Any Occupation')",
                    "decision": "Current residual capacity identified for modified sedentary/clerical duties following 6-8 weeks vocational retraining.",
                    "assessment_date": t_1[:10],
                    "original_validity_decision": "Partially Supported - Transferable skills allow light retail/administrative employment"
                },
                "rows": [
                    {"assessment_domain": "Physical Effort", "finding": "Light physical capacity preserved"},
                    {"assessment_domain": "Cognitive Function", "finding": "Intact, suitable for office duties"}
                ]
            }
        ))

        # 3. Insurer Declination / Procedural Fairness Notice (PDF Letter)
        t_2 = self.build_iso_timestamp(day_offset=60, hour=11, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("LTR", "insurer_decline"),
            filename="group-insurer-procedural-fairness-declination.pdf",
            media_type="application/pdf",
            created_at_iso=t_2,
            sender_name="Senior Claims Assessor, Pacific Life",
            sender_email="claims.assessor@pacific-life.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Insurer Recommendation - Proposed Declination",
            payload_type="pdf_letter",
            spec={
                "title": "Group Life Policy - Preliminary Assessment",
                "letterhead": {"name": f"{insurer_name} Group Life Division", "lines": ["452 Collins Street", "Melbourne VIC 3000"]},
                "date_line": t_2[:10],
                "addressee": ["Trustee Claims Review Committee", "Example Super Pty Ltd", "GPO Box 2441, Melbourne VIC 3001"],
                "reference_block": [("Policy Number", "GL-99420-EX"), ("Member Ref", ctx.member_reference), ("Claim ID", ctx.case_reference)],
                "salutation": "Dear Trustee Members,",
                "blocks": [
                    {"h": "Proposed Declination of Total and Permanent Disablement Claim"},
                    {"p": f"Pacific Life has reviewed the claim for member {ctx.member_name} under Policy GL-99420-EX for insured benefit ${(insured_benefit_cents/100):,.2f}."},
                    {"p": f"Having considered the medical file, including the report of {treating_doctor} and the independent examination by {ime_doctor}, the Insurer forms the view that the member does not meet the policy definition of TPD."},
                    {"p": "Specifically, the member retains transferable administrative competencies and computer literacy allowing gainful sedentary employment within reasonable commute."},
                    {"p": "We invite the Trustee to review our draft finding and provide any further medical submissions within 30 days."}
                ],
                "signoff": ["Yours sincerely,", "Chief Medical Underwriter", insurer_name],
                "footer": f"{insurer_name} Australian Branch | ABN 88 000 111 222"
            }
        ))

        # 4. Member Dispute Escalation Email (EML)
        t_3 = self.build_iso_timestamp(day_offset=70, hour=16, minute=15)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("EML", "member_dispute"),
            filename="member-tpd-complaint-escalation.eml",
            media_type="message/rfc822",
            created_at_iso=t_3,
            sender_name=ctx.member_name,
            sender_email=member_email,
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Formal Complaint - Unreasonable Delay and Flawed IME",
            payload_type="eml",
            spec={
                "subject": f"URGENT COMPLAINT: Unfair TPD Delay & Rejecting Treating Specialist Evidence [Case: {ctx.case_reference}]",
                "blocks": [
                    {"p": f"Dear {case_mgr},"},
                    {"p": f"I am writing to lodge an official complaint against Example Super and the insurer. It has now been over 8 months since I submitted my TPD claim with comprehensive documentation from my treating surgeon {treating_doctor}."},
                    {"p": f"The insurer forced me to travel over two hours while in severe pain to see their doctor ({ime_doctor}), who only examined me for 15 minutes before claiming I could sit at an office desk full time. This completely contradicts my spinal MRI and specialist surgeon's prognosis."},
                    {"p": "I cannot sit for more than 20 minutes without debilitating spasms. I am in financial distress and facing mortgage default. If the Trustee accepts the insurer's unfair recommendation, I will immediately escalate this matter to AFCA."}
                ]
            }
        ))

        return specs

    def generate_native_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        ctx = self.ctx
        t_recv = self.build_iso_timestamp(0)
        t_avail = self.build_iso_timestamp(0, hour=16)
        pty_member = self.get_deterministic_id("P", "member")
        pty_insurer = self.get_deterministic_id("P", "insurer")

        case_row = self.build_case_record(
            case_id=ctx.case_reference,
            party_ids=[pty_member, pty_insurer],
            channel="email",
            product_or_journey="tpd_insurance",
            manager_reference="STAFF-CM02",
            original_receipt_date=t_recv[:10],
            complaints_receipt_date=t_recv[:10],
            broad_tags=["insurance", "delay"],
            recorded_at=t_recv,
            occurred_at=self.build_iso_timestamp(0, hour=10, minute=0),
            available_at=t_avail
        )
        party_rows = [
            self.build_party_record(
                party_id=pty_member,
                party_role="member",
                member_reference=ctx.member_reference,
                recorded_at=t_recv,
                available_at=t_avail
            ),
            self.build_party_record(
                party_id=pty_insurer,
                party_role="other",
                member_reference=None,
                recorded_at=t_recv,
                available_at=t_avail
            )
        ]
        return {
            "cases": [case_row],
            "parties": party_rows
        }
