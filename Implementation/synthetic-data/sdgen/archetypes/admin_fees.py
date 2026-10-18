"""Archetype 4: Administration Fees, Rollover Delays, and Allocation Disputes.
Covers SGC employer contribution allocation lags, lost account consolidation sweeps,
advice fee disclosure breaches (lack of valid ongoing fee consent), and unit pricing timing disputes.
"""
from typing import Any, Dict, List

from .base import ArchetypeCaseContext, BaseComplaintArchetype, GeneratedArtifactSpec


class AdminFeesAndRolloversArchetype(BaseComplaintArchetype):
    archetype_id = "admin_fees_and_rollovers"
    archetype_title = "Administration Fees, Rollover & SGC Allocation Dispute"

    def generate_artifact_specs(self) -> List[GeneratedArtifactSpec]:
        ctx = self.ctx
        p = ctx.parameters
        rollover_amount_cents = p.get("rollover_amount_cents", 18450000)  # $184,500.00
        disputed_fee_cents = p.get("disputed_fee_cents", 440000)          # $4,400.00 annual ongoing advice fee
        sgc_delayed_cents = p.get("sgc_delayed_cents", 1250000)           # $12,500.00 employer SGC
        market_loss_cents = p.get("market_loss_cents", 820000)            # $8,200.00 unit pricing differential
        case_mgr = "Toby Vance"
        case_mgr_email = "toby.vance@examplesuper.example"
        member_email = "member.account@fastmail.example"

        specs = []

        # 1. Transaction Fee Ledger (CSV Export)
        t_0 = self.build_iso_timestamp(day_offset=0, hour=9, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("CSV", "fee_ledger"),
            filename="account-transaction-and-fee-history.csv",
            media_type="text/csv",
            created_at_iso=t_0,
            sender_name="Core Super Registry System",
            sender_email="registry-exports@examplesuper.example",
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Member Historical Fee Deductions & Transaction Log",
            payload_type="csv",
            spec={
                "columns": ["transaction_id", "effective_date", "processing_date", "type", "description", "amount_cents", "unit_price", "closing_balance_cents"],
                "rows": [
                    {"transaction_id": "TXN-88410", "effective_date": "2025-10-31", "processing_date": "2025-11-03", "type": "ADVICE_FEE", "description": "Ongoing Adviser Service Fee (Quarterly)", "amount_cents": -110000, "unit_price": "1.8421", "closing_balance_cents": 18450000},
                    {"transaction_id": "TXN-88722", "effective_date": "2025-11-30", "processing_date": "2025-12-02", "type": "ADMIN_FEE", "description": "Direct Member Administration Fee", "amount_cents": -7500, "unit_price": "1.8540", "closing_balance_cents": 18442500},
                    {"transaction_id": "TXN-89104", "effective_date": "2026-01-15", "processing_date": "2026-01-18", "type": "ADVICE_FEE", "description": "Ongoing Adviser Service Fee (Quarterly)", "amount_cents": -110000, "unit_price": "1.8105", "closing_balance_cents": 18332500},
                    {"transaction_id": "TXN-89550", "effective_date": "2026-01-31", "processing_date": "2026-02-12", "type": "SGC_CONTRIB", "description": "Delayed Employer SGC Q2 Contribution", "amount_cents": 1250000, "unit_price": "1.8350", "closing_balance_cents": 19582500}
                ]
            }
        ))

        # 2. Member Complaint Email regarding Fee Breach and Rollover Delay (EML)
        t_1 = self.build_iso_timestamp(day_offset=12, hour=14, minute=20)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("EML", "complaint_email"),
            filename="member-complaint-unauthorized-advice-fees.eml",
            media_type="message/rfc822",
            created_at_iso=t_1,
            sender_name=ctx.member_name,
            sender_email=member_email,
            recipient_name=case_mgr,
            recipient_email=case_mgr_email,
            title="Formal Complaint - Unauthorized Advice Fees & Rollover Inaction",
            payload_type="eml",
            spec={
                "subject": f"COMPLAINT: Unauthorized Fee Deductions and Unreasonable Rollover Delay [Account: {ctx.member_reference}]",
                "blocks": [
                    {"p": f"Dear Complaints Resolution Team,"},
                    {"p": f"I am writing to register an urgent formal complaint regarding unauthorized financial advice fee deductions from my superannuation account {ctx.member_reference}."},
                    {"p": "I terminated my relationship with my former financial adviser over 14 months ago and never executed an ongoing fee consent renewal form under the Corporations Act. Despite this, Example Super continued deducting $1,100 per quarter from my balance without valid consent."},
                    {"p": f"Furthermore, I requested a full account rollover to my industry fund on 2026-01-08 (SuperStream reference SS-2026-0108-99). The processing was delayed by over 5 weeks due to an alleged 'manual validation hold', during which the market dropped and I suffered an estimated unit price timing detriment of ${(market_loss_cents/100):,.2f}."},
                    {"p": "I demand a full refund of all unauthorized advice fees deducted since expiry of consent, plus statutory interest and compensation for the rollover timing loss."}
                ]
            }
        ))

        # 3. SuperStream Electronic Portability Rollover Statement (PDF Document)
        t_2 = self.build_iso_timestamp(day_offset=25, hour=16, minute=0)
        specs.append(GeneratedArtifactSpec(
            artifact_id=self.get_deterministic_id("DOC", "superstream_statement"),
            filename="superstream-rollover-benefit-statement.pdf",
            media_type="application/pdf",
            created_at_iso=t_2,
            sender_name="Example Super Electronic Portability Gateway",
            sender_email="superstream-gateway@examplesuper.example",
            recipient_name="Australian Superannuation Clearing House",
            recipient_email="clearinghouse@ato.gov.example",
            title="SuperStream Rollover Benefit Statement",
            payload_type="pdf_doc",
            spec={
                "issuer": "Example Super Electronic Rollover Gateway",
                "semantic_type": "requirement_assessment",
                "fields": {
                    "document_reference": self.get_deterministic_id("SSR", "rollover"),
                    "version": "1.0",
                    "evidence_item": "SuperStream Electronic Portability Transfer",
                    "document_date": t_2[:10],
                    "member_name": ctx.member_name,
                    "member_account_number": ctx.member_reference,
                    "request_reference": ctx.request_reference,
                    "requirement": "Section 243 Superannuation Industry (Supervision) Regulations",
                    "decision": "Rollover Executed following Resolution of Verification Exception",
                    "assessment_date": t_2[:10],
                    "original_validity_decision": "Completed - Funds Disbursed via SuperStream EBMS3 Protocol"
                },
                "rows": [
                    {"field": "Transferring Fund USI", "value": "EX-SUPER-001"},
                    {"field": "Receiving Fund USI", "value": "IND-SUPER-774"},
                    {"field": "Tax-Free Component", "value": "$42,500.00"},
                    {"field": "Taxable-Taxed Component", "value": "$142,000.00"},
                    {"field": "Net Rollover Amount", "value": f"${(rollover_amount_cents/100):,.2f}"}
                ]
            }
        ))

        return specs

    def generate_native_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        ctx = self.ctx
        t_recv = self.build_iso_timestamp(0)
        t_avail = self.build_iso_timestamp(0, hour=16)
        pty_member = self.get_deterministic_id("P", "member")

        case_row = self.build_case_record(
            case_id=ctx.case_reference,
            party_ids=[pty_member],
            channel="email",
            product_or_journey="super_administration",
            manager_reference="STAFF-CM04",
            original_receipt_date=t_recv[:10],
            complaints_receipt_date=t_recv[:10],
            broad_tags=["fees", "rollover"],
            recorded_at=t_recv,
            occurred_at=self.build_iso_timestamp(0, hour=9, minute=0),
            available_at=t_avail
        )
        party_rows = [
            self.build_party_record(
                party_id=pty_member,
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
