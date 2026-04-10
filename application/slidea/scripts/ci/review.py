#!/usr/bin/env python3
"""
Patch Review Tool - Optimized Version
Reviews a git patch file for code quality, architecture compliance, and potential issues.
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict
from langchain_openai import ChatOpenAI


from core.utils.config import settings, app_base_dir
from core.utils.llm import default_llm, llm_invoke


@dataclass
class Definition:
    name: str
    type: str
    content: str
    line_start: int


@dataclass
class ReviewIssue:
    message: str
    severity: str
    category: str
    suggestion: Optional[str] = None


@dataclass
class ReviewReport:
    overall_score: int
    summary: str
    architecture_compliant: bool
    architecture_issues: List[str] = field(default_factory=list)
    syntax_errors: List[ReviewIssue] = field(default_factory=list)
    logic_issues: List[ReviewIssue] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


ARCHITECTURE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "compliant": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["compliant", "issues", "summary"],
}

CODE_QUALITY_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "syntax_errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "severity": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["message", "severity"],
            },
        },
        "logic_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "severity": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["message", "severity"],
            },
        },
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "integer"},
        "summary": {"type": "string"},
    },
    "required": ["syntax_errors", "logic_issues", "suggestions", "score", "summary"],
}


class PatchReviewer:
    def __init__(self, patch_path: str, llm=None):
        self.llm = llm or default_llm
        with open(patch_path, "r", encoding="utf-8") as f:
            self.patch_content = f.read()[:65536]

    async def run_review(self) -> ReviewReport:
        logging.debug("Running architecture review...")
        arch_res = await self.review_architecture()
        logging.debug(
            "Architecture review completed: compliant=%s, issues=%s",
            arch_res.get("compliant"),
            len(arch_res.get("issues", [])),
        )

        logging.debug("Starting code quality review ...")
        res = await self.review_quality()

        logging.debug("Building review report...")
        report = ReviewReport(
            overall_score=10,
            summary=arch_res.get("summary", ""),
            architecture_compliant=arch_res.get("compliant", True),
            architecture_issues=arch_res.get("issues", []),
        )

        if res:
            report.overall_score = min(report.overall_score, res.get("score", 10))

            for issue in res.get("syntax_errors", []):
                report.syntax_errors.append(
                    ReviewIssue(
                        message=issue["message"],
                        severity=issue["severity"],
                        category="syntax",
                        suggestion=issue.get("suggestion"),
                    )
                )

            for issue in res.get("logic_issues", []):
                report.logic_issues.append(
                    ReviewIssue(
                        message=issue["message"],
                        severity=issue["severity"],
                        category="logic",
                        suggestion=issue.get("suggestion"),
                    )
                )

            report.suggestions.extend(res.get("suggestions", []))

        if not report.architecture_compliant:
            logging.info(
                "Architecture not compliant, adjusting overall score from %d to min(6)",
                report.overall_score,
            )
            report.overall_score = min(report.overall_score, 6)

        logging.info(
            "Review completed: overall_score=%d, syntax_errors=%d, logic_issues=%d",
            report.overall_score,
            len(report.syntax_errors),
            len(report.logic_issues),
        )
        return report

    async def review_quality(self) -> Dict:
        prompt = f"""审查以下 Patch 中新增代码的质量：

【审查规则】
1. 只关注新增代码
2. 不假设外部定义（如外部函数、类、变量等）
3. 风险导向：Bug/安全问题必报，低严重性需确定性才上报
4. 建设性评论：直接说明问题，准确描述严重性，简洁、客观、有帮助
5. 不关注代码格式，仅关注代码质量问题

【严重性判断】
- error: 确定的 Bug、安全问题、会导致程序崩溃或功能异常
- warning: 可能的问题、代码风格问题、潜在风险

【输出要求】
必须且只按照如下JSON格式返回：不要有其他内容
{{
    "syntax_errors": [
        {{"message": "问题1描述", "severity": "error/warning"}},
        {{"message": "问题N描述", "severity": "error/warning"}}
    ],
    "logic_issues": [
        {{"message": "问题1描述", "severity": "error/warning"}},
        {{"message": "问题N描述", "severity": "error/warning"}}
    ],
    "suggestions": ["改进建议1", "改进建议N"],
    "score": 1-10分,
    "summary": "代码质量总结"
}}

【Patch 内容】
{self.patch_content}
"""

        return await llm_invoke(
            self.llm,
            [{"role": "user", "content": prompt}],
            json_schema=CODE_QUALITY_REVIEW_SCHEMA
        )

    async def review_architecture(self) -> Dict:
        arch_path = Path(app_base_dir) / "docs" / "architecture.md"
        logging.debug("Loading architecture document from: %s", arch_path)
        arch_doc = (
            arch_path.read_text(encoding="utf-8")
            if arch_path.exists()
            else "Standard Layered Architecture."
        )
        logging.debug("Architecture document loaded: %d characters", len(arch_doc))

        prompt = f"""审查 Patch 中新增文件的架构合规性：

【审查重点】
请仅从以下两个维度进行架构审查：
1. 新增文件的位置：文件放置的目录层级是否符合架构设计的分层结构和模块划分
2. 新增文件的功能：文件承担的职责是否符合架构设计中的职责边界和依赖关系

请忽略代码实现细节，仅关注架构层面的合规性。

【输出要求】
必须以如下JSON格式返回：
{{
    "compliant": true/false,
    "issues": ["问题1", "问题2"],
    "summary": "架构审查总结"
}}

【架构规范】
{arch_doc}

【Patch 内容】
{self.patch_content}
"""

        logging.debug("Invoking LLM for architecture review...")
        return await llm_invoke(
            self.llm,
            [{"role": "user", "content": prompt}],
            json_schema=ARCHITECTURE_REVIEW_SCHEMA
        )


def format_text_report(report: ReviewReport) -> str:
    lines = [
        "\n",
        "=" * 60,
        f"PATCH REVIEW REPORT | SCORE: {report.overall_score}/10",
        "=" * 60,
    ]
    lines.append(
        f"\nArchitecture: {'✅ OK' if report.architecture_compliant else '❌ VIOLATION'}"
    )

    if report.architecture_issues:
        for issue in report.architecture_issues:
            lines.append(f"  - {issue}")

    lines.append(f"\nSummary: {report.summary}")

    if report.syntax_errors:
        lines.append("\nSyntax Errors:")
        for i in report.syntax_errors:
            icon = "❌" if i.severity == "error" else "⚠️"
            line = f" {icon} [{i.category}] {i.message}"
            if i.suggestion:
                line += f" -> {i.suggestion}"
            lines.append(line)

    if report.logic_issues:
        lines.append("\nLogic Issues:")
        for i in report.logic_issues:
            icon = "❌" if i.severity == "error" else "⚠️"
            line = f" {icon} [{i.category}] {i.message}"
            if i.suggestion:
                line += f" -> {i.suggestion}"
            lines.append(line)

    if report.suggestions:
        lines.append("\nSuggestions:")
        for s in report.suggestions[:10]:
            lines.append(f" - {s}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


async def main_async():
    parser = argparse.ArgumentParser(description="Professional Patch Review Tool")
    parser.add_argument("patch_file", help="Path to patch")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--model", help="LLM Model")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--api-key", help="API Key")

    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s"
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s"
        )

    custom_llm = None
    if args.model or args.api_base or args.api_key:
        custom_llm = ChatOpenAI(
            model=args.model or settings.DEFAULT_LLM_MODEL,
            api_key=args.api_key or settings.DEFAULT_LLM_API_KEY,
            base_url=args.api_base or settings.DEFAULT_LLM_API_BASE_URL,
            timeout=600,
        )

    reviewer = PatchReviewer(args.patch_file, llm=custom_llm)
    report = await reviewer.run_review()
    logging.info(format_text_report(report))

    return 0 if report.architecture_compliant and report.overall_score > 6 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
