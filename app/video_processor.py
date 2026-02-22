"""
Video processing utilities using FFmpeg.
Handles video chunking and metadata extraction.
"""
import os
import subprocess
import json
import zipfile
import logging
import re
from typing import List, Dict, Optional
from pathlib import Path

from app.config import config_manager

logger = logging.getLogger(__name__)


class VideoProcessor:
    """Processes videos: extracts metadata, splits into chunks."""

    def __init__(self, temp_dir: Optional[str] = None):
        settings = config_manager.settings
        self.temp_dir = temp_dir or settings.temp_directory
        self.chunk_duration = settings.chunk_duration_minutes * 60  # seconds
        self.chunk_split_mode = (settings.chunk_split_mode or "fixed").lower()
        self.silence_window_seconds = max(0, int(settings.silence_window_seconds))
        self.silence_min_duration_seconds = max(0.1, float(settings.silence_min_duration_seconds))
        self.silence_noise_db = float(settings.silence_noise_db)
        os.makedirs(self.temp_dir, exist_ok=True)

    def get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to get video duration: {e}")

    def get_video_info(self, video_path: str) -> Dict:
        """Get comprehensive video information."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)

            video_stream = next(
                (s for s in data.get("streams", []) if s["codec_type"] == "video"),
                {}
            )

            return {
                "duration": float(data["format"].get("duration", 0)),
                "size_bytes": int(data["format"].get("size", 0)),
                "format": data["format"].get("format_name", "unknown"),
                "width": video_stream.get("width", 0),
                "height": video_stream.get("height", 0),
                "codec": video_stream.get("codec_name", "unknown"),
                "fps": self._parse_fps(video_stream.get("r_frame_rate", "0/1")),
            }
        except Exception as e:
            raise RuntimeError(f"Failed to get video info: {e}")

    def _parse_fps(self, fps_string: str) -> float:
        """Parse FPS from ffprobe format (e.g., '30000/1001')."""
        try:
            if "/" in fps_string:
                num, den = fps_string.split("/")
                return round(float(num) / float(den), 2)
            return float(fps_string)
        except (ValueError, ZeroDivisionError):
            return 0.0

    def split_video(self, video_path: str, job_id: str) -> List[Dict]:
        """
        Split video into chunks if longer than chunk_duration.
        Returns list of chunk info dictionaries.
        """
        duration = self.get_video_duration(video_path)
        video_name = Path(video_path).stem

        # If video is short enough, no need to split
        if duration <= self.chunk_duration:
            return [{
                "path": video_path,
                "chunk_number": 1,
                "total_chunks": 1,
                "start_time": 0,
                "end_time": duration,
                "duration": duration,
                "is_original": True
            }]

        # Calculate chunk ranges using configured split mode.
        if self.chunk_split_mode == "silence_aware":
            try:
                ranges = self._build_silence_aware_ranges(video_path, duration)
            except Exception as e:
                logger.warning(
                    "Silence-aware split failed, falling back to fixed mode: %s",
                    str(e)
                )
                ranges = self._build_fixed_ranges(duration)
        else:
            ranges = self._build_fixed_ranges(duration)

        num_chunks = len(ranges)

        chunks = []
        job_temp_dir = os.path.join(self.temp_dir, job_id)
        os.makedirs(job_temp_dir, exist_ok=True)

        for i, (start_time, end_time) in enumerate(ranges):
            chunk_duration = end_time - start_time

            chunk_filename = f"{video_name}_chunk_{i+1:03d}.mp4"
            chunk_path = os.path.join(job_temp_dir, chunk_filename)

            # Split using ffmpeg with stream copy (fast, no re-encoding)
            cmd = [
                "ffmpeg",
                "-y",  # Overwrite output
                "-ss", str(start_time),  # Start time
                "-i", video_path,
                "-t", str(chunk_duration),  # Duration
                "-c", "copy",  # Copy streams without re-encoding
                "-avoid_negative_ts", "make_zero",
                chunk_path
            ]

            try:
                subprocess.run(cmd, capture_output=True, check=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to split video at chunk {i+1}: {e.stderr.decode()}")

            chunks.append({
                "path": chunk_path,
                "chunk_number": i + 1,
                "total_chunks": num_chunks,
                "start_time": start_time,
                "end_time": end_time,
                "duration": chunk_duration,
                "is_original": False
            })

        return chunks

    def _build_fixed_ranges(self, duration: float) -> List[tuple]:
        """Build chunk ranges using fixed-size segmentation."""
        ranges = []
        current_start = 0.0

        while current_start < duration:
            current_end = min(current_start + self.chunk_duration, duration)
            ranges.append((current_start, current_end))
            current_start = current_end

        return ranges

    def _build_silence_aware_ranges(self, video_path: str, duration: float) -> List[tuple]:
        """
        Build chunk ranges using fixed target boundaries adjusted to nearby silence.
        Boundaries are shifted within +/- silence_window_seconds when possible.
        """
        fixed_ranges = self._build_fixed_ranges(duration)
        if len(fixed_ranges) <= 1:
            return fixed_ranges

        min_chunk_duration = self._get_min_chunk_duration_seconds()

        logger.debug(
            "Silence-aware split start | duration=%.2fs chunk_duration=%.2fs window=+/-%.2fs min_silence=%.2fs noise_db=%.2f min_chunk=%.2fs",
            duration,
            self.chunk_duration,
            float(self.silence_window_seconds),
            self.silence_min_duration_seconds,
            self.silence_noise_db,
            min_chunk_duration
        )

        target_boundaries = [r[1] for r in fixed_ranges[:-1]]
        silence_intervals = self._detect_silence_intervals(video_path, duration)
        adjusted_boundaries = []

        total_boundaries = len(target_boundaries)
        previous_boundary = 0.0
        for index, target in enumerate(target_boundaries, start=1):
            boundaries_left_after = total_boundaries - index

            min_boundary = previous_boundary + min_chunk_duration
            max_boundary = duration - 1.0
            if boundaries_left_after > 0:
                # Reserve at least min_chunk_duration for each future non-final chunk.
                max_boundary = min(max_boundary, duration - (min_chunk_duration * boundaries_left_after))

            decision = self._pick_boundary_near_silence(
                target=target,
                previous_boundary=previous_boundary,
                duration=duration,
                silence_intervals=silence_intervals,
                min_boundary=min_boundary,
                max_boundary=max_boundary
            )
            boundary = decision["boundary"]
            adjusted_boundaries.append(boundary)
            logger.debug(
                "Silence-aware boundary %d | target=%.2fs chosen=%.2fs shift=%+.2fs reason=%s allowed=[%.2f, %.2f] window=[%.2f, %.2f]%s",
                index,
                target,
                boundary,
                decision["shift_seconds"],
                decision["reason"],
                min_boundary,
                max_boundary,
                decision["window_start"],
                decision["window_end"],
                (
                    f" candidate_interval=[{decision['interval_start']:.2f}, {decision['interval_end']:.2f}]"
                    if decision["interval_start"] is not None and decision["interval_end"] is not None
                    else ""
                )
            )
            previous_boundary = boundary

        points = [0.0] + adjusted_boundaries + [duration]
        ranges = []
        for i in range(len(points) - 1):
            start_time = points[i]
            end_time = points[i + 1]
            if end_time - start_time > 0.5:
                ranges.append((start_time, end_time))

        if not ranges:
            return fixed_ranges

        logger.debug("Silence-aware split complete | chunk_count=%d", len(ranges))
        for idx, (start_time, end_time) in enumerate(ranges, start=1):
            logger.debug(
                "Silence-aware chunk %d | start=%.2fs end=%.2fs duration=%.2fs",
                idx,
                start_time,
                end_time,
                end_time - start_time
            )

        return ranges

    def _get_min_chunk_duration_seconds(self) -> float:
        """
        Minimum allowed chunk length in silence-aware mode.
        Keeps boundaries from collapsing into tiny intermediate chunks.
        """
        # Keep at least 30 seconds for normal long chunks, but for short chunk sizes
        # cap at half of chunk duration to stay feasible.
        return min(
            max(30.0, self.silence_min_duration_seconds * 2.0),
            max(10.0, self.chunk_duration * 0.5)
        )

    def _detect_silence_intervals(self, video_path: str, duration: float) -> List[Dict[str, float]]:
        """Detect silence intervals using ffmpeg silencedetect."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i", video_path,
            "-af", f"silencedetect=noise={self.silence_noise_db}dB:d={self.silence_min_duration_seconds}",
            "-f", "null",
            "-"
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as e:
            raise RuntimeError(f"Failed to run silencedetect: {e}")

        if result.returncode != 0 and not result.stderr:
            raise RuntimeError("silencedetect returned non-zero exit code with empty stderr")

        silence_start_pattern = re.compile(r"silence_start:\s*([0-9]*\.?[0-9]+)")
        silence_end_pattern = re.compile(
            r"silence_end:\s*([0-9]*\.?[0-9]+)\s*\|\s*silence_duration:\s*([0-9]*\.?[0-9]+)"
        )

        intervals: List[Dict[str, float]] = []
        current_start: Optional[float] = None

        for line in result.stderr.splitlines():
            start_match = silence_start_pattern.search(line)
            if start_match:
                current_start = float(start_match.group(1))
                continue

            end_match = silence_end_pattern.search(line)
            if end_match:
                end_value = float(end_match.group(1))
                duration_value = float(end_match.group(2))
                if current_start is None:
                    current_start = max(0.0, end_value - duration_value)

                interval_duration = end_value - current_start
                if interval_duration >= self.silence_min_duration_seconds:
                    intervals.append({
                        "start": max(0.0, current_start),
                        "end": min(duration, end_value),
                        "duration": interval_duration
                    })
                current_start = None

        if current_start is not None:
            # Silence continues until video end.
            interval_duration = duration - current_start
            if interval_duration >= self.silence_min_duration_seconds:
                intervals.append({
                    "start": max(0.0, current_start),
                    "end": duration,
                    "duration": interval_duration
                })

        logger.debug("Silence intervals detected | count=%d", len(intervals))
        for index, interval in enumerate(intervals[:20], start=1):
            logger.debug(
                "Silence interval %d | start=%.2fs end=%.2fs duration=%.2fs",
                index,
                interval["start"],
                interval["end"],
                interval["duration"]
            )
        if len(intervals) > 20:
            logger.debug("Silence interval output truncated | remaining=%d", len(intervals) - 20)

        return intervals

    def _pick_boundary_near_silence(
        self,
        target: float,
        previous_boundary: float,
        duration: float,
        silence_intervals: List[Dict[str, float]],
        min_boundary: float,
        max_boundary: float
    ) -> Dict[str, Optional[float]]:
        """Pick nearest silence midpoint around target, otherwise return target."""
        # Clamp boundary corridor by safety constraints first.
        safe_min = max(previous_boundary + 1.0, min_boundary)
        safe_max = min(duration - 1.0, max_boundary)

        if safe_min >= safe_max:
            boundary = min(max(target, safe_min), safe_max if safe_max >= safe_min else safe_min)
            return {
                "boundary": boundary,
                "reason": "fixed_fallback_constrained",
                "shift_seconds": boundary - target,
                "window_start": safe_min,
                "window_end": safe_max,
                "interval_start": None,
                "interval_end": None,
            }

        window_start = max(safe_min, target - self.silence_window_seconds)
        window_end = min(safe_max, target + self.silence_window_seconds)

        if window_start >= window_end:
            boundary = min(max(target, safe_min), safe_max)
            return {
                "boundary": boundary,
                "reason": "fixed_fallback_window",
                "shift_seconds": boundary - target,
                "window_start": window_start,
                "window_end": window_end,
                "interval_start": None,
                "interval_end": None,
            }

        best_candidate = None
        best_distance = None
        best_interval_start = None
        best_interval_end = None

        for interval in silence_intervals:
            interval_start = interval["start"]
            interval_end = interval["end"]

            # Ignore intervals that don't overlap the search window.
            if interval_end < window_start or interval_start > window_end:
                continue

            clamped_start = max(interval_start, window_start)
            clamped_end = min(interval_end, window_end)
            if clamped_end <= clamped_start:
                continue

            candidate = (clamped_start + clamped_end) / 2.0
            distance = abs(candidate - target)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_candidate = candidate
                best_interval_start = clamped_start
                best_interval_end = clamped_end

        if best_candidate is None:
            boundary = min(max(target, safe_min), safe_max)
            reason = "fixed_fallback"
        else:
            boundary = best_candidate
            reason = "silence"

        boundary = max(safe_min, boundary)
        boundary = min(safe_max, boundary)
        return {
            "boundary": boundary,
            "reason": reason,
            "shift_seconds": boundary - target,
            "window_start": window_start,
            "window_end": window_end,
            "interval_start": best_interval_start,
            "interval_end": best_interval_end,
        }

    def extract_frame(self, video_path: str, timecode: str, output_path: str) -> bool:
        """Extract a single frame at specified timecode."""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", timecode,
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",  # High quality JPEG
            output_path
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def extract_keyframes_to_zip(
        self,
        video_path: str,
        keyframes: List[Dict],
        output_zip_path: str,
        job_id: str
    ) -> Dict:
        """
        Extract multiple keyframes and pack them into a ZIP archive.

        Args:
            video_path: Path to the video file
            keyframes: List of dicts with 'timecode', 'title', 'frame_description'
            output_zip_path: Path for the output ZIP file
            job_id: Job ID for temporary directory

        Returns:
            Dict with extraction results
        """
        job_temp_dir = os.path.join(self.temp_dir, f"{job_id}_keyframes")
        os.makedirs(job_temp_dir, exist_ok=True)

        extracted = []
        failed = []

        try:
            for i, kf in enumerate(keyframes):
                timecode = kf.get("timecode", "00:00:00")
                title = kf.get("title", f"frame_{i+1}")

                # Sanitize title for filename
                safe_title = "".join(c if c.isalnum() or c in "- _" else "_" for c in title)
                safe_title = safe_title[:50]  # Limit length

                # Create filename: 001_00-00-00_title.jpg
                timecode_safe = timecode.replace(":", "-")
                frame_filename = f"{i+1:03d}_{timecode_safe}_{safe_title}.jpg"
                frame_path = os.path.join(job_temp_dir, frame_filename)

                if self.extract_frame(video_path, timecode, frame_path):
                    extracted.append({
                        "filename": frame_filename,
                        "timecode": timecode,
                        "title": title
                    })
                else:
                    failed.append({
                        "timecode": timecode,
                        "title": title,
                        "error": "Failed to extract frame"
                    })

            # Create ZIP archive
            with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for item in extracted:
                    frame_path = os.path.join(job_temp_dir, item["filename"])
                    zf.write(frame_path, item["filename"])

            return {
                "success": True,
                "zip_path": output_zip_path,
                "extracted_count": len(extracted),
                "failed_count": len(failed),
                "extracted": extracted,
                "failed": failed
            }

        finally:
            # Cleanup temp frames
            import shutil
            if os.path.exists(job_temp_dir):
                shutil.rmtree(job_temp_dir)

    def cleanup_job(self, job_id: str):
        """Clean up temporary files for a job."""
        import shutil
        job_temp_dir = os.path.join(self.temp_dir, job_id)
        if os.path.exists(job_temp_dir):
            shutil.rmtree(job_temp_dir)


def seconds_to_timecode(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def timecode_to_seconds(timecode: str) -> float:
    """Convert HH:MM:SS or MM:SS format to seconds."""
    parts = timecode.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        return float(timecode)
