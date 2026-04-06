"""
Helpers for parsing structured JSON artifacts from model responses.
"""
import json
import re
from typing import Dict, List, Optional, Sequence


class StructuredOutputParser:
    """Parses JSON blocks for keyframes and clip segments from model output."""

    _FENCED_JSON_RE = re.compile(r"```json\s*([\s\S]*?)\s*```", re.IGNORECASE)

    def parse_keyframes(self, text: str, chunk_info: Optional[Dict] = None) -> List[Dict]:
        """Extract normalized keyframe entries from model output."""
        items = self._extract_items(text, root_key="key_frames")
        normalized: List[Dict] = []
        for item in items:
            timecode = str(item.get("timecode", "")).strip()
            title = str(item.get("title", "")).strip()
            if not timecode or not title:
                continue
            normalized.append({
                "timecode": timecode,
                "title": title,
                "frame_description": str(item.get("frame_description", "")).strip() or None,
            })
        normalized = self._normalize_keyframes_to_absolute(normalized, chunk_info)
        return self._dedupe(normalized, keys=("timecode", "title"))

    def parse_clip_segments(self, text: str, chunk_info: Optional[Dict] = None) -> List[Dict]:
        """Extract normalized clip segments from model output."""
        items = self._extract_items(text, root_key="clip_segments")
        normalized: List[Dict] = []
        for index, item in enumerate(items, start=1):
            start_timecode = self._pick_first_string(
                item,
                ("start_timecode", "start", "start_tc", "from_timecode"),
            )
            end_timecode = self._pick_first_string(
                item,
                ("end_timecode", "end", "end_tc", "to_timecode"),
            )
            if not start_timecode or not end_timecode:
                continue

            title = self._pick_first_string(item, ("title", "clip_title", "name")) or f"Clip {index}"
            description = self._pick_first_string(
                item,
                ("description", "clip_description", "summary"),
            )
            selection_reason = self._pick_first_string(
                item,
                ("selection_reason", "reason", "why"),
            )
            normalized.append({
                "start_timecode": start_timecode,
                "end_timecode": end_timecode,
                "title": title,
                "description": description,
                "selection_reason": selection_reason,
            })

        normalized = self._normalize_clips_to_absolute(normalized, chunk_info)
        return self._dedupe(
            normalized,
            keys=("start_timecode", "end_timecode", "title"),
        )

    def _extract_items(self, text: str, root_key: str) -> List[Dict]:
        for candidate in self._extract_json_candidates(text):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            items = parsed.get(root_key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]

        raw_match = re.search(
            rf"\{{\s*\"{re.escape(root_key)}\"\s*:\s*\[[\s\S]*?\]\s*\}}",
            text or "",
        )
        if raw_match:
            try:
                parsed = json.loads(raw_match.group(0))
            except json.JSONDecodeError:
                return []
            items = parsed.get(root_key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]

        return []

    def _extract_json_candidates(self, text: str) -> List[str]:
        candidates = [match.group(1).strip() for match in self._FENCED_JSON_RE.finditer(text or "")]
        stripped = (text or "").strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)
        return candidates

    def _pick_first_string(self, item: Dict, keys: Sequence[str]) -> Optional[str]:
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            string_value = str(value).strip()
            if string_value:
                return string_value
        return None

    def _dedupe(self, items: List[Dict], keys: Sequence[str]) -> List[Dict]:
        seen = set()
        unique: List[Dict] = []
        for item in items:
            signature = tuple(item.get(key) for key in keys)
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(item)
        return unique

    def _normalize_keyframes_to_absolute(self, items: List[Dict], chunk_info: Optional[Dict]) -> List[Dict]:
        """Shift chunk-relative keyframe timecodes into absolute video coordinates when needed."""
        if not chunk_info or chunk_info.get("is_original", False):
            return items

        chunk_start = float(chunk_info.get("start_time", 0) or 0)
        chunk_duration = float(chunk_info.get("duration", 0) or 0)
        if chunk_start <= 0 or chunk_duration <= 0:
            return items

        normalized = []
        for item in items:
            seconds = self._timecode_to_seconds(item.get("timecode"))
            if seconds is None:
                normalized.append(item)
                continue

            if self._looks_relative_to_chunk(seconds, chunk_start, chunk_duration):
                patched = dict(item)
                patched["timecode"] = self._seconds_to_timecode(seconds + chunk_start)
                normalized.append(patched)
            else:
                normalized.append(item)

        return normalized

    def _normalize_clips_to_absolute(self, items: List[Dict], chunk_info: Optional[Dict]) -> List[Dict]:
        """Shift chunk-relative clip segments into absolute video coordinates when needed."""
        if not chunk_info or chunk_info.get("is_original", False):
            return items

        chunk_start = float(chunk_info.get("start_time", 0) or 0)
        chunk_duration = float(chunk_info.get("duration", 0) or 0)
        if chunk_start <= 0 or chunk_duration <= 0:
            return items

        normalized = []
        for item in items:
            start_seconds = self._timecode_to_seconds(item.get("start_timecode"))
            end_seconds = self._timecode_to_seconds(item.get("end_timecode"))
            if start_seconds is None or end_seconds is None:
                normalized.append(item)
                continue

            if self._looks_relative_interval(start_seconds, end_seconds, chunk_start, chunk_duration):
                patched = dict(item)
                patched["start_timecode"] = self._seconds_to_timecode(start_seconds + chunk_start)
                patched["end_timecode"] = self._seconds_to_timecode(end_seconds + chunk_start)
                normalized.append(patched)
            else:
                normalized.append(item)

        return normalized

    def _looks_relative_to_chunk(self, seconds: float, chunk_start: float, chunk_duration: float) -> bool:
        """Heuristic: values that fit inside chunk duration but precede chunk start are relative."""
        return seconds <= chunk_duration + 1 and seconds < max(chunk_start - 1, 0)

    def _looks_relative_interval(
        self,
        start_seconds: float,
        end_seconds: float,
        chunk_start: float,
        chunk_duration: float
    ) -> bool:
        """Heuristic for chunk-relative clip segment intervals."""
        return (
            start_seconds <= chunk_duration + 1
            and end_seconds <= chunk_duration + 1
            and start_seconds < max(chunk_start - 1, 0)
        )

    def _timecode_to_seconds(self, value: Optional[str]) -> Optional[float]:
        """Convert HH:MM:SS or MM:SS to seconds."""
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None

        try:
            parts = text.split(":")
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            if len(parts) == 2:
                minutes, seconds = parts
                return int(minutes) * 60 + float(seconds)
            return float(text)
        except (TypeError, ValueError):
            return None

    def _seconds_to_timecode(self, seconds: float) -> str:
        """Convert seconds to HH:MM:SS format."""
        total_seconds = max(0, int(seconds))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"


structured_output_parser = StructuredOutputParser()
