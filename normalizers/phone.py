import os
import json
import re
from typing import Any, Dict, Optional
from normalizers.base import BaseNormalizer, NormalizationResult

def load_phone_rules() -> dict:
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, 'configs', 'phone_rules.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Warning: Failed to load phone rules: {e}")
    return {}

class PhoneNormalizer(BaseNormalizer):
    def normalize(self, raw: Any, context: Optional[Dict[str, Any]] = None) -> NormalizationResult:
        if not raw:
            return {"value": "", "confidence": 0.0, "note": "Empty phone value."}
        
        raw_str = str(raw).strip()
        
        # Cleaned digits extraction
        digits_only = re.sub(r'\D', '', raw_str)
        if not digits_only:
            return {"value": "", "confidence": 0.0, "note": "No digits found in phone number."}

        # Determine country signal from location context
        country_code = None
        if context and 'country' in context and context['country']:
            country_code = str(context['country']).upper()

        # Priority 1: phonenumbers library validation
        try:
            import phonenumbers
            parsed_phone = None
            if country_code:
                try:
                    parsed_phone = phonenumbers.parse(raw_str, country_code)
                except phonenumbers.NumberParseException:
                    pass
            
            if not parsed_phone and raw_str.startswith('+'):
                try:
                    parsed_phone = phonenumbers.parse(raw_str, None)
                except phonenumbers.NumberParseException:
                    pass

            if parsed_phone and phonenumbers.is_valid_number(parsed_phone):
                formatted = phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
                return {
                    "value": formatted,
                    "confidence": 0.95,
                    "note": f"Successfully parsed and formatted to E.164 via phonenumbers using country '{country_code or 'embedded'}'."
                }
        except ImportError:
            pass

        # Load configurations
        phone_rules = load_phone_rules()

        # Priority 2: Country signal exists, fallback to phone_rules.json validation
        if country_code:
            if country_code in phone_rules:
                rule = phone_rules[country_code]
                dial_code = str(rule["dialing_code"])
                nat_len = int(rule["national_length"])
                
                # Check Case A: starts with dialing code and has length of dialing code + national length
                if digits_only.startswith(dial_code) and len(digits_only) == len(dial_code) + nat_len:
                    return {
                        "value": f"+{digits_only}",
                        "confidence": 0.80,
                        "note": f"Fallback valid: Match dialing code '+{dial_code}' and national length {nat_len} for country '{country_code}'."
                    }
                # Check Case B: lacks dialing code but matches national length exactly
                elif len(digits_only) == nat_len:
                    return {
                        "value": f"+{dial_code}{digits_only}",
                        "confidence": 0.80,
                        "note": f"Fallback valid: Prepended dialing code '+{dial_code}' for national length {nat_len} for country '{country_code}'."
                    }
                else:
                    # Invalid numbers: failed country rules
                    formatted_val = f"+{digits_only}" if raw_str.startswith('+') else digits_only
                    return {
                        "value": formatted_val,
                        "confidence": 0.20,
                        "note": f"Invalid number: Fails validation rules for country '{country_code}' (expected length {nat_len} or starts with dial code '{dial_code}')."
                    }
            else:
                # Country signal exists but no rule configured
                formatted_val = f"+{digits_only}" if raw_str.startswith('+') else digits_only
                return {
                    "value": formatted_val,
                    "confidence": 0.20,
                    "note": f"Invalid number: Country rule for '{country_code}' is not configured in phone_rules.json."
                }

        # Priority 3: No country signal exists
        # Keep only the cleaned digits, do not prepend country code, cap confidence.
        # Check if it looks E.164 already (starts with + and has digits) to preserve leading + if appropriate,
        # but do not invent/prepend country code.
        formatted_val = digits_only
        return {
            "value": formatted_val,
            "confidence": 0.50,
            "note": "No country signal and no phonenumbers validation. Digits kept as-is, confidence capped."
        }
