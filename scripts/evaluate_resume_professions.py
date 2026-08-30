"""Evaluate Resume Studio against anonymous cross-profession samples."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bosshunter.ai.credentials import AIRequestError
from bosshunter.config import load_config
from bosshunter.db import get_db
from bosshunter.resume_builder import (
	ResumeBuilderError,
	compose_career_profile,
	extract_source_facts,
	ingest_resume_source,
	refresh_profile_clarifications,
)
from bosshunter.resume_builder.store import update_fact


def _score_case(case: dict, facts: list[dict], questions: list[dict], profile: dict) -> dict:
	structured = [
		fact.get("structured_data") or {}
		for fact in facts
		if fact.get("fact_type") == "star_story"
	]
	has_action_and_result = any(item.get("action") and item.get("result") for item in structured)
	markdown = str(profile.get("markdown") or "")
	profile_json = profile.get("profile_json") or {}
	projects = profile_json.get("projects") or []
	star_count = sum(len(project.get("stars") or []) for project in projects)
	questions_are_clear = all(
		(item.get("metadata") or {}).get("context")
		and (item.get("metadata") or {}).get("answer_guidance")
		and (item.get("metadata") or {}).get("resume_effect")
		for item in questions
	)
	checks = {
		"fact_extraction": bool(facts),
		"action_and_result": has_action_and_result,
		"method_in_resume": str(case["method"]).casefold() in markdown.casefold(),
		"project_structure": len(projects) >= 1 and star_count >= 1,
		"questions_are_clear": questions_are_clear,
		"clean_resume": "## 待补充信息" not in markdown and "## 已确认表达边界" not in markdown,
		"zero_validation_warnings": int(
			(profile.get("quality_report") or {}).get("validation_warning_count") or 0
		) == 0,
	}
	weights = {
		"fact_extraction": 20,
		"action_and_result": 20,
		"method_in_resume": 15,
		"project_structure": 15,
		"questions_are_clear": 10,
		"clean_resume": 10,
		"zero_validation_warnings": 10,
	}
	score = sum(weights[name] for name, passed in checks.items() if passed)
	return {
		"id": case["id"],
		"role": case["role"],
		"score": score,
		"checks": checks,
		"fact_count": len(facts),
		"question_count": len(questions),
		"project_count": len(projects),
		"star_count": star_count,
		"evidence_coverage": (profile.get("quality_report") or {}).get("evidence_coverage", 0),
		"validation_warning_count": (
			profile.get("quality_report") or {}
		).get("validation_warning_count", 0),
		"validation_warnings": (
			profile.get("quality_report") or {}
		).get("validation_warnings", []),
	}


def _markdown_report(results: list[dict], generated_at: str) -> str:
	lines = [
		"# Resume Studio 跨职业实用度评测",
		"",
		f"- 生成时间：{generated_at}",
		"- 样本：匿名合成数据，不包含真实个人信息。",
		"- 通过线：单职业 80 分；评分只衡量事实抽取、STAR 结构、问题清晰度和正文安全，不衡量视觉排版或 ATS 匹配。",
		"",
		"| 职业 | 分数 | 事实 | 问题 | 项目/STAR | 证据覆盖率 | 验证告警 |",
		"| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
	]
	for item in results:
		lines.append(
			f"| {item['role']} | {item['score']} | {item['fact_count']} | "
			f"{item['question_count']} | {item['project_count']}/{item['star_count']} | "
			f"{float(item['evidence_coverage']):.0%} | {item['validation_warning_count']} |"
		)
	lines.extend(["", "## 分项检查", ""])
	for item in results:
		failed = [name for name, passed in item["checks"].items() if not passed]
		lines.append(
			f"- **{item['role']}**：{'通过' if not failed else '未通过 ' + ', '.join(failed)}"
		)
	return "\n".join(lines) + "\n"


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--config", default="config.yaml")
	parser.add_argument(
		"--fixtures",
		default="tests/fixtures/resume_professions.json",
	)
	parser.add_argument(
		"--output-dir",
		default="tmp/resume-profession-evaluation",
	)
	parser.add_argument(
		"--external-ai-consent",
		action="store_true",
		help="Required: confirms anonymous samples may be sent to the configured external AI service.",
	)
	parser.add_argument(
		"--case-id",
		help="Run only one anonymous fixture case by id.",
	)
	args = parser.parse_args()
	if not args.external_ai_consent:
		parser.error("必须显式传入 --external-ai-consent 才会发送匿名样本")

	config = load_config(Path(args.config).resolve())
	fixture = json.loads(Path(args.fixtures).resolve().read_text(encoding="utf-8"))
	output_dir = Path(args.output_dir).resolve()
	output_dir.mkdir(parents=True, exist_ok=True)
	results: list[dict] = []

	with tempfile.TemporaryDirectory(prefix="bosshunter-profession-eval-") as temporary:
		temporary_root = Path(temporary)
		cases = fixture["cases"]
		if args.case_id:
			cases = [case for case in cases if case["id"] == args.case_id]
			if not cases:
				parser.error(f"找不到匿名样本：{args.case_id}")
		for index, case in enumerate(cases, start=1):
			case_root = temporary_root / f"{index:02d}-{case['id']}"
			conn = get_db(case_root / "data" / "bosshunter.db")
			try:
				source, _ = ingest_resume_source(
					conn,
					filename=case["filename"],
					content=case["source_text"].encode("utf-8"),
					storage_dir=case_root / "data" / "resume_sources",
				)
				facts = extract_source_facts(
					conn,
					source["id"],
					config,
					source_kind=case["source_kind"],
				)
				for fact in facts:
					update_fact(conn, fact["id"], status="accepted")
				questions = refresh_profile_clarifications(conn)
				profile = compose_career_profile(
					conn,
					config,
					output_dir=case_root / "data" / "career_profiles",
					target_role=case["role"],
				)
				results.append(_score_case(case, facts, questions, profile))
			except (AIRequestError, OSError, ResumeBuilderError, ValueError) as exc:
				results.append({
					"id": case["id"],
					"role": case["role"],
					"score": 0,
					"checks": {},
					"fact_count": 0,
					"question_count": 0,
					"project_count": 0,
					"star_count": 0,
					"evidence_coverage": 0,
					"validation_warning_count": 0,
					"validation_warnings": [],
					"error": str(exc),
				})
			finally:
				conn.close()

	generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
	report = {
		"generated_at": generated_at,
		"sample_policy": "anonymous_synthetic",
		"pass_score": 80,
		"results": results,
	}
	(output_dir / "report.json").write_text(
		json.dumps(report, ensure_ascii=False, indent=2) + "\n",
		encoding="utf-8",
	)
	(output_dir / "report.md").write_text(
		_markdown_report(results, generated_at),
		encoding="utf-8",
	)
	print(json.dumps(report, ensure_ascii=False))
	return 0 if all(item["score"] >= 80 for item in results) else 1


if __name__ == "__main__":
	raise SystemExit(main())
