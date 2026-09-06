"""Evidence-first Resume Studio application services."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bosshunter.ai.credentials import AIRequestError, call_anthropic_text
from bosshunter.resume_builder.documents import ResumeUploadError, prepare_resume_content, safe_resume_filename
from bosshunter.resume_builder.store import (
	clear_resume_studio_records,
	create_profile_version,
	create_source,
	create_version,
	delete_source_records,
	get_profile_version,
	get_source,
	get_source_by_hash,
	get_version,
	list_clarifications,
	list_facts,
	list_profile_versions,
	list_sources,
	list_versions,
	mark_profile_version_active,
	mark_version_active,
	replace_fact_candidates,
	replace_open_clarifications,
	set_source_classification,
	set_source_status,
	source_version_references,
)

MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_CHARS = 120_000
DEFAULT_CHUNK_CHARS = 12_000
STAR_BATCH_CHARS = 12_000
RESUME_BATCH_CHARS = 30_000
MAX_STAR_BATCHES = 4
MAX_STAR_STORIES_PER_BATCH = 6
MAX_PROFILE_QUESTIONS = 5
MAX_TARGET_RESUME_PROJECTS = 4
FACT_CATEGORIES = {
	"基本信息",
	"个人优势",
	"工作经历",
	"项目经历",
	"技术栈",
	"量化成果",
	"作品链接",
	"教育经历",
	"证书",
	"其他",
}
SOURCE_KINDS = {"resume", "technical_document", "portfolio"}
SOURCE_KIND_CHOICES = SOURCE_KINDS | {"auto"}
SOURCE_KIND_LABELS = {
	"resume": "简历",
	"technical_document": "技术文档",
	"portfolio": "作品集",
}
CLASSIFICATION_MIN_CONFIDENCE = 0.65
RESUME_ENTITY_TYPES = {
	"identity",
	"contact",
	"experience",
	"project",
	"education",
	"skill",
	"certification",
	"award",
	"publication",
	"other",
}
RESUME_ENTITY_LABELS = {
	"identity": "基本信息",
	"contact": "联系方式",
	"experience": "工作经历",
	"project": "项目经历",
	"education": "教育经历",
	"skill": "专业能力",
	"certification": "证书",
	"award": "奖项",
	"publication": "公开成果",
	"other": "其他经历",
}
RESUME_FIELD_CATEGORIES = {
	"name": "基本信息",
	"headline": "基本信息",
	"email": "基本信息",
	"phone": "基本信息",
	"location": "基本信息",
	"url": "作品链接",
	"company": "工作经历",
	"position": "工作经历",
	"start_date": "工作经历",
	"end_date": "工作经历",
	"responsibility": "工作经历",
	"achievement": "量化成果",
	"project_name": "项目经历",
	"role": "项目经历",
	"technology": "技术栈",
	"school": "教育经历",
	"degree": "教育经历",
	"major": "教育经历",
	"certification": "证书",
	"award": "其他",
	"publication": "其他",
	"language": "其他",
	"summary": "个人优势",
	"other": "其他",
}
RESUME_FIELD_LABELS = {
	"name": "姓名",
	"headline": "当前头衔",
	"email": "邮箱",
	"phone": "电话",
	"location": "所在地",
	"url": "链接",
	"company": "公司",
	"position": "职位",
	"start_date": "开始时间",
	"end_date": "结束时间",
	"responsibility": "职责",
	"achievement": "成果",
	"project_name": "项目",
	"role": "角色",
	"technology": "技术",
	"school": "学校",
	"degree": "学历",
	"major": "专业",
	"certification": "证书",
	"award": "奖项",
	"publication": "公开成果",
	"language": "语言",
	"summary": "个人总结",
	"other": "其他信息",
}
RESUME_NARRATIVE_FIELDS = {"responsibility", "achievement", "summary", "other"}
STAR_COMPONENTS = ("situation", "task", "action", "result")
OWNERSHIP_LEVELS = {"unknown", "participated", "collaborated", "responsible", "led"}
PROFILE_STRONG_VERBS = ("主导", "牵头", "负责", "推动", "独立完成", "独立设计", "独立开发")
_TOKEN_PATTERNS = [
	re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![A-Za-z0-9.])"),
	re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"),
	re.compile(r"https?://[^\s)>）】]+", re.IGNORECASE),
	re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
	re.compile(r"(?<!\d)(?:19|20)\d{2}(?:[./年-](?:0?[1-9]|1[0-2]))?(?:[./月-](?:0?[1-9]|[12]\d|3[01]))?(?:日)?(?!\d)"),
	re.compile(
		r"(?<![\w.])\d+(?:\.\d+)?(?:\s*[-~至到]\s*\d+(?:\.\d+)?)?\s*"
		r"(?:%|％|年|个月|月|天|人|次|篇|万|亿|元|K|k|W|w|倍|\+)(?!\w)"
	),
]
_NAMED_TOKEN_PATTERN = re.compile(r"(?<![\w.+#/-])[A-Za-z][A-Za-z0-9.+#/_-]{1,}(?![\w.+#/-])")


class ResumeBuilderError(ValueError):
	"""A safe, user-facing Resume Studio failure."""


def _clean_whitespace(value: str) -> str:
	return re.sub(r"\s+", " ", value).strip()


def _structured_tokens(text: str) -> set[str]:
	return {
		_clean_whitespace(match.group(0)).casefold()
		for pattern in _TOKEN_PATTERNS
		for match in pattern.finditer(text)
	}


def _unsupported_tokens(candidate: str, evidence: str) -> list[str]:
	supported = _structured_tokens(evidence)
	unsupported = {token for token in _structured_tokens(candidate) if token not in supported}
	named_evidence = re.sub(
		r"(?<=[A-Za-z0-9.+#/_-])(?=[^\x00-\x7F])|(?<=[^\x00-\x7F])(?=[A-Za-z])",
		" ",
		evidence,
	)
	evidence_names = {
		match.group(0).casefold().strip("._/-")
		for match in _NAMED_TOKEN_PATTERN.finditer(named_evidence)
	}
	unsupported.update(
		match.group(0)
		for match in _NAMED_TOKEN_PATTERN.finditer(candidate)
		if match.group(0).casefold().strip("._/-") not in evidence_names
	)
	return sorted(unsupported)


def _json_payload(raw: str) -> dict:
	text = raw.strip()
	text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
	text = re.sub(r"\s*```$", "", text)
	start = text.find("{")
	if start < 0:
		raise ResumeBuilderError("AI 未返回可解析的结构化结果")
	try:
		payload, _ = json.JSONDecoder().raw_decode(text[start:])
	except json.JSONDecodeError as exc:
		raise ResumeBuilderError("AI 返回的结构化结果格式无效") from exc
	if not isinstance(payload, dict):
		raise ResumeBuilderError("AI 返回结果必须是 JSON 对象")
	return payload


def _request_json_payload(
	caller: Callable[..., str | None],
	prompt: str,
	config: dict,
	max_tokens: int,
	*,
	purpose: str,
	empty_message: str,
) -> dict:
	"""Retry one transient/invalid response without increasing normal-path calls."""
	current_prompt = prompt
	for attempt in range(2):
		try:
			raw = caller(current_prompt, config, max_tokens, purpose=purpose)
		except AIRequestError:
			if attempt == 0:
				continue
			raise
		if not raw:
			if attempt == 0:
				continue
			raise ResumeBuilderError(empty_message)
		try:
			return _json_payload(raw)
		except ResumeBuilderError:
			if attempt == 1:
				raise
			current_prompt = (
				f"{prompt}\n\n上一次响应不是完整有效的 JSON。请减少条目数量和文字长度，"
				"只返回一个完整 JSON 对象，不要 Markdown 代码块或额外说明。"
			)
	raise ResumeBuilderError(empty_message)


def _chunks(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
	paragraphs = re.split(r"\n\s*\n", text)
	chunks: list[str] = []
	current: list[str] = []
	current_length = 0
	for paragraph in paragraphs:
		paragraph = paragraph.strip()
		if not paragraph:
			continue
		if len(paragraph) > max_chars:
			if current:
				chunks.append("\n\n".join(current))
				current, current_length = [], 0
			chunks.extend(paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars))
			continue
		if current and current_length + len(paragraph) + 2 > max_chars:
			chunks.append("\n\n".join(current))
			current, current_length = [], 0
		current.append(paragraph)
		current_length += len(paragraph) + 2
	if current:
		chunks.append("\n\n".join(current))
	return chunks


def _section_chunks(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
	"""Split Markdown-like content without losing the heading that scopes a chunk."""
	lines = text.splitlines()
	sections: list[list[str]] = []
	current: list[str] = []
	for line in lines:
		if re.match(r"^#{1,6}\s+\S", line.strip()) and current:
			sections.append(current)
			current = []
		current.append(line)
	if current:
		sections.append(current)
	if len(sections) <= 1:
		return _chunks(text, max_chars)

	result: list[str] = []
	for section in sections:
		section_text = "\n".join(section).strip()
		heading = section[0].strip() if section and re.match(r"^#{1,6}\s+\S", section[0].strip()) else ""
		for chunk in _chunks(section_text, max_chars):
			if heading and not chunk.startswith(heading):
				chunk = f"{heading}\n\n{chunk}"
			result.append(chunk)
	return result


def _merge_chunk_batches(chunks: list[str], max_chars: int) -> list[str]:
	batches: list[str] = []
	current: list[str] = []
	current_length = 0
	for chunk in chunks:
		added_length = len(chunk) + (2 if current else 0)
		if current and current_length + added_length > max_chars:
			batches.append("\n\n".join(current))
			current, current_length = [], 0
		current.append(chunk)
		current_length += len(chunk) + (2 if len(current) > 1 else 0)
	if current:
		batches.append("\n\n".join(current))
	return batches


def _star_batches(
	text: str,
	*,
	target_chars: int = STAR_BATCH_CHARS,
	max_batches: int = MAX_STAR_BATCHES,
) -> list[str]:
	"""Combine semantic sections so STAR extraction has a bounded LLM call count."""
	sections = _section_chunks(text)
	if not sections:
		return []
	batches = _merge_chunk_batches(sections, target_chars)
	if len(batches) <= max_batches:
		return batches

	total_chars = sum(len(section) for section in sections) + max(0, len(sections) - 1) * 2
	rebalanced_target = max(target_chars, (total_chars + max_batches - 1) // max_batches)
	for _ in range(8):
		batches = _merge_chunk_batches(sections, rebalanced_target)
		if len(batches) <= max_batches:
			return batches
		rebalanced_target = int(rebalanced_target * 1.2) + 1
	return batches


def _source_preview(text: str, max_chars: int = 12_000) -> str:
	if len(text) <= max_chars:
		return text
	head_chars = max_chars * 2 // 3
	return f"{text[:head_chars]}\n\n[中间内容已省略]\n\n{text[-(max_chars - head_chars):]}"


def _confidence(value: object) -> float:
	try:
		return max(0.0, min(1.0, float(value)))
	except (TypeError, ValueError):
		return 0.0


def _evidence_item(component: str, quote: str, source_text: str) -> dict:
	normalized_source = _clean_whitespace(source_text)
	start = normalized_source.find(quote)
	return {
		"component": component,
		"quote": quote,
		"start_offset": start if start >= 0 else None,
		"end_offset": start + len(quote) if start >= 0 else None,
	}


def _classification_prompt(source: dict) -> str:
	return f"""你是材料分类器。判断材料的主要语义类型，不要根据文件扩展名猜测。

类型定义：
- resume：以个人基本信息、工作/项目/教育经历和技能为主的现有简历。
- technical_document：以技术问题、设计、实现、调试、测试或方案说明为主。
- portfolio：以一个或多个个人作品、案例、演示或成果展示为主。
- mixed：多种类型占比接近，无法确定主要类型。
- unknown：证据不足。

规则：
1. evidence 必须逐字摘自材料，并能直接支持分类。
2. 只输出 JSON：{{"document_kind":"resume","confidence":0.95,"evidence":"原文证据"}}。

材料名称：{source["filename"]}
材料预览：
{_source_preview(source["normalized_text"])}
"""


def _resolve_source_kind(
	conn: sqlite3.Connection,
	source: dict,
	config: dict,
	caller: Callable[..., str | None],
	requested_kind: str | None,
) -> str:
	if requested_kind is not None and requested_kind not in SOURCE_KIND_CHOICES:
		raise ResumeBuilderError("材料类型只能是自动识别、简历、技术文档或作品集")

	if requested_kind in SOURCE_KINDS:
		set_source_classification(
			conn,
			source["id"],
			detected_kind=str(source.get("detected_kind") or "unknown"),
			confidence=_confidence(source.get("detected_kind_confidence")),
			evidence=source.get("detected_kind_evidence"),
			selected_kind=requested_kind,
		)
		return requested_kind

	if requested_kind is None and source.get("selected_kind") in SOURCE_KINDS:
		return str(source["selected_kind"])

	raw = caller(_classification_prompt(source), config, 1200, purpose="resume_source_classify")
	if not raw:
		raise ResumeBuilderError("AI 服务未返回材料分类结果")
	payload = _json_payload(raw)
	kind = str(payload.get("document_kind", "unknown")).strip()
	confidence = _confidence(payload.get("confidence"))
	evidence = _clean_whitespace(str(payload.get("evidence", "")))
	normalized_source = _clean_whitespace(source["normalized_text"])
	if evidence and evidence not in normalized_source:
		evidence = ""
	if kind not in SOURCE_KINDS | {"mixed", "unknown"}:
		kind = "unknown"
	set_source_classification(
		conn,
		source["id"],
		detected_kind=kind,
		confidence=confidence,
		evidence=evidence or None,
		selected_kind=None,
	)
	if kind not in SOURCE_KINDS or confidence < CLASSIFICATION_MIN_CONFIDENCE or not evidence:
		raise ResumeBuilderError("无法可靠识别材料类型，请选择“简历”“技术文档”或“作品集”后重新提取")
	return kind


def _resume_prompt(source: dict, chunk: str, index: int) -> str:
	fields = ", ".join(sorted(RESUME_FIELD_CATEGORIES))
	return f"""你是高精度简历字段抽取器。材料已经确认是一份简历。

规则：
1. 只提取原文明确出现的信息，不推测、不润色、不改写、不转换成 STAR。
2. value 和 evidence 必须逐字摘自原文；value 必须是 evidence 的子串。
3. 每条只表达一个原子字段。属于同一工作或项目的字段使用相同 group_id。
4. entity_type 只能是：{", ".join(sorted(RESUME_ENTITY_TYPES))}。
5. field_name 只能是：{fields}。
6. 数字、日期、公司、职位、学校、技术和链接必须完全保持原文。
7. 只输出 JSON：
{{"facts":[{{"entity_type":"experience","field_name":"company","group_id":"work-1","value":"原文值","evidence":"包含该值的原文证据","confidence":0.98}}]}}。

材料名称：{source["filename"]}
分片：{index}
原始材料：
{chunk}
"""


def _resume_content(field_name: str, value: str) -> str:
	if field_name in RESUME_NARRATIVE_FIELDS:
		return value
	label = RESUME_FIELD_LABELS.get(field_name)
	return f"{label}：{value}" if label else value


def _extract_resume_candidates(
	source: dict,
	config: dict,
	caller: Callable[..., str | None],
) -> list[dict]:
	candidates: list[dict] = []
	seen: set[tuple[str, str, str, str]] = set()
	for index, chunk in enumerate(
		_star_batches(source["normalized_text"], target_chars=RESUME_BATCH_CHARS),
		start=1,
	):
		payload = _request_json_payload(
			caller,
			_resume_prompt(source, chunk, index),
			config,
			6000,
			purpose="resume_source_resume",
			empty_message="AI 服务未返回简历字段抽取结果",
		)
		items = payload.get("facts")
		if not isinstance(items, list):
			raise ResumeBuilderError("AI 简历抽取结果缺少 facts 数组")
		normalized_chunk = _clean_whitespace(chunk)
		for item in items:
			if not isinstance(item, dict):
				continue
			entity_type = str(item.get("entity_type", "")).strip()
			field_name = str(item.get("field_name", "")).strip()
			value = _clean_whitespace(str(item.get("value", "")))
			evidence = _clean_whitespace(str(item.get("evidence", "")))
			raw_group_id = _clean_whitespace(str(item.get("group_id", "")))[:100]
			group_id = f"chunk-{index}:{raw_group_id}" if raw_group_id else None
			if entity_type not in RESUME_ENTITY_TYPES or field_name not in RESUME_FIELD_CATEGORIES:
				continue
			if not value or not evidence or len(value) > 1000 or len(evidence) > 2000:
				continue
			if evidence not in normalized_chunk or value not in evidence:
				continue
			key = (entity_type, group_id or "", field_name, value.casefold())
			if key in seen:
				continue
			seen.add(key)
			content = _resume_content(field_name, value)
			candidates.append({
				"category": RESUME_FIELD_CATEGORIES[field_name],
				"content": content,
				"evidence": evidence,
				"confidence": _confidence(item.get("confidence")),
				"fact_type": "resume_field",
				"entity_type": entity_type,
				"field_name": field_name,
				"group_id": group_id,
				"structured_data": {
					"document_kind": "resume",
					"value": value,
				},
				"completeness": 1,
				"needs_clarification": False,
				"evidence_items": [_evidence_item(field_name, evidence, source["normalized_text"])],
			})
	return candidates


def _star_prompt(source: dict, source_kind: str, chunk: str, index: int, *, strict: bool = False) -> str:
	strict_rules = """
严格重试规则：
- text 可以直接等于 evidence，evidence 必须从原文逐字复制，禁止概括证据。
- 文档只描述系统或团队动作时仍可抽取 Action，但 ownership_level 必须为 unknown。
- 最多返回 6 条互不重复且最能体现职业价值的故事，使用最短连续证据，优先保证 JSON 完整和证据准确。
""" if strict else ""
	return f"""你是职业经历证据抽取器。材料类型是{SOURCE_KIND_LABELS[source_kind]}。

请识别本分片中一个或多个相互独立的项目、作品、业务案例、服务改进或问题闭环，并用 STAR 拆解。

规则：
1. 每个 situation/task/action/result 都输出 text 和逐字 evidence；没有明确信息时使用 null。
2. action 重点说明本人使用什么工具、技术、方法、流程或专业能力解决了什么问题。
3. 文档只说明团队或系统方案、没有个人贡献时，ownership_level 必须是 unknown。
4. ownership_level 只能是 unknown、participated、collaborated、responsible、led。
5. 不得补充原文没有的数字、结果、技术或个人职责。
6. technologies 可记录原文明示的工具、技术、系统、标准或方法；name 必须逐字出现在对应 evidence 中。
7. professional_skills 可以根据明确动作归纳，但必须标记 derived=true 并附原文 evidence。
8. evidence 必须从原文逐字复制，禁止概括；text 可以直接等于 evidence。
9. 文档只描述系统或团队动作时仍可抽取 Action，但 ownership_level 必须为 unknown。
10. 每个分片最多返回 6 条互不重复且最能体现职业价值的故事。优先保留有证据的关键职责、问题解决、流程改进、交付、客户/用户影响、质量安全、团队采用、覆盖范围或真实案例闭环；不要用技术复杂度作为唯一选择标准。
11. 每个 text 最多 240 个字符；每个 evidence 只复制能直接支撑字段的最短连续原文，最多 400 个字符。原文中的双引号、反斜杠和换行必须按 JSON 标准转义。
12. 只输出 JSON：
{{"stories":[{{
  "title":{{"text":"项目名称","evidence":"原文"}},
  "situation":{{"text":"背景或问题","evidence":"原文"}},
  "task":null,
  "action":{{"text":"本人动作","evidence":"原文"}},
  "result":{{"text":"结果","evidence":"原文"}},
  "technologies":[{{"name":"Python","evidence":"原文"}}],
  "professional_skills":[{{"name":"故障定位","evidence":"原文","derived":true}}],
  "ownership_level":"responsible",
  "ownership_evidence":"原文",
  "confidence":0.9
}}]}}。
{strict_rules}

材料名称：{source["filename"]}
分片：{index}
原始材料：
{chunk}
"""


def _star_component(item: dict, name: str, normalized_chunk: str) -> tuple[str, str] | None:
	value = item.get(name)
	if not isinstance(value, dict):
		return None
	text = _clean_whitespace(str(value.get("text", "")))
	evidence = _clean_whitespace(str(value.get("evidence", "")))
	if not text or not evidence or len(text) > 1000 or len(evidence) > 2000:
		return None
	if evidence not in normalized_chunk or _unsupported_tokens(text, evidence):
		return None
	return text, evidence


def _star_named_items(values: object, normalized_chunk: str, *, derived: bool) -> list[dict]:
	result: list[dict] = []
	if not isinstance(values, list):
		return result
	for value in values:
		if not isinstance(value, dict):
			continue
		name = _clean_whitespace(str(value.get("name", "")))[:120]
		evidence = _clean_whitespace(str(value.get("evidence", "")))
		if not name or not evidence or evidence not in normalized_chunk:
			continue
		if not derived and name.casefold() not in evidence.casefold():
			continue
		result.append({"name": name, "evidence": evidence, "derived": derived})
	return result


def _extract_star_candidates(
	source: dict,
	source_kind: str,
	config: dict,
	caller: Callable[..., str | None],
	*,
	strict: bool = True,
	allow_zero_retry: bool = True,
) -> list[dict]:
	def deduplicate_batch(values: list[dict]) -> list[dict]:
		result: list[dict] = []
		for candidate in values:
			data = candidate.get("structured_data") or {}
			title = _clean_whitespace(str(data.get("title", ""))).casefold()
			action = _clean_whitespace(
				str((data.get("action") or {}).get("text", ""))
			).casefold().rstrip("。；;,.，")
			duplicate_index = None
			for existing_index, existing in enumerate(result):
				existing_data = existing.get("structured_data") or {}
				existing_title = _clean_whitespace(str(existing_data.get("title", ""))).casefold()
				existing_action = _clean_whitespace(
					str((existing_data.get("action") or {}).get("text", ""))
				).casefold().rstrip("。；;,.，")
				action_overlap = action and existing_action and (
					action in existing_action or existing_action in action
				)
				same_story = title == existing_title or min(len(action), len(existing_action)) >= 20
				if action_overlap and same_story:
					duplicate_index = existing_index
					if len(action) > len(existing_action):
						result[existing_index] = candidate
					break
			if duplicate_index is None:
				result.append(candidate)
		return result

	candidates: list[dict] = []
	seen: set[str] = set()
	for index, chunk in enumerate(_star_batches(source["normalized_text"]), start=1):
		batch_start = len(candidates)
		payload = _request_json_payload(
			caller,
			_star_prompt(source, source_kind, chunk, index, strict=strict),
			config,
			5000,
			purpose="resume_source_star",
			empty_message="AI 服务未返回 STAR 抽取结果",
		)
		stories = payload.get("stories")
		if not isinstance(stories, list):
			raise ResumeBuilderError("AI STAR 抽取结果缺少 stories 数组")
		normalized_chunk = _clean_whitespace(chunk)
		for story in stories:
			if not isinstance(story, dict):
				continue
			components = {
				name: value
				for name in STAR_COMPONENTS
				if (value := _star_component(story, name, normalized_chunk)) is not None
			}
			if "action" not in components:
				continue
			title_component = _star_component(story, "title", normalized_chunk)
			title = title_component[0] if title_component else Path(source["filename"]).stem
			technologies = _star_named_items(story.get("technologies"), normalized_chunk, derived=False)
			skills = _star_named_items(story.get("professional_skills"), normalized_chunk, derived=True)
			ownership_level = str(story.get("ownership_level", "unknown")).strip()
			ownership_evidence = _clean_whitespace(str(story.get("ownership_evidence", "")))
			if ownership_level not in OWNERSHIP_LEVELS:
				ownership_level = "unknown"
			if not ownership_evidence or ownership_evidence not in normalized_chunk:
				ownership_level, ownership_evidence = "unknown", ""

			missing_fields = [name for name in STAR_COMPONENTS if name not in components]
			if ownership_level == "unknown":
				missing_fields.append("ownership")
			action_text = components["action"][0]
			result_text = components.get("result", ("", ""))[0]
			content = f"{action_text}；{result_text}" if result_text else action_text
			all_evidence = [value[1] for value in components.values()]
			if title_component:
				all_evidence.insert(0, title_component[1])
			all_evidence.extend(item["evidence"] for item in technologies)
			all_evidence.extend(item["evidence"] for item in skills)
			if ownership_evidence:
				all_evidence.append(ownership_evidence)
			all_evidence = list(dict.fromkeys(all_evidence))
			if _unsupported_tokens(content, "\n".join(all_evidence)):
				continue
			key = f"{title.casefold()}\0{action_text.casefold()}"
			if key in seen:
				continue
			seen.add(key)
			group_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
			structured_components = {
				name: {"text": value[0], "evidence": value[1]} for name, value in components.items()
			}
			evidence_items = [
				_evidence_item(name, value[1], source["normalized_text"]) for name, value in components.items()
			]
			if title_component:
				evidence_items.insert(0, _evidence_item("title", title_component[1], source["normalized_text"]))
			for item in technologies:
				evidence_items.append(_evidence_item("technologies", item["evidence"], source["normalized_text"]))
			for item in skills:
				evidence_items.append(_evidence_item("professional_skills", item["evidence"], source["normalized_text"]))
			if ownership_evidence:
				evidence_items.append(_evidence_item("ownership", ownership_evidence, source["normalized_text"]))
			candidates.append({
				"category": "项目经历",
				"content": content,
				"evidence": "\n".join(all_evidence),
				"confidence": _confidence(story.get("confidence")),
				"fact_type": "star_story",
				"entity_type": "project",
				"field_name": "star",
				"group_id": group_id,
				"structured_data": {
					"document_kind": source_kind,
					"title": title,
					**structured_components,
					"technologies": technologies,
					"professional_skills": skills,
					"ownership_level": ownership_level,
					"ownership_evidence": ownership_evidence or None,
					"missing_fields": missing_fields,
				},
				"completeness": len(components) / len(STAR_COMPONENTS),
				"needs_clarification": bool(missing_fields),
				"evidence_items": evidence_items,
			})
		batch_candidates = deduplicate_batch(candidates[batch_start:])
		candidates[batch_start:] = batch_candidates[:MAX_STAR_STORIES_PER_BATCH]
	candidates = deduplicate_batch(candidates)
	if not candidates and allow_zero_retry:
		return _extract_star_candidates(
			source,
			source_kind,
			config,
			caller,
			strict=True,
			allow_zero_retry=False,
		)
	return candidates


def ingest_resume_source(
	conn: sqlite3.Connection,
	*,
	filename: str,
	content: bytes,
	storage_dir: Path,
) -> tuple[dict, bool]:
	"""Normalize and store one local source, returning (source, duplicate)."""
	if len(content) > MAX_SOURCE_BYTES:
		raise ResumeBuilderError("文件大小超过 10MB 限制")
	original_name = safe_resume_filename(filename)
	stored_name, normalized_bytes = prepare_resume_content(original_name, content)
	try:
		normalized_text = normalized_bytes.decode("utf-8").replace("\r\n", "\n").strip()
	except UnicodeDecodeError as exc:
		raise ResumeUploadError("材料必须能转换为 UTF-8 文本") from exc
	if len(normalized_text) < 10:
		raise ResumeBuilderError("材料内容过少，无法提取有效简历事实")
	if len(normalized_text) > MAX_SOURCE_CHARS:
		raise ResumeBuilderError("材料文本超过 120000 字符，请拆分后上传")

	digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
	existing = get_source_by_hash(conn, digest)
	if existing:
		return existing, True

	source_id = uuid4().hex
	storage_dir.mkdir(parents=True, exist_ok=True)
	destination = storage_dir / f"{source_id}_{stored_name}"
	temporary = storage_dir / f".{source_id}.tmp"
	try:
		temporary.write_text(f"{normalized_text}\n", encoding="utf-8")
		temporary.replace(destination)
	finally:
		temporary.unlink(missing_ok=True)
	source = create_source(
		conn,
		filename=original_name,
		source_type=Path(original_name).suffix.lower().lstrip("."),
		stored_path=str(destination),
		content_hash=digest,
		normalized_text=normalized_text,
		source_id=source_id,
	)
	return source, False


def extract_source_facts(
	conn: sqlite3.Connection,
	source_id: str,
	config: dict,
	*,
	source_kind: str | None = None,
	call_text: Callable[..., str | None] | None = None,
) -> list[dict]:
	"""Classify one source and extract typed, evidence-backed fact candidates."""
	source = get_source(conn, source_id)
	if not source:
		raise ResumeBuilderError("材料不存在")
	caller = call_text or call_anthropic_text
	set_source_status(conn, source_id, "extracting")
	try:
		resolved_kind = _resolve_source_kind(conn, source, config, caller, source_kind)
		if resolved_kind == "resume":
			candidates = _extract_resume_candidates(source, config, caller)
		else:
			candidates = _extract_star_candidates(source, resolved_kind, config, caller)
		if not candidates:
			raise ResumeBuilderError("未提取到符合类型约束且带原文证据的简历事实")
		facts = replace_fact_candidates(conn, source_id, candidates)
		set_source_status(conn, source_id, "review", None)
		return facts
	except Exception as exc:
		set_source_status(conn, source_id, "failed", str(exc)[:500])
		raise


def _resume_fact_value(fact: dict) -> str:
	if fact.get("edited_content"):
		value = _clean_whitespace(str(fact["edited_content"]))
		label = RESUME_FIELD_LABELS.get(str(fact.get("field_name")), "")
		return re.sub(r"^" + re.escape(label) + r"[：:]\s*", "", value) if label else value
	data = fact.get("structured_data")
	if isinstance(data, dict) and data.get("value"):
		return _clean_whitespace(str(data["value"]))
	return _clean_whitespace(str(fact.get("effective_content") or ""))


def _clarification_experience_label(fact: dict, facts: list[dict], excluded_field: str) -> str:
	entity_type = str(fact.get("entity_type") or "other")
	preferred_fields = {
		"identity": ("name", "headline"),
		"contact": ("name", "email", "phone"),
		"experience": ("company", "position", "start_date", "end_date"),
		"project": ("project_name", "role", "start_date", "end_date"),
		"education": ("school", "degree", "major", "start_date", "end_date"),
		"skill": ("technology",),
		"certification": ("certification",),
		"award": ("award",),
		"publication": ("publication",),
	}.get(entity_type, ())
	parts: list[str] = []
	for field_name in preferred_fields:
		if field_name == excluded_field:
			continue
		match = next((
			candidate for candidate in facts
			if candidate.get("source_id") == fact.get("source_id")
			and candidate.get("entity_type") == fact.get("entity_type")
			and candidate.get("group_id") == fact.get("group_id")
			and candidate.get("field_name") == field_name
		), None)
		if not match:
			continue
		value = _resume_fact_value(match)
		if value:
			parts.append(f"{RESUME_FIELD_LABELS.get(field_name, field_name)}：{value}")
	return "；".join(parts)[:240] or RESUME_ENTITY_LABELS.get(entity_type, "简历经历")


def refresh_profile_clarifications(conn: sqlite3.Connection) -> list[dict]:
	"""Build a deterministic queue of high-value questions from accepted facts."""
	facts = list_facts(conn, status="accepted")
	items: list[dict] = []
	resume_groups: dict[tuple[str, str, str], list[dict]] = {}
	for fact in facts:
		if fact.get("fact_type") == "resume_field":
			key = (
				str(fact.get("entity_type") or ""),
				str(fact.get("group_id") or ""),
				str(fact.get("field_name") or ""),
			)
			resume_groups.setdefault(key, []).append(fact)

		if fact.get("fact_type") != "star_story" or not isinstance(fact.get("structured_data"), dict):
			continue
		data = fact["structured_data"]
		title = _clean_whitespace(str(data.get("title") or fact["source_filename"]))[:120]
		action = data.get("action") if isinstance(data.get("action"), dict) else {}
		result = data.get("result") if isinstance(data.get("result"), dict) else {}
		action_text = _clean_whitespace(str(action.get("text") or fact["effective_content"]))[:240]
		result_text = _clean_whitespace(str(result.get("text") or ""))[:180]
		if fact.get("edited_content"):
			action_text = _clean_whitespace(str(fact["effective_content"]))[:240]
			result_text = ""
		context = action_text or _clean_whitespace(str(fact["effective_content"]))[:240]
		missing = {str(value) for value in data.get("missing_fields", [])}
		question_specs = {
			"situation": (
				"这段经历缺少“背景或限制”。当时服务的是哪类业务或用户，遇到了什么具体限制（例如时间、质量、资源或合规）？没有可公开信息时请回答“无可公开信息”。",
				"按“业务/用户场景 + 一项具体限制 + 为什么必须处理”回答。",
				"回答会写入这条经历的背景，让招聘者先理解问题为什么重要。",
			),
			"task": (
				"这段经历缺少“你的任务”。你本人需要解决哪个具体问题，或交付什么结果？请同时说明你的负责范围；没有明确个人任务时回答“无明确个人任务”。",
				"按“要解决的问题或交付物 + 我的负责范围”回答，不要只重复项目名称。",
				"回答会写入这条经历的任务，并用于区分团队目标和你的个人职责。",
			),
			"result": (
				"这段经历缺少“完成后的结果”。上述动作最终带来了什么可验证变化？可以填写交付、采用、覆盖、效率、质量或问题闭环；没有统计时回答“未统计”。",
				"按“发生了什么变化 + 适用对象或时间 + 如何验证”回答，定性结果也可以。",
				"回答会直接写入这条经历的结果，不会作为备注附在简历末尾。",
			),
		}
		for component in sorted(missing - {"ownership"}):
			question, answer_guidance, resume_effect = question_specs.get(
				component,
				("这段经历还缺少一项必要信息。请根据当前事实补充；没有或无法确认时直接说明。", "只填写你能确认、能在面试中解释的信息。", "回答会用于完善当前这条经历。"),
			)
			items.append({
				"fact_id": fact["id"],
				"dedupe_key": f"{fact['id']}:missing:{component}",
				"kind": f"missing_{component}",
				"question": f"「{title}」：{question}",
				"priority": 90 if component == "result" else 70,
				"metadata": {
					"component": component, "title": title, "context": context,
					"answer_guidance": answer_guidance, "resume_effect": resume_effect,
				},
			})
		if data.get("ownership_level") == "unknown" or "ownership" in missing:
			items.append({
				"fact_id": fact["id"],
				"dedupe_key": f"{fact['id']}:ownership",
				"kind": "ownership",
				"question": f"「{title}」目前只记录了团队或系统动作，缺少你的个人贡献。请选择参与、协作、负责或主导，并写出你亲自完成的 1—3 个动作。",
				"priority": 100,
				"metadata": {
					"title": title, "context": context,
					"answer_guidance": "按“贡献等级 + 我亲自完成的动作 + 我未负责的范围（如有）”回答，不要只写“负责”。",
					"resume_effect": "回答会决定这条经历使用“参与、协作、负责、主导”中的哪个贡献动词。",
				},
			})
		for skill in data.get("professional_skills", []):
			if not isinstance(skill, dict) or not skill.get("derived"):
				continue
			name = _clean_whitespace(str(skill.get("name", "")))[:120]
			if not name:
				continue
			items.append({
				"fact_id": fact["id"],
				"dedupe_key": f"{fact['id']}:skill:{name.casefold()}",
				"kind": "derived_skill",
				"question": f"根据「{title}」中的现有动作，系统推测你具备“{name}”。你是否认可？如果认可，请说明你在这段经历中如何使用了它。",
				"priority": 60,
				"metadata": {
					"title": title, "skill": name, "context": context,
					"answer_guidance": "先回答“认可”或“不认可”；认可时再补充一个可由当前经历证明的具体动作。",
					"resume_effect": "认可后该能力才可进入专业能力或相关 STAR。",
				},
			})
		if result_text and _structured_tokens(result_text):
			items.append({
				"fact_id": fact["id"],
				"dedupe_key": f"{fact['id']}:metric_source",
				"kind": "metric_source",
				"question": f"「{title}」写到量化结果“{result_text}”。这个数字统计的是哪个时间范围、哪些对象，依据来自哪里？",
				"priority": 80,
				"metadata": {
					"title": title, "context": result_text,
					"answer_guidance": "按“时间范围 + 统计对象 + 数据来源”回答；无法核实时直接回答“无法核实”。",
					"resume_effect": "回答会决定这个数字是否保留，以及简历中如何说明它的统计口径。",
				},
			})

	for (entity_type, group_id, field_name), grouped in resume_groups.items():
		value_groups: dict[str, dict] = {}
		for fact in grouped:
			value = _resume_fact_value(fact)
			if not value:
				continue
			entry = value_groups.setdefault(value.casefold(), {"value": value, "facts": []})
			entry["facts"].append(fact)
		if len(value_groups) <= 1:
			continue
		fact_ids = [fact["id"] for fact in grouped]
		conflict_options: list[dict] = []
		for index, entry in enumerate(value_groups.values(), start=1):
			sources: list[str] = []
			details: list[str] = []
			for grouped_fact in entry["facts"]:
				source_filename = str(grouped_fact.get("source_filename") or "未命名材料")
				if source_filename not in sources:
					sources.append(source_filename)
				experience_label = _clarification_experience_label(
					grouped_fact, facts, field_name
				)
				evidence = _clean_whitespace(str(
					grouped_fact.get("evidence") or grouped_fact.get("effective_content") or ""
				))[:240]
				detail = f"材料《{source_filename}》中的{experience_label}"
				if evidence:
					detail += f"；原文：{evidence}"
				if detail not in details:
					details.append(detail)
			conflict_options.append({
				"label": f"选项 {index}",
				"value": entry["value"],
				"fact_ids": [option_fact["id"] for option_fact in entry["facts"]],
				"sources": sources,
				"details": details,
			})
		entity_label = RESUME_ENTITY_LABELS.get(entity_type, "简历经历")
		field_label = RESUME_FIELD_LABELS.get(field_name, "信息")
		option_summary = "；".join(
			f"{option['label']}“{option['value']}”（来源：{'、'.join(option['sources'])}）"
			for option in conflict_options
		)
		items.append({
			"fact_id": fact_ids[0],
			"dedupe_key": f"conflict:{entity_type}:{group_id}:{field_name}",
			"kind": "conflict",
			"question": (
				f"系统把以下 {len(conflict_options)} 种“{field_label}”写法归入同一段{entity_label}，"
				f"但内容不一致：{option_summary}。请确认应保留哪个选项；"
				"如果都正确，请说明每个选项分别属于哪段经历。"
			),
			"priority": 95,
			"metadata": {
				"entity_type": entity_type,
				"entity_label": entity_label,
				"group_id": group_id,
				"field_name": field_name,
				"field_label": field_label,
				"fact_ids": fact_ids,
				"conflict_options": conflict_options,
				"context": "\n".join(
					f"{option['label']}｜{field_label}：{option['value']}｜{'；'.join(option['details'])}"
					for option in conflict_options
				),
				"answer_guidance": (
					"直接回答“保留选项 1/2”；如果都要保留，请写成"
					"“选项 1 属于哪家公司、项目或时间，选项 2 属于哪一段经历”。"
				),
				"resume_effect": (
					f"回答会决定简历中{entity_label}的“{field_label}”如何归属，"
					"避免把不同经历错误合并。"
				),
			},
		})
	resolved_keys = {
		item["dedupe_key"]
		for item in list_clarifications(conn)
		if item["status"] in {"answered", "dismissed"}
	}
	unresolved = [item for item in items if item["dedupe_key"] not in resolved_keys]
	unresolved.sort(key=lambda item: (-int(item["priority"]), str(item["dedupe_key"])))
	return replace_open_clarifications(conn, unresolved[:MAX_PROFILE_QUESTIONS])


def _select_profile_prompt_facts(facts: list[dict], target_role: str) -> list[dict]:
	if not _clean_whitespace(target_role):
		return facts
	project_groups: dict[tuple[str, str], list[dict]] = {}
	for fact in facts:
		if fact.get("fact_type") == "resume_field" and fact.get("entity_type") == "project":
			key = (str(fact.get("source_filename") or ""), str(fact.get("group_id") or fact["id"]))
			project_groups.setdefault(key, []).append(fact)
	if len(project_groups) <= MAX_TARGET_RESUME_PROJECTS:
		return facts

	role_text = _clean_whitespace(target_role).casefold()
	role_tokens = set(re.findall(r"[a-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,}", role_text))
	for token in list(role_tokens):
		if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
			role_tokens.update(token[index:index + size] for size in (2, 3) for index in range(len(token) - size + 1))

	def group_score(group: list[dict]) -> tuple[int, int, int, str]:
		text = " ".join(str(fact.get("effective_content") or "") for fact in group).casefold()
		readable = int("***" not in text)
		relevance = sum(len(token) for token in role_tokens if token in text)
		latest_date = max(
			(str(fact.get("effective_content") or "") for fact in group if fact.get("field_name") == "start_date"),
			default="",
		)
		return readable, relevance, len(group), latest_date

	selected_groups = {
		key for key, _ in sorted(
			project_groups.items(),
			key=lambda item: group_score(item[1]),
			reverse=True,
		)[:MAX_TARGET_RESUME_PROJECTS]
	}
	return [
		fact for fact in facts
		if not (
			fact.get("fact_type") == "resume_field"
			and fact.get("entity_type") == "project"
			and (
				str(fact.get("source_filename") or ""),
				str(fact.get("group_id") or fact["id"]),
			) not in selected_groups
		)
	]


def _profile_prompt(
	facts: list[dict],
	clarifications: list[dict],
	target_role: str = "",
	*,
	focus: str = "all",
) -> str:
	def compact_structure(fact: dict) -> dict | None:
		data = fact.get("structured_data")
		if not isinstance(data, dict):
			return None
		if fact.get("fact_type") == "resume_field":
			return {"value": _resume_fact_value(fact)}
		if fact.get("fact_type") != "star_story":
			return None
		if fact.get("edited_content"):
			return {"title": data.get("title")}
		result: dict = {
			"title": data.get("title"),
			"technologies": [
				item.get("name") for item in data.get("technologies", [])
				if isinstance(item, dict) and item.get("name")
			],
			"ownership_level": data.get("ownership_level"),
			"missing_fields": data.get("missing_fields", []),
		}
		for key in STAR_COMPONENTS:
			component = data.get(key)
			if isinstance(component, dict) and component.get("text"):
				result[key] = component["text"]
		return result

	public_facts: list[dict] = []
	resume_entities: dict[tuple[str, str, str], dict] = {}
	for fact in facts:
		if fact.get("fact_type") != "resume_field":
			public_facts.append({
				"type": "star_story",
				"id": fact["id"],
				"group_id": fact.get("group_id"),
				"source_filename": fact.get("source_filename"),
				"content": fact.get("effective_content") or fact.get("content"),
				"user_edited": bool(fact.get("edited_content")),
				"structured_data": compact_structure(fact),
			})
			continue
		entity_type = str(fact.get("entity_type") or "other")
		group_id = str(fact.get("group_id") or fact["id"])
		source_filename = str(fact.get("source_filename") or "")
		key = (source_filename, entity_type, group_id)
		entity = resume_entities.setdefault(key, {
			"type": "resume_entity",
			"entity_type": entity_type,
			"group_id": group_id,
			"source_filename": source_filename,
			"fields": [],
		})
		entity["fields"].append([
			fact["id"],
			fact.get("field_name"),
			(compact_structure(fact) or {}).get("value"),
		])
	public_facts.extend(resume_entities.values())

	grouped_answers: dict[tuple[str, str], dict] = {}
	accepted_fact_ids = {str(fact["id"]) for fact in facts}
	for item in clarifications:
		if item.get("status") != "answered" or not item.get("answer"):
			continue
		metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
		linked_fact_ids = [str(item["fact_id"])] if item.get("fact_id") else []
		linked_fact_ids.extend(str(value) for value in metadata.get("fact_ids", []))
		linked_fact_ids = [value for value in dict.fromkeys(linked_fact_ids) if value in accepted_fact_ids]
		if item.get("fact_id") and not linked_fact_ids:
			continue
		answer = _clean_whitespace(str(item["answer"]))
		if item.get("kind") == "derived_skill":
			answer = re.split(r"[，；]", answer, maxsplit=1)[0]
		kind = str(item.get("kind") or "confirmation")
		key = (kind, str(item["id"]) if kind == "conflict" else answer.casefold())
		grouped = grouped_answers.setdefault(key, {
			"ids": [],
			"fact_ids": [],
			"kind": key[0],
			"answer": answer,
		})
		grouped["ids"].append(item["id"])
		grouped["fact_ids"].extend(linked_fact_ids)
		if kind == "conflict":
			grouped["question"] = item.get("question")
			grouped["conflict_options"] = metadata.get("conflict_options", [])
	public_answers = []
	for item in grouped_answers.values():
		item["ids"] = list(dict.fromkeys(item["ids"]))
		item["fact_ids"] = list(dict.fromkeys(item["fact_ids"]))
		public_answers.append(item)
	role_instruction = _clean_whitespace(target_role) or "通用"
	if focus == "sections":
		focus_instruction = (
			"本次只生成 sections 和审核信息；projects 必须输出空数组。"
			"把客户项目名称与职责作为工作经历中的代表项目紧凑合并，不为其单独创建 project。"
		)
	elif focus == "projects":
		focus_instruction = (
			"本次只生成 projects 和审核信息；sections 必须输出空数组。"
			"同一 source_filename 的技术总结默认代表同一个仓库项目，即使 STAR title 不同也要归并，"
			"各 title 作为该项目下的 STAR heading；除非材料明确说明它们是不同项目。"
			"source_filename 只用于归并，不是默认项目标题。项目标题优先采用事实 structured_data.title "
			"或材料正文明确写出的项目、案例、作品、活动名称；只有文件名本身明显是可投递的项目名称时，"
			"才可去掉扩展名和“技术总结”等后缀使用。不得把 README、resume、*_case 等机器式文件名写入简历。"
		)
	else:
		focus_instruction = "本次同时生成 sections 和 projects。"
	return f"""你是严谨的中文简历编辑。把已接受事实和已确认回答整理成可直接审阅和启用的中文职业简历档案。

目标岗位：{role_instruction}
生成阶段：{focus_instruction}

规则：
1. 只能使用输入中的信息，不得新增或推测数字、日期、技术、公司、职责和成果。content 是用户审核后的正文；user_edited 为 true 时必须以该正文为准，不能恢复已删除的动作或结果。
2. 每个输出条目必须引用有效 fact_ids 或 clarification_ids；简历实体 fields 每行依次为 [fact_id, field_name, value]，确认回答 ids 中的值是 clarification_id。
3. 项目、竞赛或作品必须按 group_id、标题和来源文件归并；一个 project 下可以且应当包含多个 stars，每个 star 表达一个较小的技术贡献。
4. 每个 star 的 bullet 应优先写成“使用/基于什么工具、技术、方法、流程或专业能力，解决/完成什么问题或任务；产生什么已证实结果”。没有结果证据时省略结果，不得编造，并把缺口放入 known_gaps。
5. situation、task、action、result 是内部事实结构；最终 bullet 不要机械输出 S/T/A/R 标签。action 和 bullet 必须存在。
6. 项目标题只出现一次，同一项目的多个技术贡献分别放入 stars；不要把一个项目拆成多个重复项目。
7. 非项目类基本信息、教育、专业能力可放入 sections。项目、竞赛、作品不得只放在 sections。
8. 团队方案没有个人贡献证据时，不得使用主导、负责、推动或独立完成等动词。
9. known_gaps 和 approved_framings 只是审核报告字段，不属于投递简历正文；approved_framings 只收录用户明确确认的表达边界。
10. 每个项目和 STAR 都必须携带支撑自身文本的引用 ID。
11. 输出要接近一份简洁中文简历：最多 4 个项目，每个项目选择 1-5 条不重复且最能体现目标职业价值的 STAR；优先保留已确认的采用/服务范围、项目覆盖、质量改进、交付成果、客户或用户影响、真实问题闭环和岗位相关能力。简历来源中只有项目名称与职责的经历可以作为代表项目合并进工作经历 section；总结材料/作品集中的 star_story 优先整理为 projects。
12. sections 中同类原子事实先合并为紧凑条目；项目技术贡献之间要有清楚边界。
13. 目标岗位只能影响事实的选择、排序和篇幅，不能作为事实来源，也不能据此新增输入中没有的技术、职责、关键词或成果。
14. 已确认回答是新增证据：回答补充了本人动作、任务、结果、量化口径或贡献等级时，必须融入对应 section 或 STAR 正文并引用 clarification_ids，不得只放入 approved_framings。回答为“无”、未确认信息或纯表达边界时才不写入正文。\n15. 只输出 JSON：
{{
  "sections":[{{"key":"skills","title":"专业能力","items":[{{"text":"条目","fact_ids":["id"],"clarification_ids":[]}}]}}],
  "projects":[{{
    "title":"项目或竞赛名称",
    "meta":"奖项、角色或时间；没有则为空",
    "fact_ids":["id"],
    "clarification_ids":[],
    "stars":[{{
      "heading":"技术贡献短标题",
      "situation":"背景；没有则为空",
      "task":"要解决的问题或任务",
      "action":"使用技术或方法实施的动作",
      "result":"已证实结果；没有则为空",
      "bullet":"使用/基于技术或方法，解决/完成问题或任务；已证实结果",
      "technologies":["技术名"],
      "fact_ids":["id"],
      "clarification_ids":[]
    }}]
  }}],
  "known_gaps":[{{"text":"已知缺口","fact_ids":["id"],"clarification_ids":[]}}],
  "approved_framings":[{{"text":"用户确认表达","fact_ids":[],"clarification_ids":["id"]}}]
}}。

已接受事实：
{json.dumps(public_facts, ensure_ascii=False, separators=(",", ":"))}

已确认回答：
{json.dumps(public_answers, ensure_ascii=False, separators=(",", ":"))}
"""


def _validated_profile_item(
	item: object,
	fact_map: dict[str, dict],
	clarification_map: dict[str, dict],
) -> dict | None:
	if not isinstance(item, dict):
		return None
	text = _clean_whitespace(str(item.get("text", "")))
	fact_ids = list(dict.fromkeys(
		str(value) for value in item.get("fact_ids", []) if str(value) in fact_map
	))
	clarification_ids = list(dict.fromkeys(
		str(value) for value in item.get("clarification_ids", []) if str(value) in clarification_map
	))
	if not text or not (fact_ids or clarification_ids):
		return None
	evidence_parts = []
	for fact_id in fact_ids:
		fact = fact_map[fact_id]
		if fact.get("edited_content"):
			data = fact.get("structured_data") or {}
			evidence_parts.append(str(fact["effective_content"]))
			if fact.get("fact_type") == "star_story" and isinstance(data, dict):
				evidence_parts.append(str(data.get("title") or ""))
			continue
		evidence_parts.extend([
			str(fact.get("effective_content", "")),
			str(fact.get("evidence", "")),
			str(fact.get("source_filename", "")),
			json.dumps(fact.get("structured_data"), ensure_ascii=False),
		])
	for clarification_id in clarification_ids:
		evidence_parts.append(str(clarification_map[clarification_id].get("answer", "")))
	evidence = "\n".join(evidence_parts)
	unsupported = _unsupported_tokens(text, evidence)
	if unsupported:
		raise ResumeBuilderError(f"职业档案包含无来源事实：{', '.join(unsupported)}")
	for verb in PROFILE_STRONG_VERBS:
		if verb in text and verb not in evidence:
			raise ResumeBuilderError(f"职业档案提升了个人贡献等级：{verb}")
	return {"text": text, "fact_ids": fact_ids, "clarification_ids": clarification_ids}


def _validated_profile_payload(
	payload: dict,
	facts: list[dict],
	clarifications: list[dict],
	*,
	allow_partial: bool = False,
	require_projects: bool = True,
) -> tuple[dict, list[str], list[str], list[str]]:
	fact_map = {fact["id"]: fact for fact in facts}
	clarification_map = {
		item["id"]: item
		for item in clarifications
		if item.get("status") == "answered"
		and item.get("answer")
		and (not item.get("fact_id") or str(item["fact_id"]) in fact_map)
	}
	used_fact_ids: list[str] = []
	used_clarification_ids: list[str] = []
	validation_warnings: list[str] = []

	def validate_item(value: object, context: str) -> dict | None:
		try:
			return _validated_profile_item(value, fact_map, clarification_map)
		except ResumeBuilderError as exc:
			if not allow_partial:
				raise
			validation_warnings.append(f"{context}：{exc}")
			return None

	def validate_items(values: object, context: str) -> list[dict]:
		result: list[dict] = []
		if not isinstance(values, list):
			return result
		for index, value in enumerate(values, start=1):
			item = validate_item(value, f"{context}第 {index} 条")
			if item:
				result.append(item)
				used_fact_ids.extend(item["fact_ids"])
				used_clarification_ids.extend(item["clarification_ids"])
		return result

	sections: list[dict] = []
	for section in payload.get("sections", []):
		if not isinstance(section, dict):
			continue
		key = re.sub(r"[^a-z0-9_-]", "", str(section.get("key", "")).casefold())[:40]
		title = _clean_whitespace(str(section.get("title", "")))[:80]
		items = validate_items(section.get("items"), title or "正文")
		if key and title and items:
			sections.append({"key": key, "title": title, "items": items})

	def referenced_item(
		text: object,
		fact_ids: object,
		clarification_ids: object,
		context: str,
	) -> dict | None:
		return validate_item(
			{"text": text, "fact_ids": fact_ids, "clarification_ids": clarification_ids},
			context,
		)

	projects: list[dict] = []
	for project_index, raw_project in enumerate(payload.get("projects", []), start=1):
		if not isinstance(raw_project, dict):
			continue
		project_fact_ids = raw_project.get("fact_ids", [])
		project_clarification_ids = raw_project.get("clarification_ids", [])
		title_item = referenced_item(
			raw_project.get("title"), project_fact_ids, project_clarification_ids,
			f"第 {project_index} 个项目标题",
		)
		if not title_item:
			continue
		meta_text = _clean_whitespace(str(raw_project.get("meta", "")))
		meta_item = referenced_item(
			meta_text, project_fact_ids, project_clarification_ids,
			f"项目“{title_item['text']}”元信息",
		) if meta_text else None
		stars: list[dict] = []
		for star_index, raw_star in enumerate(raw_project.get("stars", []), start=1):
			if not isinstance(raw_star, dict):
				continue
			star_fact_ids = raw_star.get("fact_ids", [])
			star_clarification_ids = raw_star.get("clarification_ids", [])
			bullet_item = referenced_item(
				raw_star.get("bullet"), star_fact_ids, star_clarification_ids,
				f"项目“{title_item['text']}”第 {star_index} 条 STAR",
			)
			if not bullet_item:
				continue
			action_item = referenced_item(
				raw_star.get("action"), star_fact_ids, star_clarification_ids,
				f"项目“{title_item['text']}”第 {star_index} 条 Action",
			)
			if not action_item:
				continue
			star: dict = {
				"bullet": bullet_item["text"],
				"action": action_item["text"],
				"fact_ids": bullet_item["fact_ids"],
				"clarification_ids": bullet_item["clarification_ids"],
			}
			for key in ("heading", "situation", "task", "result"):
				value = _clean_whitespace(str(raw_star.get(key, "")))
				if value:
					validated = referenced_item(
						value, star_fact_ids, star_clarification_ids,
						f"项目“{title_item['text']}”第 {star_index} 条 {key}",
					)
					if validated:
						star[key] = validated["text"]
			technologies: list[str] = []
			for technology in raw_star.get("technologies", []):
				validated = referenced_item(
					technology, star_fact_ids, star_clarification_ids,
					f"项目“{title_item['text']}”第 {star_index} 条技术",
				)
				if validated:
					technologies.append(validated["text"])
			star["technologies"] = list(dict.fromkeys(technologies))
			stars.append(star)
			used_fact_ids.extend(bullet_item["fact_ids"])
			used_clarification_ids.extend(bullet_item["clarification_ids"])
		if not stars:
			continue
		project = {
			"title": title_item["text"],
			"meta": meta_item["text"] if meta_item else "",
			"fact_ids": title_item["fact_ids"],
			"clarification_ids": title_item["clarification_ids"],
			"stars": stars,
		}
		projects.append(project)
		used_fact_ids.extend(title_item["fact_ids"])
		used_clarification_ids.extend(title_item["clarification_ids"])

	has_project_facts = any(
		fact.get("fact_type") == "star_story" or fact.get("entity_type") in {"project", "award"}
		for fact in facts
	)
	if require_projects and has_project_facts and not projects and allow_partial:
		grouped_stars: dict[tuple[str, str], list[dict]] = {}
		for fact in facts:
			data = fact.get("structured_data")
			if fact.get("fact_type") != "star_story" or not isinstance(data, dict):
				continue
			title = _clean_whitespace(str(data.get("title", ""))) or str(fact.get("source_filename", "项目"))
			key = (str(fact.get("source_id", "")), title.casefold())
			grouped_stars.setdefault(key, []).append(fact)
		for grouped in list(grouped_stars.values())[:8]:
			first_data = grouped[0].get("structured_data") or {}
			title = _clean_whitespace(str(first_data.get("title", ""))) or str(grouped[0].get("source_filename", "项目"))
			stars: list[dict] = []
			for fact in grouped[:5]:
				data = fact.get("structured_data") or {}
				action = data.get("action") if isinstance(data.get("action"), dict) else {}
				result = data.get("result") if isinstance(data.get("result"), dict) else {}
				technologies = [
					_clean_whitespace(str(item.get("name", "")))
					for item in data.get("technologies", []) if isinstance(item, dict) and item.get("name")
				]
				if fact.get("edited_content"):
					action, result, technologies = {}, {}, []
				fact_id = str(fact["id"])
				stars.append({
					"heading": "、".join(technologies[:3]) or "技术实现",
					"action": _clean_whitespace(str(action.get("text", ""))) or fact["effective_content"],
					"result": _clean_whitespace(str(result.get("text", ""))),
					"bullet": fact["effective_content"],
					"technologies": technologies,
					"fact_ids": [fact_id],
					"clarification_ids": [],
				})
				used_fact_ids.append(fact_id)
			projects.append({
				"title": title,
				"meta": "",
				"fact_ids": [str(fact["id"]) for fact in grouped[:5]],
				"clarification_ids": [],
				"stars": stars,
			})
		if projects:
			validation_warnings.append("AI 项目条目未通过事实校验，已使用接受的 STAR 事实生成保守版本")
	if require_projects and has_project_facts and not projects:
		raise ResumeBuilderError("职业档案必须把项目事实整理为一个项目下的一个或多个 STAR")
	if not sections and not projects:
		raise ResumeBuilderError("职业档案没有可追溯的正文条目")
	profile = {
		"sections": sections,
		"projects": projects,
		"known_gaps": validate_items(payload.get("known_gaps"), "待补充信息"),
		"approved_framings": validate_items(payload.get("approved_framings"), "已确认表达边界"),
	}
	return (
		profile,
		list(dict.fromkeys(used_fact_ids)),
		list(dict.fromkeys(used_clarification_ids)),
		list(dict.fromkeys(validation_warnings)),
	)


def _required_body_clarification_ids(facts: list[dict], clarifications: list[dict]) -> list[str]:
	fact_ids = {str(fact["id"]) for fact in facts}
	body_kinds = {"missing_situation", "missing_task", "missing_result", "ownership", "metric_source"}
	review_only_answers = {"无", "没有", "未知", "不确定", "无法确认", "无法核实", "未统计", "不认可"}
	review_only_prefixes = ("当前未明确", "未确认", "暂无", "没有统计", "无法核实", "不确定")
	required: list[str] = []
	for item in clarifications:
		answer = _clean_whitespace(str(item.get("answer") or ""))
		if item.get("status") != "answered" or not answer:
			continue
		if str(item.get("kind") or "") not in body_kinds:
			continue
		if answer.casefold() in review_only_answers or answer.startswith(review_only_prefixes):
			continue
		metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
		linked = [str(item["fact_id"])] if item.get("fact_id") else []
		linked.extend(str(value) for value in metadata.get("fact_ids", []))
		if linked and not fact_ids.intersection(linked):
			continue
		required.append(str(item["id"]))
	return list(dict.fromkeys(required))


def _body_clarification_ids(profile: dict) -> set[str]:
	result: set[str] = set()
	for section in profile.get("sections", []):
		for item in section.get("items", []):
			result.update(str(value) for value in item.get("clarification_ids", []))
	for project in profile.get("projects", []):
		result.update(str(value) for value in project.get("clarification_ids", []))
		for star in project.get("stars", []):
			result.update(str(value) for value in star.get("clarification_ids", []))
	return result


def _ensure_answer_fusion(
	profile: dict,
	facts: list[dict],
	clarifications: list[dict],
) -> None:
	required = _required_body_clarification_ids(facts, clarifications)
	missing = [value for value in required if value not in _body_clarification_ids(profile)]
	if missing:
		raise ResumeBuilderError("职业档案未融合已确认回答：" + ", ".join(missing))


def render_career_profile_markdown(profile: dict) -> str:
	lines = ["# 职业简历档案"]
	for section in profile.get("sections", []):
		lines.extend(["", f"## {section['title']}", ""])
		lines.extend(f"- {item['text']}" for item in section["items"])
	projects = profile.get("projects", [])
	if projects:
		lines.extend(["", "## 项目与竞赛经历"])
		for project in projects:
			lines.extend(["", f"### {project['title']}"])
			if project.get("meta"):
				lines.extend(["", f"*{project['meta']}*"])
			for star in project["stars"]:
				prefix = f"**{star['heading']}**：" if star.get("heading") else ""
				lines.extend(["", f"- {prefix}{star['bullet']}"])
	return "\n".join(lines).strip() + "\n"


def compose_career_profile(
	conn: sqlite3.Connection,
	config: dict,
	*,
	output_dir: Path,
	target_role: str = "",
	call_text: Callable[..., str | None] | None = None,
) -> dict:
	"""Create a durable Career Profile from accepted facts and confirmed answers."""
	facts = list_facts(conn, status="accepted")
	if not facts:
		raise ResumeBuilderError("请先接受至少一条材料事实")
	clarifications = refresh_profile_clarifications(conn)
	caller = call_text or call_anthropic_text
	target_role = _clean_whitespace(target_role)[:100]
	prompt_facts = _select_profile_prompt_facts(facts, target_role)
	part_specs = []
	resume_facts = [fact for fact in prompt_facts if fact.get("fact_type") == "resume_field"]
	project_facts = [fact for fact in prompt_facts if fact.get("fact_type") != "resume_field"]
	if resume_facts:
		part_specs.append(("sections", resume_facts, False, 2600))
	if project_facts:
		project_groups: dict[str, list[dict]] = {}
		for fact in project_facts:
			group_key = str(fact.get("source_id") or fact.get("source_filename") or "unknown")
			project_groups.setdefault(group_key, []).append(fact)
		for group_facts in project_groups.values():
			part_specs.append(("projects", group_facts, True, 3200))

	profile = {"sections": [], "projects": [], "known_gaps": [], "approved_framings": []}
	used_fact_ids: list[str] = []
	used_clarification_ids: list[str] = []
	validation_warnings: list[str] = []
	prompt_char_count = 0
	for focus, part_facts, require_projects, max_tokens in part_specs:
		base_prompt = _profile_prompt(
			part_facts,
			clarifications,
			target_role,
			focus=focus,
		)
		prompt_char_count += len(base_prompt)
		payload = _request_json_payload(
			caller,
			base_prompt,
			config,
			max_tokens,
			purpose="resume_profile_compose",
			empty_message="AI 服务未返回职业档案",
		)
		payload["projects" if focus == "sections" else "sections"] = []
		try:
			part_profile, part_fact_ids, part_clarification_ids, part_warnings = _validated_profile_payload(
				payload,
				part_facts,
				clarifications,
				require_projects=require_projects,
			)
			_ensure_answer_fusion(part_profile, part_facts, clarifications)
		except ResumeBuilderError as exc:
			message = str(exc)
			if not message.startswith((
				"职业档案包含无来源事实",
				"职业档案提升了个人贡献等级",
				"职业档案未融合已确认回答",
			)):
				raise
			repair_prompt = (
				f"{base_prompt}\n\n上一份草稿未通过确定性校验：{message}。"
				"请重新生成完整 JSON。每个条目的 text 只能使用该条目自身 fact_ids 和 "
				"clarification_ids 对应输入中已经出现的数字、日期、英文技术名和贡献动词；"
				"不能借用其他未引用事实中的词。"
			)
			repaired_payload = _request_json_payload(
				caller,
				repair_prompt,
				config,
				max_tokens,
				purpose="resume_profile_compose",
				empty_message="AI 服务未返回修复后的职业档案",
			)
			repaired_payload["projects" if focus == "sections" else "sections"] = []
			try:
				part_profile, part_fact_ids, part_clarification_ids, part_warnings = _validated_profile_payload(
					repaired_payload,
					part_facts,
					clarifications,
					require_projects=require_projects,
				)
				_ensure_answer_fusion(part_profile, part_facts, clarifications)
			except ResumeBuilderError as repair_exc:
				if not str(repair_exc).startswith((
					"职业档案包含无来源事实",
					"职业档案提升了个人贡献等级",
				)):
					raise
				part_profile, part_fact_ids, part_clarification_ids, part_warnings = _validated_profile_payload(
					repaired_payload,
					part_facts,
					clarifications,
					allow_partial=True,
					require_projects=require_projects,
				)
		for key, values in profile.items():
			values.extend(part_profile.get(key, []))
		used_fact_ids.extend(part_fact_ids)
		used_clarification_ids.extend(part_clarification_ids)
		validation_warnings.extend(f"{focus}：{warning}" for warning in part_warnings)
	used_fact_ids = list(dict.fromkeys(used_fact_ids))
	used_clarification_ids = list(dict.fromkeys(used_clarification_ids))
	validation_warnings = list(dict.fromkeys(validation_warnings))
	markdown = render_career_profile_markdown(profile)
	open_clarifications = [item for item in clarifications if item["status"] == "open"]
	quality_report = {
		"target_role": target_role or "通用",
		"prompt_char_count": prompt_char_count,
		"prompt_fact_count": len(prompt_facts),
		"prompt_request_count": len(part_specs),
		"accepted_fact_count": len(facts),
		"used_fact_count": len(used_fact_ids),
		"unused_fact_ids": [fact["id"] for fact in facts if fact["id"] not in used_fact_ids],
		"answered_clarification_count": len(used_clarification_ids),
		"open_clarification_count": len(open_clarifications),
		"incomplete_fact_count": sum(bool(fact.get("needs_clarification")) for fact in facts),
		"evidence_coverage": round(len(used_fact_ids) / len(facts), 4),
		"validation_warning_count": len(validation_warnings),
		"validation_warnings": validation_warnings,
	}
	profile_id = uuid4().hex
	output_dir.mkdir(parents=True, exist_ok=True)
	json_path = output_dir / f"career_profile_{profile_id[:12]}.json"
	markdown_path = output_dir / f"career_profile_{profile_id[:12]}.md"
	json_temporary = output_dir / f".{profile_id}.json.tmp"
	markdown_temporary = output_dir / f".{profile_id}.md.tmp"
	try:
		json_temporary.write_text(
			json.dumps(
				{"profile": profile, "quality_report": quality_report},
				ensure_ascii=False,
				indent=2,
			)
			+ "\n",
			encoding="utf-8",
		)
		markdown_temporary.write_text(markdown, encoding="utf-8")
		json_temporary.replace(json_path)
		markdown_temporary.replace(markdown_path)
	finally:
		json_temporary.unlink(missing_ok=True)
		markdown_temporary.unlink(missing_ok=True)
	name = f"主简历 {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')} · {profile_id[:6]}"
	return create_profile_version(
		conn,
		name=name,
		profile=profile,
		markdown=markdown,
		quality_report=quality_report,
		json_path=str(json_path),
		markdown_path=str(markdown_path),
		fact_ids=used_fact_ids,
		clarification_ids=used_clarification_ids,
		profile_id=profile_id,
	)


def activate_career_profile(conn: sqlite3.Connection, profile_id: str) -> dict:
	profile = get_profile_version(conn, profile_id)
	if not profile:
		raise ResumeBuilderError("Career Profile 版本不存在")
	if not Path(profile["json_path"]).exists() or not Path(profile["markdown_path"]).exists():
		raise ResumeBuilderError("Career Profile 版本文件不存在")
	return mark_profile_version_active(conn, profile_id) or profile


def compose_resume_version(
	conn: sqlite3.Connection,
	config: dict,
	*,
	target_role: str,
	output_dir: Path,
	call_text: Callable[..., str | None] | None = None,
) -> dict:
	"""Compose a draft master resume from accepted facts only."""
	facts = list_facts(conn, status="accepted")
	if not facts:
		raise ResumeBuilderError("请先接受至少一条材料事实")
	caller = call_text or call_anthropic_text
	public_facts = [
		{"id": fact["id"], "category": fact["category"], "content": fact["effective_content"]}
		for fact in facts
	]
	prompt = f"""你是严谨的中文简历编辑。请把已审核事实整理成一份结构清晰的主简历草稿。

规则：
1. 只能使用给定事实，不得新增、猜测或补全任何信息。
2. 每个条目必须列出支撑它的 fact_ids，且只能引用给定 ID。
3. 可以排序、压缩和合并事实，但不得创造数字、日期、链接、公司、项目或技术名称。
4. 不输出前言、备注或免责声明。
5. 只输出 JSON：{{"sections":[{{"title":"项目经历","items":[{{"text":"简历条目","fact_ids":["id"]}}]}}]}}。

目标方向：{target_role or '通用主简历'}
已审核事实：
{json.dumps(public_facts, ensure_ascii=False)}
"""
	raw = caller(prompt, config, 8000, purpose="resume_compose")
	if not raw:
		raise ResumeBuilderError("AI 服务未返回主简历草稿")
	payload = _json_payload(raw)
	sections = payload.get("sections")
	if not isinstance(sections, list):
		raise ResumeBuilderError("主简历生成结果缺少 sections 数组")

	fact_map = {fact["id"]: fact for fact in facts}
	used_ids: list[str] = []
	markdown_lines = ["# 个人简历"]
	for section in sections:
		if not isinstance(section, dict):
			continue
		title = _clean_whitespace(str(section.get("title", "")))[:40]
		items = section.get("items")
		if not title or not isinstance(items, list):
			continue
		section_lines: list[str] = []
		for item in items:
			if not isinstance(item, dict):
				continue
			text = _clean_whitespace(str(item.get("text", "")))
			fact_ids = [str(value) for value in item.get("fact_ids", []) if str(value) in fact_map]
			if not text or not fact_ids:
				continue
			evidence = "\n".join(
				f"{fact_map[fact_id]['effective_content']}\n{fact_map[fact_id]['evidence']}" for fact_id in fact_ids
			)
			unsupported = _unsupported_tokens(text, evidence)
			if unsupported:
				raise ResumeBuilderError(f"主简历包含无来源事实：{', '.join(unsupported)}")
			section_lines.append(f"- {text}")
			used_ids.extend(fact_ids)
		if section_lines:
			markdown_lines.extend(["", f"## {title}", "", *section_lines])
	if not used_ids:
		raise ResumeBuilderError("主简历生成结果没有可追溯条目")

	markdown = "\n".join(markdown_lines).strip() + "\n"
	version_id = uuid4().hex
	output_dir.mkdir(parents=True, exist_ok=True)
	file_path = output_dir / f"master_resume_{version_id[:12]}.md"
	temporary = output_dir / f".{version_id}.tmp"
	try:
		temporary.write_text(markdown, encoding="utf-8")
		temporary.replace(file_path)
	finally:
		temporary.unlink(missing_ok=True)
	name = f"主简历 {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}"
	return create_version(
		conn,
		name=name,
		target_role=target_role.strip(),
		markdown=markdown,
		file_path=str(file_path),
		fact_ids=used_ids,
		version_id=version_id,
	)


def activate_resume_version(conn: sqlite3.Connection, version_id: str) -> dict:
	version = get_version(conn, version_id)
	if not version:
		raise ResumeBuilderError("主简历版本不存在")
	if not Path(version["file_path"]).exists():
		raise ResumeBuilderError("主简历版本文件不存在")
	return mark_version_active(conn, version_id) or version


def delete_resume_source(
	conn: sqlite3.Connection,
	source_id: str,
	*,
	storage_dir: Path,
	confirmed: bool,
) -> None:
	if not confirmed:
		raise ResumeBuilderError("删除材料需要明确确认")
	source = get_source(conn, source_id)
	if not source:
		raise ResumeBuilderError("材料不存在")
	if source_version_references(conn, source_id):
		raise ResumeBuilderError("材料已被简历或 Career Profile 版本引用，不能删除")
	path = Path(source["stored_path"]).resolve()
	root = storage_dir.resolve()
	if not path.is_relative_to(root):
		raise ResumeBuilderError("材料路径不在受管目录内")
	path.unlink(missing_ok=True)
	delete_source_records(conn, source_id)


def clear_resume_workspace(
	conn: sqlite3.Connection,
	*,
	source_dir: Path,
	resume_dir: Path,
	profile_dir: Path,
	confirmed: bool,
) -> dict:
	"""Clear Resume Studio files and records without touching imported resumes elsewhere."""
	if not confirmed:
		raise ResumeBuilderError("清空简历工作室需要明确确认")

	roots = {
		"source": source_dir.resolve(),
		"resume": resume_dir.resolve(),
		"profile": profile_dir.resolve(),
	}
	candidates: set[Path] = set()

	def add_managed(raw_path: object, root_key: str, expected_name: str) -> None:
		path = Path(str(raw_path)).resolve()
		root = roots[root_key]
		if path.parent != root or path.name != expected_name:
			raise ResumeBuilderError("工作室数据包含不安全的文件路径，已停止清空")
		candidates.add(path)

	for source in list_sources(conn):
		expected_prefix = str(source["id"]) + "_"
		path = Path(str(source["stored_path"])).resolve()
		if path.parent != roots["source"] or not path.name.startswith(expected_prefix):
			raise ResumeBuilderError("工作室数据包含不安全的材料路径，已停止清空")
		candidates.add(path)
	for version in list_versions(conn):
		add_managed(
			version["file_path"], "resume", "master_resume_" + str(version["id"])[:12] + ".md"
		)
	for profile in list_profile_versions(conn):
		prefix = "career_profile_" + str(profile["id"])[:12]
		add_managed(profile["markdown_path"], "profile", prefix + ".md")
		add_managed(profile["json_path"], "profile", prefix + ".json")

	for root in roots.values():
		root.mkdir(parents=True, exist_ok=True)
	for path in roots["source"].iterdir():
		if path.is_file() and re.match(r"^[0-9a-f]{32}_.+", path.name):
			candidates.add(path.resolve())
	for path in roots["resume"].glob("master_resume_*.md"):
		if path.is_file():
			candidates.add(path.resolve())
	for pattern in ("career_profile_*.md", "career_profile_*.json"):
		for path in roots["profile"].glob(pattern):
			if path.is_file():
				candidates.add(path.resolve())

	for path in candidates:
		if path.parent not in roots.values():
			raise ResumeBuilderError("工作室文件路径越过受管目录，已停止清空")
	for path in candidates:
		path.unlink(missing_ok=True)
	counts = clear_resume_studio_records(conn)
	return {"deleted_files": len(candidates), "deleted_records": counts}
