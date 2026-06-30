import os
from typing import List
from plugins.base import BaseSourcePlugin, RawCandidateField

class ResumeTextPlugin(BaseSourcePlugin):
    def detect(self, source_path: str) -> bool:
        # Simplistic detection: check extension and file name signature
        filename = os.path.basename(source_path).lower()
        return filename.endswith('.txt') and ('resume' in filename or 'cv' in filename)

    def extract(self, source_path: str) -> List[RawCandidateField]:
        # Skeleton: return empty list for now
        # Extraction logic will be implemented in subsequent phases
        return []
