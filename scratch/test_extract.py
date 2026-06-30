from engine.pipeline import CandidateTransformerEngine

engine = CandidateTransformerEngine()
inputs = [
    "sample_inputs/ats_profile.json",
    "sample_inputs/linkedin_profile.json",
    "sample_inputs/resume_john_doe.txt",
    "sample_inputs/recruiter_candidates.csv",
    "sample_inputs/recruiter_notes.txt"
]

print("Running pipeline...")
# Let's extract raw fields manually and print them
all_raw_fields = []
for path in inputs:
    for plugin in engine.plugins:
        if plugin.detect(path):
            extracted = plugin.extract(path)
            print(f"File {path} parsed by {plugin.__class__.__name__}. Extracted fields count: {len(extracted)}")
            all_raw_fields.extend(extracted)

# Group by record_id
grouped = {}
for f in all_raw_fields:
    rid = f["record_id"]
    if rid not in grouped:
        grouped[rid] = []
    grouped[rid].append(f)

print(f"\nGrouped records count: {len(grouped)}")
for rid, fields in grouped.items():
    print(f"Record: {rid}, Fields count: {len(fields)}")
    for f in fields:
        print(f"  Field: {f['target_field']}, Raw Key: {f['raw_key']}, Value: {str(f['raw_value'])[:30]}")
