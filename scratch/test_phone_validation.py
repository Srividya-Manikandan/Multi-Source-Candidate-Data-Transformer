import sys
import os
sys.path.insert(0, os.path.abspath("."))

from models.candidate import Candidate
from normalizers.phone import PhoneNormalizer
from engine.pipeline import CandidateTransformerEngine

def test_phone_normalization_and_validation():
    normalizer = PhoneNormalizer()
    
    # 1. Test canonical formatting of same number in different formats
    res1 = normalizer.normalize("+1 617-555-0244", {"country": "US"})
    res2 = normalizer.normalize("6175550244", {"country": "US"})
    res3 = normalizer.normalize("+16175550244", {"country": "US"})
    
    print(f"res1 format check: {res1['value']}")
    print(f"res2 format check: {res2['value']}")
    print(f"res3 format check: {res3['value']}")
    
    assert res1["value"] == "+16175550244"
    assert res2["value"] == "+16175550244"
    assert res3["value"] == "+16175550244"
    print("[OK] Canonical E.164 formatting validation passed.")

    # 2. Test invalid phone number validation
    res_invalid = normalizer.normalize("5550244", {"country": "US"})
    print(f"res_invalid check: value={res_invalid['value']}, confidence={res_invalid['confidence']}, note={res_invalid['note']}")
    assert res_invalid["value"] is None
    assert res_invalid["confidence"] == 0.0
    assert "Invalid US phone number" in res_invalid["note"]
    print("[OK] Invalid phone validation rules enforcement passed.")

    # 3. Test pipeline routing of invalid phone to extra_fields
    engine = CandidateTransformerEngine()
    raw_fields = [
        {
            "target_field": "phones",
            "raw_key": "phone",
            "raw_value": "5550244",
            "evidence_tier": "A",
            "source": "resume.txt",
            "timestamp": 1000.0,
            "record_id": "resume#default"
        },
        {
            "target_field": "location",
            "raw_key": "location",
            "raw_value": "Boston, MA, US",
            "evidence_tier": "A",
            "source": "resume.txt",
            "timestamp": 1000.0,
            "record_id": "resume#default"
        }
    ]
    candidate = engine._normalize_record("resume#default", raw_fields, {})
    print(f"Canonical phones in candidate: {candidate.phones}")
    print(f"Extra fields: {[ (ef.raw_key, ef.value, ef.reason) for ef in candidate.extra_fields ]}")
    
    assert "5550244" not in candidate.phones
    assert len(candidate.extra_fields) == 1
    assert candidate.extra_fields[0].raw_key == "phone"
    assert "Invalid phone number" in candidate.extra_fields[0].reason
    print("[OK] Pipeline routing of invalid phone numbers to extra_fields passed.")

    # 4. Test merge exact deduplication
    c1 = Candidate(candidate_id="c1")
    c1.phones = ["+16175550244"]
    
    c2 = Candidate(candidate_id="c2")
    c2.phones = ["+16175550244"]
    
    merged = engine._merge_candidates([c1, c2])
    print(f"Merged candidate phones: {merged.phones}")
    assert len(merged.phones) == 1
    assert merged.phones[0] == "+16175550244"
    print("[OK] Merge phone deduplication exact equality check passed.")

if __name__ == "__main__":
    test_phone_normalization_and_validation()
