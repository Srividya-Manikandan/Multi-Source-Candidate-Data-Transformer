import sys
import os
sys.path.insert(0, os.path.abspath("."))

from models.candidate import Candidate, ProvenanceRecord
from engine.pipeline import CandidateTransformerEngine
from engine.explainability import DecisionLogCompiler
from engine.dashboard import QualityDashboardCompiler
from models.reporting import ValidationResult

def test_unresolved_conflict():
    engine = CandidateTransformerEngine()
    
    # Create candidate 1
    c1 = Candidate(candidate_id="ATS-001")
    c1.emails = ["test@example.com"]
    p1 = ProvenanceRecord(
        field="full_name",
        raw_key="name",
        raw_value="John Tie A",
        normalized_value="John Tie A",
        evidence_tier="A",
        confidence=0.95,
        norm_confidence=0.95,
        timestamp=1000.0,
        source="source_a.json",
        action="normalized"
    )
    c1.provenance.append(p1)
    
    # Create candidate 2
    c2 = Candidate(candidate_id="ATS-002")
    c2.emails = ["test@example.com"]  # To make them merge by email
    p2 = ProvenanceRecord(
        field="full_name",
        raw_key="name",
        raw_value="John Tie B",
        normalized_value="John Tie B",
        evidence_tier="A",
        confidence=0.95,
        norm_confidence=0.95,
        timestamp=1000.0,  # Exact tie: tier=A, timestamp=1000, confidence=0.95
        source="source_b.json",
        action="normalized"
    )
    c2.provenance.append(p2)
    
    # Run resolution and merge
    groups = engine._resolve_identities([c1, c2])
    print(f"Total components: {len(groups)}")
    merged = engine._merge_candidates(groups[0])
    
    print(f"Merged Name: {merged.full_name}")
    print(f"Provenance items for full_name: {len(merged.provenance)}")
    for p in merged.provenance:
        print(f"  Field: {p.field}, Value: {p.normalized_value}, Action: {p.action}, Conf: {p.confidence}, Reason: {p.reason}")
        
    assert merged.full_name is None, "Name should be null on tie!"
    for p in merged.provenance:
        assert p.action == "tied", "Action should be tied!"
        assert p.confidence == 0.15, "Confidence should be 0.15!"
        
    # Test dashboard compilation
    dashboard_compiler = QualityDashboardCompiler()
    dashboard = dashboard_compiler.compile_dashboard(
        raw_candidates_count=2,
        merged_candidates=[merged],
        validation_results=[ValidationResult(is_valid=True, errors=[])],
        malformed_sources_skipped=0
    )
    print("\nDashboard resolution_metrics:")
    print(dashboard["resolution_metrics"])
    
    assert dashboard["resolution_metrics"]["unresolved_conflicts"] == 1
    assert "full_name" in dashboard["resolution_metrics"]["unresolved_fields"]
    
    # Test decision log compiler
    log_compiler = DecisionLogCompiler()
    log = log_compiler.compile_log(merged)
    print("\nDecision log fields['full_name']:")
    print(log["fields"]["full_name"])
    
    assert log["fields"]["full_name"]["winner_details"]["value"] is None
    assert log["fields"]["full_name"]["winner_details"]["unresolved_conflict"] is True
    
    print("\n[SUCCESS] Unresolved conflict test passed successfully!")

if __name__ == "__main__":
    test_unresolved_conflict()
