"""Exercise Resume Studio in Chrome using fictional materials and isolated storage.

Run with --allow-external-ai to also verify the configured model. By default the
AI boundary is deterministic; HTTP, SQLite, file IO and browser UI are real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

from patchright.sync_api import expect, sync_playwright

from bosshunter.config import load_config
from bosshunter.resume_builder import service
from bosshunter.web import server

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "resume_studio"


def fixture_ai(prompt, config, max_tokens, **kwargs):
	purpose = kwargs.get("purpose")
	if purpose == "resume_source_resume":
		payload = {"facts": [
			{"entity_type": "identity", "field_name": "name", "value": "林知遥（虚构测试人物）",
			 "evidence": "姓名：林知遥（虚构测试人物）", "group_id": "identity", "confidence": 1},
			{"entity_type": "contact", "field_name": "email", "value": "lin.demo@example.com",
			 "evidence": "邮箱：lin.demo@example.com", "group_id": "identity", "confidence": 1},
		]}
	elif purpose == "resume_source_star":
		action = "我使用 Python 和 SQLite 实现字段校验、重复检测和错误行提示。"
		payload = {"stories": [{
			"title": {"text": "星河测试数据工具（虚构项目）", "evidence": "星河测试数据工具（虚构项目）"},
			"action": {"text": action, "evidence": action},
			"ownership_level": "unknown", "confidence": 1,
		}]}
	elif purpose == "resume_profile_compose":
		facts = json.loads(prompt.split("已接受事实：\n", 1)[1].split("\n\n已确认回答：", 1)[0])
		payload = {"sections": [], "projects": [], "known_gaps": [], "approved_framings": []}
		for fact in facts:
			if fact["type"] == "resume_entity":
				payload["sections"].append({"key": fact["entity_type"], "title": "基本信息", "items": [
					{"text": field[2], "fact_ids": [field[0]]} for field in fact["fields"]
				]})
			else:
				payload["projects"].append({
					"title": fact["structured_data"]["title"], "fact_ids": [fact["id"]],
					"stars": [{"action": fact["content"], "bullet": fact["content"], "fact_ids": [fact["id"]]}],
				})
	else:
		raise AssertionError(f"Unexpected AI purpose: {purpose}")
	return json.dumps(payload, ensure_ascii=False)


class QuietHandler(WSGIRequestHandler):
	def log_message(self, *args):
		pass


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--allow-external-ai", action="store_true")
	args = parser.parse_args()
	output = Path(tempfile.mkdtemp(prefix="resume-studio-", dir=ROOT / "output"))
	(output / "config.yaml").write_text("{}\n", encoding="utf-8")
	original_config = ROOT / "config.yaml"
	original_digest = hashlib.sha256(original_config.read_bytes()).hexdigest() if original_config.exists() else None
	ai_config = load_config(original_config) if args.allow_external_ai else {}
	actual_caller = service.call_anthropic_text
	ai_calls = []

	def caller(prompt, config, max_tokens, **kwargs):
		ai_calls.append(kwargs.get("purpose"))
		print(f"AI {len(ai_calls)}: {kwargs.get('purpose')}", flush=True)
		if args.allow_external_ai:
			return actual_caller(prompt, ai_config, max_tokens, **kwargs)
		return fixture_ai(prompt, config, max_tokens, **kwargs)

	server.set_base_dir(output)
	httpd = make_server("127.0.0.1", 0, server.app, server.ThreadingWSGIServer, QuietHandler)
	thread = Thread(target=httpd.serve_forever, daemon=True)
	thread.start()
	report = {"mode": "live_ai" if args.allow_external_ai else "fixture_ai", "checks": [], "output": str(output)}
	print(f"Artifacts: {output}", flush=True)
	try:
		with patch.object(service, "call_anthropic_text", side_effect=caller), sync_playwright() as playwright:
			browser = playwright.chromium.launch(channel="chrome", headless=True)
			page = browser.new_page(viewport={"width": 1440, "height": 1100})
			page.set_default_timeout(30_000)
			errors = []
			page.on("pageerror", lambda error: errors.append(str(error)))
			page.on("dialog", lambda dialog: dialog.accept())
			base_url = f"http://127.0.0.1:{httpd.server_port}"
			page.route("**/api/resume-studio", lambda route: route.fulfill(status=503, body="Unavailable"), times=1)
			page.goto(base_url + "/resume-studio")
			expect(page.get_by_role("heading", name="简历工作室", exact=True)).to_be_visible()
			expect(page.get_by_role("alert")).to_contain_text("HTTP 503")
			page.get_by_role("button", name="重新加载工作室", exact=True).click()
			expect(page.get_by_role("button", name="重新加载工作室", exact=True)).to_have_count(0)
			report["checks"].append("initial_load_failure_and_retry")
			files = sorted(FIXTURES.glob("*.md"))
			empty = output / "空材料.md"
			empty.write_bytes(b"")
			page.locator('input[type="file"]').set_input_files([str(files[0]), str(empty), str(files[1])])
			expect(page.get_by_role("alert")).to_contain_text("已处理 2/3")
			for path in files:
				expect(page.get_by_text(path.name, exact=True)).to_be_visible()
			report["checks"].append("partial_upload_recovers_and_shows_successes")
			page.locator('input[type="file"]').set_input_files(str(files[0]))
			expect(page.get_by_role("status")).to_contain_text("重复材料")
			report["checks"].append("duplicate_upload")
			for path in files:
				card = page.get_by_text(path.name, exact=True).locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]")
				card.locator("select").select_option("resume" if "简历" in path.name else "technical_document")
				with page.expect_response(lambda r: r.url.endswith("/extract"), timeout=300_000) as response:
					card.get_by_role("button", name="提取事实", exact=True).click()
				result = response.value.json()
				assert response.value.ok, result.get("error")
				expect(page.get_by_role("status")).to_contain_text("待审核事实")
			report["checks"].append("extract_resume_and_technical_document")
			# Accept via the actual page so typed PATCH responses and automatic questions are exercised.
			while page.get_by_role("button", name="接受", exact=True).count():
				page.get_by_role("button", name="接受", exact=True).first.click()
				expect(page.get_by_role("status")).to_contain_text("事实已接受")
			page.get_by_role("button", name="已接受", exact=False).click()
			star = page.locator("div.rounded-xl").filter(has=page.get_by_text("STAR 完整度", exact=False)).filter(
				has=page.get_by_role("button", name="保存修改", exact=True)
			).last
			edited = "我使用 Python 和 SQLite 实现字段校验和错误行提示，仅供虚构测试。"
			star.get_by_role("textbox", name="审核事实：", exact=False).fill(edited)
			star.get_by_role("button", name="保存修改", exact=True).click()
			expect(page.get_by_role("status")).to_contain_text("事实已接受")
			expect(star.get_by_text("STAR 完整度", exact=False)).to_be_visible()
			report["checks"].append("edit_preserves_star_details")
			# Dismiss and restore a question, then answer and reopen it.
			page.get_by_role("button", name="忽略", exact=True).first.click()
			expect(page.get_by_role("status")).to_contain_text("已忽略")
			page.get_by_text("已回答和已忽略的问题", exact=True).click()
			page.get_by_role("button", name="恢复问题", exact=True).first.click()
			expect(page.get_by_role("status")).to_contain_text("重新编辑")
			page.get_by_role("textbox", name="补充确认回答", exact=True).first.fill("未统计")
			page.get_by_role("button", name="确认回答", exact=True).first.click()
			expect(page.get_by_role("status")).to_contain_text("补充信息已确认")
			page.get_by_text("已回答和已忽略的问题", exact=True).click()
			page.get_by_role("button", name="修改回答", exact=True).first.click()
			expect(page.get_by_role("status")).to_contain_text("重新编辑")
			while page.get_by_role("button", name="忽略", exact=True).count():
				page.get_by_role("button", name="忽略", exact=True).first.click()
				expect(page.get_by_role("status")).to_contain_text("已忽略")
			report["checks"].append("dismiss_restore_answer_reopen")
			page.get_by_label("目标岗位（只影响排序和篇幅）").fill("Python 后端工程师")
			for _ in range(2):
				with page.expect_response(lambda r: r.url.endswith("/profile/compose"), timeout=300_000) as response:
					page.get_by_role("button", name="生成项目化 STAR 主简历", exact=True).click()
				result = response.value.json()
				assert response.value.ok, result.get("error")
				expect(page.get_by_role("status")).to_contain_text("已生成")
			page.get_by_role("button", name="查看与启用", exact=False).first.click()
			page.get_by_role("button", name="启用为主简历", exact=True).click()
			expect(page.get_by_role("button", name="当前已启用", exact=True)).to_be_disabled()
			with page.expect_download() as download:
				page.get_by_role("button", name="Markdown", exact=True).click()
			download.value.save_as(output / "虚构主简历.md")
			with page.expect_download() as download:
				page.get_by_role("button", name="JSON", exact=True).click()
			download.value.save_as(output / "虚构主简历.json")
			assert "Python" in (output / "虚构主简历.md").read_text(encoding="utf-8")
			report["checks"].append("compose_twice_select_history_activate_download")
			page.screenshot(path=str(output / "studio-desktop.png"), full_page=True)
			page.reload()
			expect(page.get_by_role("heading", name="简历工作室", exact=True)).to_be_visible()
			workspace = page.request.get(base_url + "/api/resume-studio").json()
			assert len(workspace["profile_versions"]) == 2
			assert sum(p["status"] == "active" for p in workspace["profile_versions"]) == 1
			page.set_viewport_size({"width": 390, "height": 844})
			page.screenshot(path=str(output / "studio-mobile.png"), full_page=True)
			assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile overflow"
			assert page.locator("main").evaluate("el => el.scrollWidth <= el.clientWidth"), "Workspace clipped on mobile"
			assert not errors, errors
			report["checks"].extend(["reload_persistence", "mobile_no_horizontal_overflow", "no_browser_errors"])
			report["fact_count"] = len(workspace["facts"])
			browser.close()
		report["passed"] = True
	finally:
		httpd.shutdown()
		httpd.server_close()
		report["ai_calls"] = ai_calls
		report["real_config_unchanged"] = not original_config.exists() or hashlib.sha256(original_config.read_bytes()).hexdigest() == original_digest
		(output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
		print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
	(ROOT / "output").mkdir(exist_ok=True)
	main()
