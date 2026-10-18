"""Comprehensive profiling benchmark for sdgen engine: throughput, latency, memory, scaling, and cost projections."""
import csv
import glob
import hashlib
import io
import json
import os
import sys
import time
import tracemalloc
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Add sdgen package root to path
HERE = Path(__file__).resolve().parent
SD_ROOT = HERE.parent
sys.path.insert(0, str(SD_ROOT))

from sdgen.assets import render_asset
from sdgen.paths import PLAN_DIR
from sdgen.render_call import render_call_summary
from sdgen.render_csv import render_rows_csv
from sdgen.render_image import render_upload_receipt
from sdgen.render_mail import blocks_to_html, blocks_to_text, message_id
from sdgen.render_pdf import render_document, render_letter
from sdgen.render_xlsx import render_rows_xlsx


def benchmark_artifact_kinds(iterations=20):
    print(f"--- 1. BENCHMARKING ARTIFACT KINDS ({iterations} iterations each) ---")
    results = {}

    # Sample data
    created_iso = "2026-01-15T10:00:00+11:00"

    # A. PDF Document
    pdf_asset = {
        "title": "Claimant relationship form",
        "semantic_type": "relationship_form",
        "created_at": created_iso,
        "document_spec": {
            "issuer": "Example Super Trustee",
            "fields": {
                "document_reference": "DOC-REF-001",
                "version": "1.0",
                "evidence_item": "Form-01",
                "document_date": "2026-01-15",
                "member_name": "John Doe",
                "member_death_date": "2025-11-20",
                "correspondence_contact": "Jane Doe",
                "declared_relationship": "Spouse",
                "witness_date": "2026-01-15",
            },
            "rows": []
        }
    }
    t0 = time.perf_counter()
    for _ in range(iterations):
        b = render_document(pdf_asset, "System Administrator")
    dt = (time.perf_counter() - t0) / iterations
    results["PDF_document"] = {"latency_ms": dt * 1000, "bytes": len(b), "throughput_per_sec": 1.0 / dt}
    print(f"  PDF Document:      {dt*1000:6.2f} ms/doc  ({1.0/dt:6.1f} docs/sec, {len(b)} bytes)")

    # B. PDF Letter
    letter_spec = {
        "title": "Determination letter",
        "letterhead": {"name": "Example Super Complaints", "lines": ["GPO Box 9999", "Melbourne VIC 3001"]},
        "date_line": "15 January 2026",
        "addressee": ["Jane Doe", "123 High St", "Melbourne VIC 3000"],
        "reference_block": [("Reference", "COMP-2026-001"), ("Member Number", "MEM-123456")],
        "salutation": "Dear Ms Doe,",
        "blocks": [
            {"h": "Trustee Determination"},
            {"p": "We refer to your complaint regarding the distribution of death benefits."},
            {"p": "The Trustee has made a determination pursuant to superannuation law and fund trust deed rules."},
            {"ul": ["Spouse benefit allocated at 100%", "No dependent children identified"]},
            {"p": "If you are dissatisfied, you may contact the Australian Financial Complaints Authority (AFCA)."}
        ],
        "signoff": ["Yours sincerely,", "Complaints Review Committee", "Example Super"],
        "footer": "Example Super Pty Ltd | ABN 12 345 678 901"
    }
    t0 = time.perf_counter()
    for _ in range(iterations):
        b = render_letter(letter_spec, created_iso, "Complaints Manager")
    dt = (time.perf_counter() - t0) / iterations
    results["PDF_letter"] = {"latency_ms": dt * 1000, "bytes": len(b), "throughput_per_sec": 1.0 / dt}
    print(f"  PDF Letter:        {dt*1000:6.2f} ms/doc  ({1.0/dt:6.1f} docs/sec, {len(b)} bytes)")

    # C. XLSX Spreadsheet
    cols = ["transaction_id", "date", "effective_date", "type", "description", "amount_cents", "unit_price", "balance_cents"]
    rows = [
        {"transaction_id": f"TX-{i:04d}", "date": "2026-01-10", "effective_date": "2026-01-10",
         "type": "ADMIN_FEE", "description": "Monthly Administration Fee", "amount_cents": -550,
         "unit_price": "1.4523", "balance_cents": 12540000 - i*550}
        for i in range(25)
    ]
    t0 = time.perf_counter()
    for _ in range(iterations):
        b = render_rows_xlsx(cols, rows, created_iso, "Financial System")
    dt = (time.perf_counter() - t0) / iterations
    results["XLSX_spreadsheet"] = {"latency_ms": dt * 1000, "bytes": len(b), "throughput_per_sec": 1.0 / dt}
    print(f"  XLSX Spreadsheet:  {dt*1000:6.2f} ms/doc  ({1.0/dt:6.1f} docs/sec, {len(b)} bytes)")

    # D. CSV Export
    t0 = time.perf_counter()
    for _ in range(iterations * 5):
        b = render_rows_csv(cols, rows)
    dt = (time.perf_counter() - t0) / (iterations * 5)
    results["CSV_export"] = {"latency_ms": dt * 1000, "bytes": len(b), "throughput_per_sec": 1.0 / dt}
    print(f"  CSV Export:        {dt*1000:6.3f} ms/doc  ({1.0/dt:6.1f} docs/sec, {len(b)} bytes)")

    # E. Call Summary (TXT)
    call_art = {"event_at": "2026-01-15T09:30:00+11:00", "recorded_at": "2026-01-15T09:55:00+11:00"}
    call_authored = {
        "service_banner": "EXAMPLE SUPER CONTACT CENTRE - INBOUND CALL LOG",
        "participants": ["Jane Doe (Caller)", "Marcus Taylor (Senior Consultant)"],
        "summary": "Caller enquired regarding status of death benefit claim submitted in December. Advised that certified documents are currently under review by the claims committee.",
        "follow_up_items": ["Send formal acknowledgement letter", "Request probate documents if required"]
    }
    class MockPlan:
        case_id = "C-TEST-001"
        request_reference = "REQ-2026-001"
    t0 = time.perf_counter()
    for _ in range(iterations * 5):
        b = render_call_summary("D01", call_art, call_authored, MockPlan())
    dt = (time.perf_counter() - t0) / (iterations * 5)
    results["Call_summary_TXT"] = {"latency_ms": dt * 1000, "bytes": len(b), "throughput_per_sec": 1.0 / dt}
    print(f"  Call Summary TXT:  {dt*1000:6.3f} ms/doc  ({1.0/dt:6.1f} docs/sec, {len(b)} bytes)")

    # F. Upload Receipt (JPEG)
    img_asset = {"created_at": created_iso, "title": "Portal Upload Receipt"}
    img_fields = {"upload_reference": "UPL-987654", "uploaded_at": created_iso, "member_account_number": "MEM-123456"}
    img_rows = [{"file": "death_certificate.pdf", "receipt_state": "received"}]
    t0 = time.perf_counter()
    for _ in range(iterations):
        b, drawn = render_upload_receipt(img_asset, img_rows, img_fields)
    dt = (time.perf_counter() - t0) / iterations
    results["JPEG_receipt"] = {"latency_ms": dt * 1000, "bytes": len(b), "throughput_per_sec": 1.0 / dt}
    print(f"  JPEG Receipt:      {dt*1000:6.2f} ms/doc  ({1.0/dt:6.1f} docs/sec, {len(b)} bytes)")

    # G. SHA-256 Hashing Overhead
    sample_payload = b * 10  # ~500KB
    t0 = time.perf_counter()
    for _ in range(iterations * 50):
        h = hashlib.sha256(sample_payload).hexdigest()
    dt = (time.perf_counter() - t0) / (iterations * 50)
    results["SHA256_500KB"] = {"latency_us": dt * 1_000_000, "throughput_MB_sec": (len(sample_payload) / 1024 / 1024) / dt}
    print(f"  SHA-256 (500KB):   {dt*1_000_000:6.2f} µs/hash ({results['SHA256_500KB']['throughput_MB_sec']:6.1f} MB/s)")

    return results


def benchmark_held_assets_across_catalog():
    print("\n--- 2. BENCHMARKING ACTUAL HELD ASSETS ACROSS 40 PLANS ---")
    pack_files = sorted(glob.glob(str(PLAN_DIR / "assistant-packs/*.json")))
    all_held = []
    for pf in pack_files:
        d = json.load(open(pf))
        names = {a["id"]: a["name"] for a in d["actors"]}
        for a in d["assets"]:
            if a.get("delivery_state") == "held" and a["media_type"] != "message/rfc822":
                all_held.append((d["case_number"], a, names.get(a.get("owner", ""), "Trustee Staff")))

    print(f"Found {len(all_held)} held non-email assets across {len(pack_files)} plans.")
    
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0]
    t0 = time.perf_counter()
    rendered_bytes = 0
    type_counts = {}
    type_times = {}

    for case_num, asset, owner in all_held:
        mt = asset["media_type"]
        type_counts[mt] = type_counts.get(mt, 0) + 1
        t_asset0 = time.perf_counter()
        data, _ = render_asset(asset, owner)
        t_asset = time.perf_counter() - t_asset0
        type_times[mt] = type_times.get(mt, 0.0) + t_asset
        rendered_bytes += len(data)

    total_time = time.perf_counter() - t0
    mem_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    print(f"Rendered {len(all_held)} assets in {total_time:.3f} s ({len(all_held)/total_time:.1f} assets/sec)")
    print(f"Total output size: {rendered_bytes / 1024 / 1024:.2f} MB")
    print(f"Peak memory allocated during rendering: {mem_peak / 1024 / 1024:.2f} MB")
    print("Breakdown by media type:")
    for mt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        dur = type_times[mt]
        print(f"  {mt:55s}: {cnt:3d} items, {dur*1000/cnt:6.2f} ms/item, total {dur:5.2f} s")

    return {
        "asset_count": len(all_held),
        "total_time_s": total_time,
        "assets_per_second": len(all_held) / total_time,
        "total_mb": rendered_bytes / 1024 / 1024,
        "peak_mem_mb": mem_peak / 1024 / 1024,
        "breakdown": {mt: {"count": cnt, "avg_ms": type_times[mt]*1000/cnt, "total_s": type_times[mt]} for mt, cnt in type_counts.items()}
    }


def _render_worker(items):
    cnt = 0
    total_len = 0
    for asset, owner in items:
        data, _ = render_asset(asset, owner)
        cnt += 1
        total_len += len(data)
    return cnt, total_len


def benchmark_concurrency():
    print("\n--- 3. MULTIPROCESSING / CONCURRENCY SCALING ---")
    pack_files = sorted(glob.glob(str(PLAN_DIR / "assistant-packs/*.json")))
    all_held = []
    for pf in pack_files:
        d = json.load(open(pf))
        names = {a["id"]: a["name"] for a in d["actors"]}
        for a in d["assets"]:
            if a.get("delivery_state") == "held" and a["media_type"] != "message/rfc822":
                all_held.append((a, names.get(a.get("owner", ""), "Trustee Staff")))

    concurrency_results = {}
    for num_procs in [1, 2, 4, 8]:
        chunk_size = (len(all_held) + num_procs - 1) // num_procs
        chunks = [all_held[i*chunk_size:(i+1)*chunk_size] for i in range(num_procs) if i*chunk_size < len(all_held)]
        
        t0 = time.perf_counter()
        if num_procs == 1:
            cnt, total_len = _render_worker(all_held)
        else:
            with ProcessPoolExecutor(max_workers=num_procs) as pool:
                res = list(pool.map(_render_worker, chunks))
                cnt = sum(r[0] for r in res)
        dur = time.perf_counter() - t0
        speedup = concurrency_results.get(1, {}).get("total_time_s", dur) / dur if num_procs > 1 else 1.0
        concurrency_results[num_procs] = {
            "total_time_s": dur,
            "throughput": len(all_held) / dur,
            "speedup": speedup
        }
        print(f"  Workers={num_procs:2d}: {dur:5.3f} s ({len(all_held)/dur:6.1f} assets/s), Speedup: {speedup:4.2f}x")

    return concurrency_results


def calculate_phase2_projections(kind_results, catalog_results, concurrency_results):
    print("\n--- 4. PHASE 2 (5,000 CASES) CAPACITY & COST PROJECTIONS ---")
    # Catalog parameters:
    # 5,000 members, ~5,800 episodes, ~6,500 internal cases, ~650 AFCA disputes
    # Average case has ~20-30 artifacts (avg 25 artifacts: 12 emails/notes, 8 PDFs, 3 spreadsheets/CSVs, 2 call summaries/receipts)
    cases = 5000
    avg_artifacts_per_case = 25
    total_artifacts = cases * avg_artifacts_per_case # 125,000 artifacts
    
    # Composition:
    # - PDFs: ~32% = 40,000
    # - XLSX: ~8%  = 10,000
    # - CSV:  ~8%  = 10,000
    # - EML:  ~40% = 50,000
    # - TXT:  ~8%  = 10,000
    # - JPEG: ~4%  = 5,000

    pdf_time_s = 40000 * (kind_results["PDF_document"]["latency_ms"] / 1000)
    xlsx_time_s = 10000 * (kind_results["XLSX_spreadsheet"]["latency_ms"] / 1000)
    csv_time_s = 10000 * (kind_results["CSV_export"]["latency_ms"] / 1000)
    eml_time_s = 50000 * 0.002 # ~2ms per email MIME assembly
    txt_time_s = 10000 * (kind_results["Call_summary_TXT"]["latency_ms"] / 1000)
    jpeg_time_s = 5000 * (kind_results["JPEG_receipt"]["latency_ms"] / 1000)

    total_render_cpu_seconds = pdf_time_s + xlsx_time_s + csv_time_s + eml_time_s + txt_time_s + jpeg_time_s
    hashing_io_overhead_s = total_artifacts * 0.001 # ~1ms per file disk write + sha256
    total_single_core_time_s = total_render_cpu_seconds + hashing_io_overhead_s

    # Multi-core projections (using 8-worker scaling factor)
    speedup_8 = concurrency_results.get(8, {}).get("speedup", 4.5)
    wall_clock_8core_s = total_single_core_time_s / speedup_8

    print(f"Total Target Artifacts: {total_artifacts:,} across {cases:,} cases")
    print(f"Single-Core Render Time: {total_single_core_time_s/60:.1f} minutes ({total_single_core_time_s/3600:.2f} hours)")
    print(f"8-Core Parallel Wall-Clock Time: {wall_clock_8core_s/60:.1f} minutes ({wall_clock_8core_s/3600:.2f} hours)")

    # Storage volume projection:
    avg_artifact_bytes = 45 * 1024 # ~45 KB average per artifact
    total_data_gb = (total_artifacts * avg_artifact_bytes) / (1024**3)
    print(f"Estimated Raw Storage Volume: {total_data_gb:.2f} GB")

    # Telemetry and Cost projections:
    # Deterministic generation: $0 API cost
    # Hybrid LLM approach (generating 500 archetype seed stories + deterministic combinatorial engine):
    # - 500 archetype seeds @ 4,000 input tokens + 2,000 output tokens = 2.0M input, 1.0M output tokens
    # Model costs:
    # 1. Claude 3.5 Sonnet: $3.00/M in, $15.00/M out -> $6.00 + $15.00 = $21.00
    # 2. Gemini 1.5 Flash: $0.075/M in, $0.30/M out -> $0.15 + $0.30 = $0.45
    # 3. Gemini 1.5 Pro: $1.25/M in, $5.00/M out -> $2.50 + $5.00 = $7.50
    # 4. Local Gemma (27B/9B): $0.00 cloud API cost
    print("\nCost Projections (Hybrid Architecture: 500 Archetype Seed Families):")
    print("  Deterministic Engine (Pure Rules):           $0.00")
    print("  Hybrid with Gemini 1.5 Flash:                ~$0.45 total")
    print("  Hybrid with Gemini 1.5 Pro:                  ~$7.50 total")
    print("  Hybrid with Claude 3.5 Sonnet:               ~$21.00 total")
    print("  Hybrid with Local Gemma (Ollama/vLLM):       $0.00 cloud cost")

    return {
        "total_cases": cases,
        "total_artifacts": total_artifacts,
        "single_core_seconds": total_single_core_time_s,
        "eight_core_wall_clock_seconds": wall_clock_8core_s,
        "estimated_storage_gb": total_data_gb,
        "costs": {
            "pure_deterministic": 0.0,
            "hybrid_gemini_flash": 0.45,
            "hybrid_gemini_pro": 7.50,
            "hybrid_claude_sonnet": 21.00,
            "local_gemma": 0.0
        }
    }


def main():
    print("=================================================================")
    print("        SDGEN SYNTHETIC GENERATION ENGINE PROFILING TOOL         ")
    print("=================================================================\n")
    k_res = benchmark_artifact_kinds()
    cat_res = benchmark_held_assets_across_catalog()
    conc_res = benchmark_concurrency()
    proj_res = calculate_phase2_projections(k_res, cat_res, conc_res)

    report = {
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "system": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "cpu_cores": os.cpu_count()
        },
        "artifact_benchmarks": k_res,
        "held_assets_benchmarks": cat_res,
        "concurrency_benchmarks": conc_res,
        "phase2_projections": proj_res
    }

    out_file = SD_ROOT / "evidence/sdgen-profiling-benchmark-2026-10-16.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=2))
    print(f"\n[OK] Profiling report saved to: {out_file}")


if __name__ == "__main__":
    main()
