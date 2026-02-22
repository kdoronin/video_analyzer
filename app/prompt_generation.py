"""
Prompt generation helpers.
Builds model-aware prompt templates and extracts XML-like structured text.
"""
import html
import re
from typing import Optional
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from app.prompts import prompt_manager


class PromptGenerationError(Exception):
    """Error while generating or parsing generated prompt templates."""
    pass


class PromptGenerationService:
    """Service for generating production-style XML prompts from user descriptions."""

    ANALYSIS_ROOT = "prompt"
    KEYFRAMES_ROOT = "keyframes_criteria"
    KEYFRAMES_ROOT_ALIASES = {
        "keyframes_criteria",
        "keyframe_criteria",
        "keyframes",
        "keyframes-criteria",
        "criteria",
        "keyframesCriteria",
    }
    ANALYSIS_ROOT_ALIASES = {
        "prompt",
        "analysis_prompt",
        "video_prompt",
        "prompt_template",
    }

    def build_generation_instruction(
        self,
        target: str,
        description: str,
        provider: str,
        model: str,
        video_type: Optional[str] = None
    ) -> str:
        """Build instruction for model-aware prompt generation."""
        user_description = (description or "").strip()
        if not user_description:
            raise PromptGenerationError("Description cannot be empty")

        if target == "analysis":
            return self._build_analysis_instruction(
                user_description=user_description,
                provider=provider,
                model=model,
                video_type=video_type
            )

        if target == "keyframes":
            return self._build_keyframes_instruction(
                user_description=user_description,
                provider=provider,
                model=model
            )

        raise PromptGenerationError(f"Unsupported target: {target}")

    def extract_xml(self, raw_response: str, target: str, strict: bool = False) -> str:
        """Extract an XML-like block from model output."""
        root_tag = self.ANALYSIS_ROOT if target == "analysis" else self.KEYFRAMES_ROOT
        response_text = str(raw_response or "").strip()
        if not response_text:
            raise PromptGenerationError("Model returned an empty response")

        # Some models return escaped XML (&lt;tag&gt;), decode before parsing.
        response_text = html.unescape(response_text)

        # First, try to parse XML directly from the whole response.
        xml_block = self._find_xml_block(response_text, root_tag)
        if not xml_block:
            xml_block = self._find_any_xml_block(response_text)

        # If not found, try markdown code blocks as fallback.
        if not xml_block:
            for fenced in re.finditer(r"```(?:xml)?\s*([\s\S]*?)\s*```", response_text, re.IGNORECASE):
                fenced_text = fenced.group(1).strip()
                xml_block = self._find_xml_block(fenced_text, root_tag)
                if not xml_block:
                    xml_block = self._find_any_xml_block(fenced_text)
                if xml_block:
                    break

        if not xml_block:
            raise PromptGenerationError(
                f"Could not find <{root_tag}> XML block in model response"
            )

        xml_block = self._normalize_root(xml_block, target)

        xml_block = xml_block.strip()
        if not xml_block.startswith("<?xml"):
            xml_block = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_block

        if strict:
            self._validate_xml(xml_block, target)
        else:
            self._validate_structure_loose(xml_block, target)

        return xml_block

    def _find_xml_block(self, text: str, root_tag: str) -> Optional[str]:
        xml_declaration_pattern = re.compile(
            rf"(<\?xml[\s\S]*?<\s*{root_tag}\b[\s\S]*?</\s*{root_tag}\s*>)",
            re.IGNORECASE
        )
        match = xml_declaration_pattern.search(text)
        if match:
            return match.group(1)

        root_pattern = re.compile(
            rf"(<\s*{root_tag}\b[\s\S]*?</\s*{root_tag}\s*>)",
            re.IGNORECASE
        )
        match = root_pattern.search(text)
        if match:
            return match.group(1)

        return None

    def _find_any_xml_block(self, text: str) -> Optional[str]:
        xml_with_decl_pattern = re.compile(
            r"(<\?xml[\s\S]*?\?>\s*<([A-Za-z_][\w:.\-]*)\b[\s\S]*?</\2\s*>)",
            re.IGNORECASE
        )
        match = xml_with_decl_pattern.search(text)
        if match:
            return match.group(1)

        xml_no_decl_pattern = re.compile(
            r"(<([A-Za-z_][\w:.\-]*)\b[\s\S]*?</\2\s*>)",
            re.IGNORECASE
        )
        match = xml_no_decl_pattern.search(text)
        if match:
            return match.group(1)

        return None

    def _normalize_root(self, xml_block: str, target: str) -> str:
        """Normalize accepted alias roots to strict expected roots."""
        expected_root = self.ANALYSIS_ROOT if target == "analysis" else self.KEYFRAMES_ROOT
        aliases = self.ANALYSIS_ROOT_ALIASES if target == "analysis" else self.KEYFRAMES_ROOT_ALIASES

        try:
            root = ET.fromstring(xml_block)
        except ET.ParseError:
            # Validation stage will raise a precise parse error.
            return xml_block

        if root.tag == expected_root:
            return xml_block

        if root.tag in aliases:
            root.tag = expected_root
            return ET.tostring(root, encoding="unicode")

        return xml_block

    def build_repair_instruction(self, raw_response: str, target: str) -> str:
        """Build a strict repair instruction to convert malformed output to valid XML."""
        root_tag = self.ANALYSIS_ROOT if target == "analysis" else self.KEYFRAMES_ROOT
        truncated_raw = str(raw_response or "").strip()
        if len(truncated_raw) > 12000:
            truncated_raw = truncated_raw[:12000]

        if target == "analysis":
            required_structure = """
- <type>
- <task>
- <context><chunk_info><chunk_number>{chunk_number}</chunk_number><total_chunks>{total_chunks}</total_chunks><time_range><start_minutes>{start_time_minutes}</start_minutes><end_minutes>{end_time_minutes}</end_minutes><duration_minutes>{duration_minutes}</duration_minutes></time_range></chunk_info></context>
- <focus> with <item>
- <requirements> with <language>, <detail_level>, <factuality>, <timecodes>, <confidence>
- <output> with <section>
"""
        else:
            required_structure = """
- <json_output>
- <no_limit>
- <cadence>
- <key_frame_criteria> with <note> and multiple <item>
- <recall_bias>
"""

        return f"""Convert the following model output into valid XML only.

Rules:
1) Return ONLY XML, no markdown and no comments.
2) Root tag must be exactly <{root_tag}>.
3) Keep Russian language for all instruction text.
4) Preserve intent and details from the source as much as possible.
5) Ensure XML is parseable and complete.
6) Required structure:
{required_structure}

Source output:
\"\"\"
{truncated_raw}
\"\"\"
"""

    def build_deterministic_fallback(
        self,
        target: str,
        description: str,
        provider: str,
        model: str,
        video_type: Optional[str] = None
    ) -> str:
        """Build a guaranteed-valid XML fallback when model output is not parseable."""
        safe_description = escape((description or "").strip())
        safe_provider = escape((provider or "").strip())
        safe_model = escape((model or "").strip())
        safe_video_type = escape((video_type or "general").strip() or "general")

        if target == "keyframes":
            return f"""<?xml version="1.0" encoding="UTF-8"?>
<keyframes_criteria>
    <json_output>Если key frames присутствуют, верни их строго в JSON внутри блока ```json ... ``` без лишнего текста.</json_output>
    <no_limit>Не ограничивай количество ключевых кадров: фиксируй все значимые моменты для понимания видео.</no_limit>
    <cadence>Ориентир: в среднем один ключевой кадр каждые 10-20 секунд, с адаптацией к динамике сцены.</cadence>
    <key_frame_criteria>
        <note>Критерии сформированы автоматически для provider={safe_provider}, model={safe_model}. Исходный кейс: {safe_description}</note>
        <item>Смена сцены, ракурса, локации или композиции кадра.</item>
        <item>Появление/исчезновение важного объекта, текста, UI-элемента или метрики.</item>
        <item>Критические действия в интерфейсе: клик, переключение, подтверждение, ошибка, уведомление.</item>
        <item>Моменты с ключевыми тезисами, числами, сроками, решениями или призывами к действию.</item>
        <item>События, прямо описанные в кейсе пользователя: {safe_description}</item>
    </key_frame_criteria>
    <recall_bias>При сомнении включай кадр в выборку, чтобы не терять важный контекст.</recall_bias>
</keyframes_criteria>"""

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<prompt>
    <type>{safe_video_type}</type>
    <task>Проанализируй видеофрагмент по кейсу пользователя и выдай структурированный результат.</task>
    <context>
        <chunk_info>
            <chunk_number>{{chunk_number}}</chunk_number>
            <total_chunks>{{total_chunks}}</total_chunks>
            <time_range>
                <start_minutes>{{start_time_minutes}}</start_minutes>
                <end_minutes>{{end_time_minutes}}</end_minutes>
                <duration_minutes>{{duration_minutes}}</duration_minutes>
            </time_range>
        </chunk_info>
    </context>
    <focus>
        <item>Цель и контекст кейса: {safe_description}</item>
        <item>Ключевые события, сцены, действия, объекты, текст на экране и UI-переходы.</item>
        <item>Ключевые тезисы/сигналы и их связь с задачей анализа.</item>
    </focus>
    <requirements>
        <language>ОБЯЗАТЕЛЬНО отвечай на русском языке.</language>
        <detail_level>Дай детальный, практичный разбор с четкой структурой.</detail_level>
        <factuality>Не выдумывай факты, явно помечай неразборчивые или неизвестные моменты.</factuality>
        <timecodes>Выделяй ключевые моменты с таймкодами в формате чч:мм:сс.</timecodes>
        <confidence>Для ключевых выводов указывай уверенность: низкая/средняя/высокая.</confidence>
    </requirements>
    <output>
        <section>Краткое резюме</section>
        <section>Таймлайн ключевых событий с таймкодами</section>
        <section>Сущности и объекты</section>
        <section>Ключевые кадры, влияющие на понимание и решения</section>
        <section>Открытые вопросы и неопределенности</section>
    </output>
</prompt>"""

    def _validate_xml(self, xml_text: str, target: str) -> None:
        """Validate XML parseability and required structure."""
        root_tag = self.ANALYSIS_ROOT if target == "analysis" else self.KEYFRAMES_ROOT

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise PromptGenerationError(f"Generated XML is not valid: {e}")

        if root.tag != root_tag:
            raise PromptGenerationError(f"Generated XML root must be <{root_tag}>")

        if target == "analysis":
            self._validate_analysis_xml(root)
        else:
            self._validate_keyframes_xml(root)

    def _validate_analysis_xml(self, root: ET.Element) -> None:
        required_paths = [
            "type",
            "task",
            "context/chunk_info/chunk_number",
            "context/chunk_info/total_chunks",
            "context/chunk_info/time_range/start_minutes",
            "context/chunk_info/time_range/end_minutes",
            "context/chunk_info/time_range/duration_minutes",
            "focus/item",
            "requirements/language",
            "requirements/detail_level",
            "requirements/factuality",
            "requirements/timecodes",
            "requirements/confidence",
            "output/section",
        ]

        for path in required_paths:
            if root.find(path) is None:
                raise PromptGenerationError(f"Generated analysis XML is missing required path: {path}")

        expected_placeholders = {
            "context/chunk_info/chunk_number": "{chunk_number}",
            "context/chunk_info/total_chunks": "{total_chunks}",
            "context/chunk_info/time_range/start_minutes": "{start_time_minutes}",
            "context/chunk_info/time_range/end_minutes": "{end_time_minutes}",
            "context/chunk_info/time_range/duration_minutes": "{duration_minutes}",
        }

        for path, expected_value in expected_placeholders.items():
            node = root.find(path)
            node_value = (node.text or "").strip() if node is not None else ""
            if node_value != expected_value:
                raise PromptGenerationError(
                    f"Generated analysis XML must keep placeholder {expected_value} at {path}"
                )

    def _validate_keyframes_xml(self, root: ET.Element) -> None:
        required_paths = [
            "json_output",
            "no_limit",
            "cadence",
            "key_frame_criteria/note",
            "key_frame_criteria/item",
            "recall_bias",
        ]

        for path in required_paths:
            if root.find(path) is None:
                raise PromptGenerationError(f"Generated keyframes XML is missing required path: {path}")

    def _validate_structure_loose(self, xml_text: str, target: str) -> None:
        """Validate only high-level structure by tag presence, without strict XML parsing."""
        expected_root = self.ANALYSIS_ROOT if target == "analysis" else self.KEYFRAMES_ROOT
        root_aliases = self.ANALYSIS_ROOT_ALIASES if target == "analysis" else self.KEYFRAMES_ROOT_ALIASES

        root_match = re.search(r"<\s*([A-Za-z_][\w:.\-]*)\b", xml_text)
        if not root_match:
            raise PromptGenerationError("Generated prompt has no root tag")

        root_tag = root_match.group(1)
        if root_tag != expected_root and root_tag not in root_aliases:
            raise PromptGenerationError(f"Generated prompt has unexpected root tag: <{root_tag}>")

        if target == "analysis":
            required_tags = [
                "type",
                "task",
                "context",
                "chunk_info",
                "focus",
                "requirements",
                "output",
            ]
        else:
            required_tags = [
                "json_output",
                "no_limit",
                "cadence",
                "key_frame_criteria",
                "recall_bias",
            ]

        for tag in required_tags:
            if not re.search(rf"<\s*{re.escape(tag)}\b", xml_text, re.IGNORECASE):
                raise PromptGenerationError(f"Generated prompt is missing required tag: <{tag}>")

    def _build_analysis_instruction(
        self,
        user_description: str,
        provider: str,
        model: str,
        video_type: Optional[str]
    ) -> str:
        reference_type = video_type if video_type and video_type != "custom" else "general"
        try:
            reference_prompt = prompt_manager.load_prompt(reference_type, with_keyframes=False)
        except Exception:
            reference_prompt = prompt_manager.load_prompt("general", with_keyframes=False)

        return f"""You are an expert system-prompt engineer for multimodal video analysis.

Your task: generate one production-ready XML prompt template for VIDEO ANALYSIS.
The generated prompt must be specialized for this runtime stack:
- Provider: {provider}
- Model: {model}
- Selected video type: {reference_type}

User case description:
\"\"\"
{user_description}
\"\"\"

Reference style (quality and depth baseline from existing repository):
\"\"\"
{reference_prompt}
\"\"\"

Hard requirements:
1) Return ONLY XML, no markdown, no explanations.
2) Root tag must be exactly <prompt>.
3) Keep this structure:
   - <type>
   - <task>
   - <context><chunk_info><chunk_number>{{chunk_number}}</chunk_number><total_chunks>{{total_chunks}}</total_chunks><time_range><start_minutes>{{start_time_minutes}}</start_minutes><end_minutes>{{end_time_minutes}}</end_minutes><duration_minutes>{{duration_minutes}}</duration_minutes></time_range></chunk_info></context>
   - <focus> with multiple <item>
   - <requirements> with at least: <language>, <detail_level>, <factuality>, <timecodes>, <confidence>
   - <output> with multiple <section>
4) Instructions inside XML must be in Russian.
5) No placeholders except these exact chunk placeholders: {{chunk_number}}, {{total_chunks}}, {{start_time_minutes}}, {{end_time_minutes}}, {{duration_minutes}}.
6) Make it strict, practical, and model-aware (wording/detail adapted to the specified model).
7) Ensure the result is valid XML and starts with XML declaration.
"""

    def _build_keyframes_instruction(
        self,
        user_description: str,
        provider: str,
        model: str
    ) -> str:
        reference_criteria = prompt_manager.get_keyframes_criteria_default()

        return f"""You are an expert system-prompt engineer for multimodal keyframe extraction.

Your task: generate one production-ready XML criteria template for KEYFRAMES.
The generated criteria must be specialized for this runtime stack:
- Provider: {provider}
- Model: {model}

User definition of keyframes:
\"\"\"
{user_description}
\"\"\"

Reference style (quality and depth baseline from existing repository):
\"\"\"
{reference_criteria}
\"\"\"

Hard requirements:
1) Return ONLY XML, no markdown, no explanations.
2) Root tag must be exactly <keyframes_criteria>.
3) Keep this structure:
   - <json_output>
   - <no_limit>
   - <cadence>
   - <key_frame_criteria> containing <note> and multiple <item>
   - <recall_bias>
4) Instructions inside XML must be in Russian.
5) Do not include JSON schema block or <keyframes_format>; only criteria XML.
6) Make criteria specific, strict, and adapted to model capabilities.
7) Ensure the result is valid XML and starts with XML declaration.
"""


prompt_generation_service = PromptGenerationService()
