import uuid
from typing import List, Dict, Any, Tuple, Optional
from models.candidate import Candidate, Location, Experience, Education, ProvenanceRecord, ExtraField
from plugins.base import BaseSourcePlugin, RawCandidateField
from plugins.csv_plugin import CSVPlugin
from plugins.ats_json_plugin import ATSJsonPlugin
from plugins.resume_text_plugin import ResumeTextPlugin
from plugins.linkedin_plugin import LinkedInPlugin
from plugins.recruiter_notes_plugin import RecruiterNotesPlugin

from normalizers.phone import PhoneNormalizer
from normalizers.date import DateNormalizer
from normalizers.skill import SkillNormalizer
from normalizers.name import NameNormalizer
from normalizers.location import LocationNormalizer

# Similarity helper for identity resolution
def fuzzy_string_match(s1: str, s2: str) -> float:
    s1_clean = str(s1).strip().lower()
    s2_clean = str(s2).strip().lower()
    if not s1_clean or not s2_clean:
        return 0.0
    if s1_clean == s2_clean:
        return 1.0
        
    try:
        from rapidfuzz import fuzz
        return fuzz.token_sort_ratio(s1_clean, s2_clean) / 100.0
    except ImportError:
        pass
        
    # Jaccard 2-gram char fallback
    def ngrams(s, n=2):
        return set(s[i:i+n] for i in range(len(s)-n+1))
    g1, g2 = ngrams(s1_clean), ngrams(s2_clean)
    if not g1 or not g2:
        return 1.0 if s1_clean == s2_clean else 0.0
    return len(g1.intersection(g2)) / float(len(g1.union(g2)))


class CandidateTransformerEngine:
    def __init__(self):
        # Register plugins
        self.plugins: List[BaseSourcePlugin] = [
            ATSJsonPlugin(),
            LinkedInPlugin(),
            CSVPlugin(),
            ResumeTextPlugin(),
            RecruiterNotesPlugin()
        ]
        
        # Register normalizers
        self.phone_normalizer = PhoneNormalizer()
        self.date_normalizer = DateNormalizer()
        self.skill_normalizer = SkillNormalizer()
        self.name_normalizer = NameNormalizer()
        self.location_normalizer = LocationNormalizer()

    def run_pipeline(self, file_paths: List[str]) -> List[Candidate]:
        """
        Runs the full candidate transformer pipeline on a list of input files:
        detect -> extract -> normalize -> resolve identity -> merge.
        """
        # 1. Detect & Extract raw fields
        all_raw_fields: List[RawCandidateField] = []
        for path in file_paths:
            plugin_found = False
            for plugin in self.plugins:
                if plugin.detect(path):
                    plugin_found = True
                    extracted = plugin.extract(path)
                    if extracted:
                        all_raw_fields.extend(extracted)
                    break
            if not plugin_found:
                print(f"Warning: No plugin registered to parse source: {path}")

        if not all_raw_fields:
            return []

        # 2. Date Format Lock Analysis
        # Analyze dates per source to find a consistent format
        source_formats = self._analyze_date_formats(all_raw_fields)
        context = {"source_formats": source_formats}

        # Group raw fields by record_id (candidate profile from a specific file)
        grouped_raw: Dict[str, List[RawCandidateField]] = {}
        for field in all_raw_fields:
            rid = field["record_id"]
            if rid not in grouped_raw:
                grouped_raw[rid] = []
            grouped_raw[rid].append(field)

        # Normalize and construct individual candidates
        individual_candidates: List[Candidate] = []
        for record_id, fields in grouped_raw.items():
            candidate = self._normalize_record(record_id, fields, context)
            individual_candidates.append(candidate)

        # 3. Identity Resolution
        resolved_components = self._resolve_identities(individual_candidates)

        # 4. Merge candidates
        merged_candidates: List[Candidate] = []
        for component in resolved_components:
            if len(component) == 1:
                # No merge needed, just recalculate overall confidence
                c = component[0]
                self._calculate_overall_confidence(c)
                merged_candidates.append(c)
            else:
                # Merge multiple profiles
                merged = self._merge_candidates(component)
                self._calculate_overall_confidence(merged)
                merged_candidates.append(merged)

        return merged_candidates

    def _analyze_date_formats(self, fields: List[RawCandidateField]) -> Dict[str, str]:
        """
        Analyzes slash-separated dates in a source to lock onto a consistent format (MM/DD/YYYY vs DD/MM/YYYY).
        """
        source_dates: Dict[str, List[str]] = {}
        for f in fields:
            # We look at fields likely containing dates
            if f["target_field"] in ["experience", "education"] and isinstance(f["raw_value"], list):
                for item in f["raw_value"]:
                    if isinstance(item, dict):
                        for k in ["start_date", "end_date", "start", "end"]:
                            if k in item and item[k]:
                                src = f["source"]
                                if src not in source_dates:
                                    source_dates[src] = []
                                source_dates[src].append(str(item[k]))
            elif isinstance(f["raw_value"], str):
                # Simple check for slash format in any string raw value
                if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', f["raw_value"]):
                    src = f["source"]
                    if src not in source_dates:
                        source_dates[src] = []
                    source_dates[src].append(f["raw_value"])

        locks: Dict[str, str] = {}
        for src, dates in source_dates.items():
            dd_mm_votes = 0
            mm_dd_votes = 0
            for d in dates:
                match = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b', d)
                if match:
                    v1 = int(match.group(1))
                    v2 = int(match.group(2))
                    # Check if deterministic segment > 12
                    if v1 > 12 and v2 <= 12:
                        dd_mm_votes += 1
                    elif v2 > 12 and v1 <= 12:
                        mm_dd_votes += 1
            if dd_mm_votes > 0 and mm_dd_votes == 0:
                locks[src] = "DD/MM/YYYY"
            elif mm_dd_votes > 0 and dd_mm_votes == 0:
                locks[src] = "MM/DD/YYYY"
            # If votes are tied or conflicting, we do not lock a format
        return locks

    def _normalize_record(self, record_id: str, fields: List[RawCandidateField], global_context: dict) -> Candidate:
        # Create a blank candidate profile
        # Assign candidate ID from the fields, or generate a temporary one
        candidate_id = str(uuid.uuid4())
        for f in fields:
            if f["target_field"] == "candidate_id":
                candidate_id = str(f["raw_value"])
                break

        candidate = Candidate(candidate_id=candidate_id)
        
        # Maps evidence tier to baseline confidence (matched ceiling limits)
        tier_confidences = {
            "A": 0.95,
            "B": 0.70,
            "C": 0.50,
            "D": 0.30
        }

        for f in fields:
            target = f["target_field"]
            raw_key = f["raw_key"]
            raw_val = f["raw_value"]
            tier = f["evidence_tier"]
            source = f["source"]
            tstamp = f["timestamp"]
            
            # Baseline evidence confidence
            evidence_conf = tier_confidences.get(tier, 0.30)
            
            # Form context for normalizer
            context = {
                "source": source,
                "source_formats": global_context.get("source_formats", {})
            }

            # Handle extra fields / unmapped fields
            if target == "extra_fields":
                candidate.extra_fields.append(ExtraField(
                    raw_key=raw_key,
                    value=raw_val,
                    source=source,
                    reason="Explicit unmapped field in source plugin"
                ))
                continue

            # Normalize values based on target type
            if target == "full_name":
                norm = self.name_normalizer.normalize(raw_val, context)
                candidate.full_name = norm["value"]
                candidate.provenance.append(ProvenanceRecord(
                    source=source, field=target, raw_key=raw_key, raw_value=raw_val,
                    normalized_value=norm["value"], evidence_tier=tier, confidence=evidence_conf,
                    timestamp=tstamp, action="normalized", norm_confidence=norm["confidence"], norm_note=norm["note"]
                ))
            elif target == "emails":
                # Raw value could be string or list.
                # Emails/Phones arrays union + dedupe, but in individual record it is single-source
                emails_to_normalize = raw_val if isinstance(raw_val, list) else [raw_val]
                for email in emails_to_normalize:
                    email_str = str(email).strip().lower()
                    if email_str and email_str not in candidate.emails:
                        candidate.emails.append(email_str)
                        candidate.provenance.append(ProvenanceRecord(
                            source=source, field=target, raw_key=raw_key, raw_value=email,
                            normalized_value=email_str, evidence_tier=tier, confidence=evidence_conf,
                            timestamp=tstamp, action="normalized", norm_confidence=0.95, norm_note="Lowercased email."
                        ))
            elif target == "phones":
                phones_to_normalize = raw_val if isinstance(raw_val, list) else [raw_val]
                for phone in phones_to_normalize:
                    # In normalizer we check if country code can be inferred from other location fields
                    # Search for any location raw fields in this same record
                    loc_raw = next((lf["raw_value"] for lf in fields if lf["target_field"] == "location"), None)
                    if loc_raw:
                        loc_norm = self.location_normalizer.normalize(loc_raw)
                        if loc_norm["value"]:
                            context["country"] = loc_norm["value"].country

                    norm = self.phone_normalizer.normalize(phone, context)
                    if norm["value"] and norm["value"] not in candidate.phones:
                        candidate.phones.append(norm["value"])
                        candidate.provenance.append(ProvenanceRecord(
                            source=source, field=target, raw_key=raw_key, raw_value=phone,
                            normalized_value=norm["value"], evidence_tier=tier, confidence=evidence_conf,
                            timestamp=tstamp, action="normalized", norm_confidence=norm["confidence"], norm_note=norm["note"]
                        ))
            elif target == "location":
                norm = self.location_normalizer.normalize(raw_val, context)
                candidate.location = norm["value"]
                candidate.provenance.append(ProvenanceRecord(
                    source=source, field=target, raw_key=raw_key, raw_value=raw_val,
                    normalized_value=norm["value"], evidence_tier=tier, confidence=evidence_conf,
                    timestamp=tstamp, action="normalized", norm_confidence=norm["confidence"], norm_note=norm["note"]
                ))
            elif target == "links":
                links_to_normalize = raw_val if isinstance(raw_val, list) else [raw_val]
                for link in links_to_normalize:
                    link_str = str(link).strip()
                    if link_str and link_str not in candidate.links:
                        candidate.links.append(link_str)
                        candidate.provenance.append(ProvenanceRecord(
                            source=source, field=target, raw_key=raw_key, raw_value=link,
                            normalized_value=link_str, evidence_tier=tier, confidence=evidence_conf,
                            timestamp=tstamp, action="normalized", norm_confidence=0.95, norm_note="Trimmed link."
                        ))
            elif target == "headline":
                # Raw value could be a summary
                headline_str = str(raw_val).strip()
                candidate.headline = headline_str
                candidate.provenance.append(ProvenanceRecord(
                    source=source, field=target, raw_key=raw_key, raw_value=raw_val,
                    normalized_value=headline_str, evidence_tier=tier, confidence=evidence_conf,
                    timestamp=tstamp, action="normalized", norm_confidence=0.90, norm_note="Stripped headline."
                ))
            elif target == "years_experience":
                try:
                    years = float(raw_val)
                    candidate.years_experience = years
                    candidate.provenance.append(ProvenanceRecord(
                        source=source, field=target, raw_key=raw_key, raw_value=raw_val,
                        normalized_value=years, evidence_tier=tier, confidence=evidence_conf,
                        timestamp=tstamp, action="normalized", norm_confidence=0.95, norm_note="Float cast."
                    ))
                except ValueError:
                    candidate.extra_fields.append(ExtraField(
                        raw_key=raw_key, value=raw_val, source=source,
                        reason="Failed to normalize years_experience: not a float"
                    ))
            elif target == "skills":
                norm = self.skill_normalizer.normalize(raw_val, context)
                # Map matched skills
                if norm["value"]:
                    for skill in norm["value"]:
                        if skill not in candidate.skills:
                            candidate.skills.append(skill)
                            candidate.provenance.append(ProvenanceRecord(
                                source=source, field=target, raw_key=raw_key, raw_value=raw_val,
                                normalized_value=skill, evidence_tier=tier, confidence=evidence_conf,
                                timestamp=tstamp, action="normalized", norm_confidence=norm["confidence"], norm_note=norm["note"]
                            ))
                # Map unmatched skills to extra_fields
                # Unmatched is a custom return key
                unmatched = norm.get("unmatched", []) # type: ignore
                for un_skill in unmatched:
                    candidate.extra_fields.append(ExtraField(
                        raw_key=f"skill_unmatched:{raw_key}",
                        value=un_skill,
                        source=source,
                        reason=f"Skill '{un_skill}' was not found in the canonical skill dictionary"
                    ))
            elif target == "experience":
                # Experience is parsed structured records
                raw_exp_list = raw_val if isinstance(raw_val, list) else [raw_val]
                for exp_dict in raw_exp_list:
                    if not isinstance(exp_dict, dict):
                        continue
                    company = str(exp_dict.get("company", "")).strip()
                    if not company:
                        continue
                    
                    # Normalize start/end dates
                    raw_start = exp_dict.get("start_date") or exp_dict.get("start")
                    raw_end = exp_dict.get("end_date") or exp_dict.get("end")
                    
                    norm_start = self.date_normalizer.normalize(raw_start, context)["value"] if raw_start else None
                    norm_end = self.date_normalizer.normalize(raw_end, context)["value"] if raw_end else None

                    exp_entry = Experience(
                        company=company,
                        title=exp_dict.get("title"),
                        start_date=norm_start,
                        end_date=norm_end,
                        description=exp_dict.get("description")
                    )
                    candidate.experience.append(exp_entry)
                    candidate.provenance.append(ProvenanceRecord(
                        source=source, field=target, raw_key=raw_key, raw_value=exp_dict,
                        normalized_value=exp_entry, evidence_tier=tier, confidence=evidence_conf,
                        timestamp=tstamp, action="normalized", norm_confidence=0.90, norm_note="Structured experience normalized."
                    ))
            elif target == "education":
                # Education is parsed structured records
                raw_edu_list = raw_val if isinstance(raw_val, list) else [raw_val]
                for edu_dict in raw_edu_list:
                    if not isinstance(edu_dict, dict):
                        continue
                    inst = str(edu_dict.get("institution", "")).strip()
                    if not inst:
                        continue

                    raw_start = edu_dict.get("start_date") or edu_dict.get("start")
                    raw_end = edu_dict.get("end_date") or edu_dict.get("end")

                    norm_start = self.date_normalizer.normalize(raw_start, context)["value"] if raw_start else None
                    norm_end = self.date_normalizer.normalize(raw_end, context)["value"] if raw_end else None

                    edu_entry = Education(
                        institution=inst,
                        degree=edu_dict.get("degree"),
                        field_of_study=edu_dict.get("field_of_study"),
                        start_date=norm_start,
                        end_date=norm_end
                    )
                    candidate.education.append(edu_entry)
                    candidate.provenance.append(ProvenanceRecord(
                        source=source, field=target, raw_key=raw_key, raw_value=edu_dict,
                        normalized_value=edu_entry, evidence_tier=tier, confidence=evidence_conf,
                        timestamp=tstamp, action="normalized", norm_confidence=0.90, norm_note="Structured education normalized."
                    ))

        return candidate

    def _resolve_identities(self, candidates: List[Candidate]) -> List[List[Candidate]]:
        """
        Groups individual candidate profiles into resolved identities using logic rules:
        - Overlapping email OR overlapping phone -> strong merge link.
        - Name similarity + company overlap -> weak signals corroborating link.
        - Name similarity alone -> NEVER merges.
        Returns connected components of candidates.
        """
        n = len(candidates)
        adj: Dict[int, List[int]] = {i: [] for i in range(n)}

        for i in range(n):
            for j in range(i + 1, n):
                c1 = candidates[i]
                c2 = candidates[j]
                
                link = False
                reason = ""
                
                # Check strong signals
                # 1. Matching email
                common_emails = set(c1.emails).intersection(set(c2.emails))
                if common_emails:
                    link = True
                    reason = f"Matching email: {common_emails}"

                # 2. Matching phone
                if not link:
                    common_phones = set(c1.phones).intersection(set(c2.phones))
                    if common_phones:
                        link = True
                        reason = f"Matching phone: {common_phones}"

                # Check weak signals (Name similarity + Company overlap)
                if not link and c1.full_name and c2.full_name:
                    name_score = fuzzy_string_match(c1.full_name, c2.full_name)
                    if name_score >= 0.85:
                        # Names are highly similar. Check company overlap in experience.
                        company_overlap = False
                        matched_company = ""
                        for exp1 in c1.experience:
                            for exp2 in c2.experience:
                                if exp1.company and exp2.company:
                                    comp_score = fuzzy_string_match(exp1.company, exp2.company)
                                    if comp_score >= 0.85:
                                        company_overlap = True
                                        matched_company = f"{exp1.company} / {exp2.company}"
                                        break
                            if company_overlap:
                                break
                        
                        if company_overlap:
                            link = True
                            reason = f"Weak signals corroborated: name match ({c1.full_name} ~ {c2.full_name}) and company overlap ({matched_company})"
                
                if link:
                    adj[i].append(j)
                    adj[j].append(i)

        # BFS/DFS to find connected components
        visited = set()
        components: List[List[Candidate]] = []
        for i in range(n):
            if i not in visited:
                comp = []
                queue = [i]
                visited.add(i)
                while queue:
                    curr = queue.pop(0)
                    comp.append(candidates[curr])
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                components.append(comp)

        return components

    def _merge_candidates(self, group: List[Candidate]) -> Candidate:
        """
        Merges a group of candidate profiles representing the same identity.
        """
        # Pick candidate ID of the first (or build a stable one)
        base_cand = Candidate(candidate_id=group[0].candidate_id)

        # Merge Array Fields (Union + Deduplicate)
        # emails, phones, skills, links
        for c in group:
            for em in c.emails:
                if em not in base_cand.emails:
                    base_cand.emails.append(em)
            for ph in c.phones:
                if ph not in base_cand.phones:
                    base_cand.phones.append(ph)
            for sk in c.skills:
                if sk not in base_cand.skills:
                    base_cand.skills.append(sk)
            for lk in c.links:
                if lk not in base_cand.links:
                    base_cand.links.append(lk)
            
            # Combine extra fields
            for ef in c.extra_fields:
                # Avoid duplicates
                if not any(e.raw_key == ef.raw_key and e.value == ef.value and e.source == ef.source for e in base_cand.extra_fields):
                    base_cand.extra_fields.append(ef)

        # Merge Single-Valued Fields
        # full_name, headline, years_experience, location
        base_cand.full_name = self._merge_single_field("full_name", group, base_cand)
        base_cand.headline = self._merge_single_field("headline", group, base_cand)
        base_cand.years_experience = self._merge_single_field("years_experience", group, base_cand)
        base_cand.location = self._merge_single_field("location", group, base_cand)

        # Merge Experience and Education lists (Union + Deduplicate)
        self._merge_experience(group, base_cand)
        self._merge_education(group, base_cand)

        # Combine all normalized provenance records
        # Keep track of winner actions vs superseded actions
        for c in group:
            for prov in c.provenance:
                # Add to base candidate provenance
                base_cand.provenance.append(prov)

        return base_cand

    def _merge_single_field(self, field_name: str, group: List[Candidate], base_cand: Candidate) -> Any:
        """
        Selects the winning value for a single-valued field based on priority rules:
        1. Higher evidence tier (A > B > C > D)
        2. More recent source timestamp
        3. Higher post-normalization confidence
        Also marks losing values as discarded in provenance, and applies a conflict penalty if values disagree.
        """
        tier_ranks = {"A": 4, "B": 3, "C": 2, "D": 1}
        
        # Collect all candidates' values for this field
        candidates_values: List[Tuple[Any, ProvenanceRecord]] = []
        for c in group:
            for p in c.provenance:
                if p.field == field_name and p.action == "normalized":
                    candidates_values.append((p.normalized_value, p))

        if not candidates_values:
            return None

        # Sort values based on priority rules
        # Tuple sort: (tier_rank, timestamp, norm_confidence)
        # Python sorts ascending, so we use negative/inversions for highest first
        def priority_key(item: Tuple[Any, ProvenanceRecord]) -> Tuple[int, float, float]:
            val, prov = item
            tr = tier_ranks.get(prov.evidence_tier, 0)
            return (tr, prov.timestamp, prov.norm_confidence)

        candidates_values.sort(key=priority_key, reverse=True)
        
        winner_val, winner_prov = candidates_values[0]

        # Check for disagreement across sources
        # Disagreement is when a different source has a different normalized value
        disagreement = False
        distinct_values = []
        
        for val, prov in candidates_values:
            # We compare string representations or exact objects
            val_str = str(val).strip().lower() if val else ""
            if val_str and val_str not in distinct_values:
                distinct_values.append(val_str)
        
        if len(distinct_values) > 1:
            disagreement = True

        # Handle losers
        for val, prov in candidates_values[1:]:
            prov.action = "discarded"
            prov.reason = f"Superseded by value '{winner_val}' from '{winner_prov.source}' due to higher priority."

        # Mark winner
        winner_prov.action = "merged"

        # Apply conflict penalty and weak signal promotion
        if disagreement:
            # Conflict penalty: reduce evidence confidence by 0.10
            penalty = 0.10
            old_conf = winner_prov.confidence
            new_conf = max(0.0, old_conf - penalty)
            winner_prov.confidence = new_conf
            winner_prov.reason = f"Conflict penalty applied: reduced confidence from {old_conf:.2f} to {new_conf:.2f} due to disagreeing source values."
        else:
            # Check for weak signal promotion if multiple independent sources agree on the same value
            unique_sources = set(item[1].source for item in candidates_values if str(item[0]).strip().lower() == str(winner_val).strip().lower())
            if len(unique_sources) > 1:
                # Agreeing signals promote confidence using probabilistic union formula:
                # c_new = c1 + c2 - c1 * c2
                # We do this iteratively for all agreeing sources
                curr_conf = winner_prov.confidence
                agree_provs = [item[1] for item in candidates_values if str(item[0]).strip().lower() == str(winner_val).strip().lower() and item[1] != winner_prov]
                for p in agree_provs:
                    other_conf = p.confidence
                    curr_conf = curr_conf + other_conf - (curr_conf * other_conf)
                
                promoted_conf = min(0.95, curr_conf)  # ceiling at 0.95
                if promoted_conf > winner_prov.confidence:
                    winner_prov.reason = f"Weak signal promotion: increased confidence from {winner_prov.confidence:.2f} to {promoted_conf:.2f} based on agreement from {len(unique_sources)} sources."
                    winner_prov.confidence = promoted_conf

        return winner_val

    def _merge_experience(self, group: List[Candidate], base_cand: Candidate):
        """
        Unions and deduplicates experiences.
        """
        all_exps: List[Experience] = []
        for c in group:
            for exp in c.experience:
                # Check for overlap with existing
                overlap = False
                for existing in all_exps:
                    comp_score = fuzzy_string_match(exp.company, existing.company)
                    title_score = fuzzy_string_match(exp.title or "", existing.title or "")
                    if comp_score >= 0.85 and title_score >= 0.75:
                        overlap = True
                        # Merge details (e.g. union descriptions, resolve start/end dates)
                        if exp.description and exp.description not in (existing.description or ""):
                            if existing.description:
                                existing.description += "\n" + exp.description
                            else:
                                existing.description = exp.description
                        if exp.start_date and (not existing.start_date or exp.start_date < existing.start_date):
                            existing.start_date = exp.start_date
                        if exp.end_date and (not existing.end_date or exp.end_date > existing.end_date):
                            existing.end_date = exp.end_date
                        break
                if not overlap:
                    all_exps.append(Experience(
                        company=exp.company,
                        title=exp.title,
                        start_date=exp.start_date,
                        end_date=exp.end_date,
                        description=exp.description
                    ))
        base_cand.experience = all_exps

    def _merge_education(self, group: List[Candidate], base_cand: Candidate):
        """
        Unions and deduplicates education.
        """
        all_edus: List[Education] = []
        for c in group:
            for edu in c.education:
                overlap = False
                for existing in all_edus:
                    inst_score = fuzzy_string_match(edu.institution, existing.institution)
                    deg_score = fuzzy_string_match(edu.degree or "", existing.degree or "")
                    if inst_score >= 0.85 and deg_score >= 0.75:
                        overlap = True
                        if edu.start_date and (not existing.start_date or edu.start_date < existing.start_date):
                            existing.start_date = edu.start_date
                        if edu.end_date and (not existing.end_date or edu.end_date > existing.end_date):
                            existing.end_date = edu.end_date
                        break
                if not overlap:
                    all_edus.append(Education(
                        institution=edu.institution,
                        degree=edu.degree,
                        field_of_study=edu.field_of_study,
                        start_date=edu.start_date,
                        end_date=edu.end_date
                    ))
        base_cand.education = all_edus

    def _calculate_overall_confidence(self, candidate: Candidate):
        """
        Calculates and updates the overall confidence of the candidate based on average field confidences.
        """
        # Collect the evidence confidence of all resolved / merged fields from provenance
        field_confidences: Dict[str, List[float]] = {}
        for p in candidate.provenance:
            if p.action in ["merged", "normalized"]:
                if p.field not in field_confidences:
                    field_confidences[p.field] = []
                field_confidences[p.field].append(p.confidence)
        
        if not field_confidences:
            candidate.overall_confidence = 0.0
            return

        # Average for each field first, then average across fields
        avg_field_confs = []
        for field, confs in field_confidences.items():
            avg_field_confs.append(sum(confs) / len(confs))

        candidate.overall_confidence = sum(avg_field_confs) / len(avg_field_confs)
