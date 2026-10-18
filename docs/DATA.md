# Synthetic evidence tooling

The demonstration uses fictional members and fund records. The bulk 500-member corpus and private expected answers remain outside this source repository. Generated artifacts, SQLite stores, evaluation labels and rejected batches must not be committed.

Generic authored-input tooling is in `Implementation/synthetic-data/sdgen/authored/`. Its author guide and five-member worked example are in `Implementation/synthetic-data/authoring-input/`. Older case-specific tools remain for compatibility, but are not the recommended authoring route.

```bash
cd Implementation/synthetic-data
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m sdgen authored-check authoring-input/examples/fx-five-members
.venv/bin/python -m sdgen authored-build authoring-input/examples/fx-five-members --out-root /absolute/path/to/new-candidates
.venv/bin/python -m sdgen authored-verify /absolute/path/to/new-candidates/CANDIDATE
```

The builder does not invent narrative content. It validates and renders authored inputs, writes a new candidate and separates runtime evidence from private review material. Mechanical checks are not proof of prose realism or diagnostic truth. Expected figures and labels must not enter the model's runtime inputs.

Checks cover chronology, copy fidelity, payment/reversal coherence, lifecycle coverage and historical availability. Source records retain integer cents and seconds, event dates and availability dates. Do not replace missing work with salary estimates or assume missing money means no business burden.
