"""
Prompt template management for video analysis.
Loads and manages XML prompt templates for different video types.
"""
import os
from typing import Dict, List, Optional
from pathlib import Path

from app.config import config_manager


# Video type definitions with display names
VIDEO_TYPES = {
    "general": {
        "name": "General Analysis",
        "description": "Universal video analysis for any content type",
        "prompt_file": "chunk_analysis_prompt.xml"
    },
    "lecture": {
        "name": "Lecture / Educational",
        "description": "Educational content, online courses, lectures",
        "prompt_file": "chunk_analysis_lecture.xml"
    },
    "tutorial": {
        "name": "Tutorial / How-to",
        "description": "Step-by-step tutorials and guides",
        "prompt_file": "chunk_analysis_tutorial.xml"
    },
    "marketing": {
        "name": "Marketing / Product Demo",
        "description": "Product demonstrations, advertisements, promotional videos",
        "prompt_file": "chunk_analysis_marketing.xml"
    },
    "presentation": {
        "name": "Presentation / Pitch",
        "description": "Business presentations, pitches, investor decks",
        "prompt_file": "chunk_analysis_presentation.xml"
    },
    "meeting": {
        "name": "Meeting / Standup",
        "description": "Work meetings, standups, team calls",
        "prompt_file": "chunk_analysis_meeting.xml"
    },
    "interview": {
        "name": "Interview Evaluation",
        "description": "Job interviews with detailed candidate evaluation",
        "prompt_file": "chunk_analysis_interview.xml"
    },
    "language_lesson": {
        "name": "Language Lesson",
        "description": "Language instruction with full transcription",
        "prompt_file": "chunk_analysis_language_lesson.xml"
    },
    "voiceover": {
        "name": "Voiceover / Sound Design",
        "description": "Generate AI music/SFX prompts based on video",
        "prompt_file": "chunk_analysis_voiceover.xml"
    }
}


class PromptManager:
    """Manages loading and formatting of prompt templates."""

    def __init__(self, prompts_dir: Optional[str] = None):
        self.prompts_dir = prompts_dir or config_manager.settings.prompts_directory
        self._cache: Dict[str, str] = {}

    def get_available_types(self) -> List[Dict]:
        """Get list of available video types with their info."""
        result = []
        for type_id, info in VIDEO_TYPES.items():
            prompt_path = os.path.join(self.prompts_dir, info["prompt_file"])
            result.append({
                "id": type_id,
                "name": info["name"],
                "description": info["description"],
                "available": os.path.exists(prompt_path)
            })
        return result

    def get_keyframes_criteria_default(self) -> str:
        """Get default keyframes criteria description (editable by user)."""
        cache_key = "keyframes_criteria_default"

        if cache_key in self._cache:
            return self._cache[cache_key]

        criteria_path = os.path.join(self.prompts_dir, "keyframes_criteria_default.xml")

        if not os.path.exists(criteria_path):
            return ""

        with open(criteria_path, "r", encoding="utf-8") as f:
            criteria = f.read()

        self._cache[cache_key] = criteria
        return criteria

    def get_keyframes_format(self) -> str:
        """Get keyframes JSON format specification (fixed, not editable)."""
        cache_key = "keyframes_format"

        if cache_key in self._cache:
            return self._cache[cache_key]

        format_path = os.path.join(self.prompts_dir, "keyframes_format.xml")

        if not os.path.exists(format_path):
            return ""

        with open(format_path, "r", encoding="utf-8") as f:
            format_spec = f.read()

        self._cache[cache_key] = format_spec
        return format_spec

    def load_prompt(
        self,
        video_type: str,
        with_keyframes: bool = False,
        custom_keyframes_criteria: Optional[str] = None
    ) -> str:
        """
        Load prompt template for specified video type.
        Optionally append keyframes extraction instructions.

        Args:
            video_type: Type of video analysis
            with_keyframes: Whether to include keyframes instructions
            custom_keyframes_criteria: Custom keyframes criteria (if None, uses default)
        """
        if video_type not in VIDEO_TYPES:
            raise ValueError(f"Unknown video type: {video_type}")

        type_info = VIDEO_TYPES[video_type]
        prompt_file = type_info["prompt_file"]

        # Don't cache when using custom keyframes criteria
        if custom_keyframes_criteria is not None:
            use_cache = False
        else:
            use_cache = True
            cache_key = f"{prompt_file}_{with_keyframes}"
            if cache_key in self._cache:
                return self._cache[cache_key]

        prompt_path = os.path.join(self.prompts_dir, prompt_file)

        if not os.path.exists(prompt_path):
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt = f.read()

        # Append keyframes criteria if requested (without format - format is added separately)
        if with_keyframes:
            # Use custom criteria or default
            if custom_keyframes_criteria is not None:
                criteria = custom_keyframes_criteria
            else:
                criteria = self.get_keyframes_criteria_default()

            if criteria:
                prompt += "\n\n" + criteria

        if use_cache:
            self._cache[cache_key] = prompt

        return prompt

    def load_combine_prompt(self) -> str:
        """Load prompt for combining chunk analyses."""
        cache_key = "combine_analysis"

        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt_path = os.path.join(self.prompts_dir, "combine_analysis_prompt.xml")

        if not os.path.exists(prompt_path):
            # Default combine prompt if file doesn't exist
            return """You are an expert video analyst. You have received multiple partial analyses
of different chunks of the same video. Your task is to combine them into a single,
coherent, comprehensive analysis.

Rules:
1. Remove duplicate information
2. Maintain chronological order of events
3. Preserve all unique details from each chunk
4. Create a unified narrative
5. Adjust any relative timecodes to absolute video timecodes

Combine the following analyses into one comprehensive document:"""

        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt = f.read()

        self._cache[cache_key] = prompt
        return prompt

    def format_chunk_prompt(
        self,
        prompt: str,
        chunk_number: int,
        total_chunks: int,
        start_time_minutes: float,
        end_time_minutes: float,
        duration_minutes: float
    ) -> str:
        """Format prompt template with chunk information."""
        return prompt.format(
            chunk_number=chunk_number,
            total_chunks=total_chunks,
            start_time_minutes=round(start_time_minutes, 1),
            end_time_minutes=round(end_time_minutes, 1),
            duration_minutes=round(duration_minutes, 1)
        )

    def clear_cache(self):
        """Clear prompt cache."""
        self._cache.clear()


# Global prompt manager instance
prompt_manager = PromptManager()
