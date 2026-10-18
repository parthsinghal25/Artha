"""Archetype 3: Defined Benefit and Pension Calculation Disputes.
Covers Final Average Salary (FAS) calculation discrepancies, preservation age transitions,
pension indexation timing lags (CPI vs AWOTE), and commutation formula errors.
"""
from typing import Any, Dict, List

from .base import ArchetypeCaseContext, BaseComplaintArchetype, GeneratedArtifactSpec


class PensionDisputeArchetype(BaseComplaintArchetype):
    archetype_id = "defined_benefit_pension_dispute"
    archetype_title = "Defined Benefit & Pension Calculation Dispute"

    def generate_artifact_specs(self) -> List[GeneratedArtifactSpec]:
        ctx = self.ctx
        p = ctx.parameters
        disputed_pension_cents = p.get("disputed_pension_cents", 420000)  # $4,200/month
        expected_pension_cents = p.get("expected_pension_cents", 465000)  # $4,650/month
        fas_disputed_cents = p.get("fas_disputed_cents", 11200000)        # $112,000 FAS
        fas_expected_cents = p.get("fas_expected_cents", 12400000)        # $124,000 FAS
        accrual_years = p.get("accrual_years", 28.5)
        case_mgr = "Julian Prescott"
        case_mgr_email = "julian.prescott@examplesuper.example"
        member_email = "member.pension@client-domain.example"

        specs = []

        # 1. Pension Benefit Calculation Statement (XLSX Worksheet)
        t_0 = self.build_iso_timestamp(day_offset=0, hour=10, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("XLSX", "pension_statement"),
            filename="defined-benefit-retirement-calculation.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            created_at_iso=t_0,
            sender_name="Defined Benefit Actuarial Administration",
            sender_email="db-actuarial@examplesuper.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Defined Benefit Pension Determination Worksheet",
            payload_type="xlsx",
            spec={
                "columns": ["metric_name", "statutory_basis", "system_value", "unit", "notes"],
                "rows": [
                    {"metric_name": "Final Average Salary (FAS)", "statutory_basis": "Best 3 consecutive years prior to exit", "system_value": f"${(fas_disputed_cents/100):,.2f}", "unit": "AUD", "notes": "Excluded higher duties allowance in FY24"},
                    {"metric_name": "Accrual Service Period", "statutory_basis": "Completed years of contributory membership", "system_value": str(accrual_years), "unit": "Years", "notes": "Commenced 1997-04-12"},
                    {"metric_name": "Benefit Multiplier", "statutory_basis": "Division B Standard Rules (1.5% p.a.)", "system_value": "42.75%", "unit": "Percentage", "notes": "0.015 * 28.5 years"},
                    {"metric_name": "Annual Pension Payable", "statutory_basis": "FAS * Total Multiplier", "system_value": f"${(disputed_pension_cents * 12 / 100):,.2f}", "unit": "AUD/year", "notes": "Gross monthly payment: $" + f"{(disputed_pension_cents/100):,.2f}"},
                    {"metric_name": "CPI Indexation Rate", "statutory_basis": "Weighted 8 Capital Cities June CPI", "system_value": "3.80%", "unit": "Percentage", "notes": "Next indexation review 1 July 2026"}
                ]
            }
        ))

        # 2. Member Grievance Letter regarding Allowance Exclusion (PDF Letter)
        t_1 = self.build_iso_timestamp(day_offset=15, hour=11, minute=30)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("LTR", "member_grievance"),
            filename="member-formal-pension-dispute-notice.pdf",
            media_type="application/pdf",
            created_at_iso=t_1,
            sender_name=ctx.member_name,
            sender_email=member_email,
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Formal Notice of Dispute - Final Average Salary Miscalculation",
            payload_type="pdf_letter",
            spec={
                "title": "Dispute of Defined Benefit Pension Calculation",
                "letterhead": {"name": ctx.member_name, "lines": ["45 Glen Iris Road", "Glen Iris VIC 3146"]},
                "date_line": t_1[:10],
                "addressee": ["Trustee Complaints Resolution Officer", "Example Super Pty Ltd", "GPO Box 2441, Melbourne VIC 3001"],
                "reference_block": [("Member Ref", ctx.member_reference), ("Pension ID", "PEN-DB-48210"), ("Case ID", ctx.case_reference)],
                "salutation": "Dear Complaints Committee,",
                "blocks": [
                    {"h": "Incorrect Exclusion of Substantive Allowances from FAS"},
                    {"p": f"I am writing to formally dispute the retirement pension entitlement issued on {t_0[:10]} calculating my monthly pension as ${(disputed_pension_cents/100):,.2f} based on a Final Average Salary of ${(fas_disputed_cents/100):,.2f}."},
                    {"p": "Your administration team has wrongly excluded the ongoing Senior Operational Allowance paid continuously over 36 months between July 2022 and June 2025. Under Section 14.3 of the Example Super Division B Trust Deed, 'Salary' encompasses all recognized continuous superannuable allowances upon which employer SGC contributions were remitted."},
                    {"p": f"Including this recognized allowance results in a true FAS of ${(fas_expected_cents/100):,.2f}, yielding a correct monthly pension entitlement of ${(expected_pension_cents/100):,.2f}."},
                    {"p": "This monthly shortfall of $" + f"{((expected_pension_cents - disputed_pension_cents)/100):,.2f}" + " is causing significant detriment to my retirement budget. I request an immediate manual actuarial recalculation and adjustment of my pension arrears."}
                ],
                "signoff": ["Yours faithfully,", ctx.member_name],
                "footer": f"Member Reference: {ctx.member_reference} | Case: {ctx.case_reference}"
            }
        ))

        # 3. Call Intake Log regarding Indexation Timing (Call Summary TXT)
        t_2 = self.build_iso_timestamp(day_offset=25, hour=14, minute=45)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("TXT", "call_log"),
            filename="call-centre-pension-indexation-log.txt",
            media_type="text/plain",
            created_at_iso=t_2,
            sender_name="Example Super Pension Support Team",
            sender_email="pension-support@examplesuper.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Inbound Pension Escalation Call Summary",
            payload_type="call_txt",
            spec={
                "service_banner": "EXAMPLE SUPER PENSION DIVISION - INBOUND TELEPHONY LOG",
                "participants": [f"{ctx.member_name} (Retired Member)", "Brenda Collins (Senior Pension Specialist)"],
                "summary": f"Member called regarding the pending FAS dispute and queried why the July 2026 CPI indexation adjustment was not applied to the current interim monthly benefit. Advised member that interim payments are held static pending resolution of the formal Trust Deed interpretation by the Trustee Legal Team.",
                "follow_up_items": [
                    "Forward employer payroll certification records to Actuarial Review",
                    "Confirm whether member was subject to continuous allowance definition under Division B"
                ]
            }
        ))

        return specs

    def generate_native_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        ctx = self.ctx
        t_recv = self.build_iso_timestamp(0)
        t_avail = self.build_iso_timestamp(0, hour=16)
        pty_pensioner = self.get_deterministic_id("P", "pensioner")

        case_row = self.build_case_record(
            case_id=ctx.case_reference,
            party_ids=[pty_pensioner],
            channel="letter",
            product_or_journey="defined_benefit",
            manager_reference="STAFF-CM01",
            original_receipt_date=t_recv[:10],
            complaints_receipt_date=t_recv[:10],
            broad_tags=["pension", "calculation"],
            recorded_at=t_recv,
            occurred_at=self.build_iso_timestamp(0, hour=10, minute=0),
            available_at=t_avail
        )
        party_rows = [
            self.build_party_record(
                party_id=pty_pensioner,
                party_role="member",
                member_reference=ctx.member_reference,
                recorded_at=t_recv,
                available_at=t_avail
            )
        ]
        return {
            "cases": [case_row],
            "parties": party_rows
        }
