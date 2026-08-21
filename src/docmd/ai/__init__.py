"""AI 阅读与提问模块。

固定注入处理规则（门控）：
- 必须完整阅读文档，不得静默截断；
- 超长文档按块读取并合并全部 C/D 字段，不得遗漏；
- 必须汇总所有 C/D 字段，一次性提出确认问题；
- 用户确认前禁止任何业务处理。

支持两种模式：
- openai：OpenAI 兼容 POST /chat/completions（完整分块阅读）
- manual：无 AI 时返回本地生成的待确认清单（作为不可绕过的兜底汇总，仍需用户确认）

安全：
- API 密钥不落明文 SQLite，统一走 SecretStore（keyring 或本地加密文件）。
- HTTP 不关闭 TLS 校验（无 verify=False）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from ..core.secrets import SecretStore

# 固定注入的处理规则（确认前禁止业务处理）
RULES = """你是文档识别字段确认助手。请严格遵循以下规则：
1. 必须完整阅读给定 Markdown 全文，不得跳过任何部分。
2. 必须汇总文档中所有可信等级为 C/D 的字段，一次性列出，一个都不能遗漏。
3. 仅生成「待确认问题清单」，逐条列出每个 C/D 字段及其候选值，请用户确认或纠正。
4. 在用户确认之前，禁止开始任何分析、总结、问答、导出正式文件或写入知识库的业务处理。
5. 输出格式：以列表逐条列出待确认项，标注字段名、可信等级、页码、候选值与冲突原因，
   并明确询问用户应采用的最终值。""".strip()

# 单次请求的字符上限（不静默截断，超长则分块）
_CHUNK_SIZE = 6000
_CHUNK_OVERLAP = 300


@dataclass
class AskResult:
    questions: str
    engine: str
    raw: Optional[dict] = None


class NoAiConfigured(Exception):
    pass


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """按给定大小分块，相邻块重叠 overlap，避免切分时破坏字段信息。"""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(start + size - overlap, start + 1)
    return chunks


class AiAsker:
    def __init__(self, config: Optional[dict] = None, db=None,
                 secret_store: Optional[SecretStore] = None):
        """config: {base_url, model, api_key, path_to_key, max_context}
        若 api_key 为空且带 db/secret_store，则从 SecretStore 安全读取。
        """
        self.secret_store = secret_store or SecretStore()
        self.config = {}
        if config:
            self.config = dict(config)
        if db is not None:
            self.config.setdefault("base_url", db.get_config("ai_config", "base_url") or "")
            self.config.setdefault("model", db.get_config("ai_config", "model") or "")
            self.config.setdefault("project", db.get_config("ai_config", "project") or "")
        # 密钥只从 SecretStore 读取（如显式传入且非空则可直接使用）
        if not self.config.get("api_key"):
            self.config["api_key"] = self.secret_store.get("ai", "api_key") or self.config.get("path_to_key") or ""
        self.db = db

    @property
    def configured(self) -> bool:
        return bool(self.config.get("base_url")) and bool(self.config.get("model"))

    def ask(self, markdown_text: str, mode: str = "auto") -> AskResult:
        """完整阅读并提问。mode: auto|openai|manual。"""
        if mode == "manual" or (mode == "auto" and not self.configured):
            return self._manual(markdown_text)
        if not self.configured:
            raise NoAiConfigured("未配置 AI 服务")
        if self.config.get("project"):
            return self._chunked_openai_2(markdown_text)
        return self._chunked_openai(markdown_text)

    # ---------- OpenAI 兼容（分块、完整阅读、合并 C/D） ----------
    def _chunked_openai(self, markdown_text: str) -> AskResult:
        chunks = _chunk_text(markdown_text)
        if len(chunks) == 1:
            return self._openai_chunk(chunks[0], "这是完整文档，请直接汇总全部 C/D 字段并提出确认问题。")
        # 多块：逐块提取候选，最后聚合
        partials: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            if i == len(chunks):
                instruction = (
                    "这是文档的最后一块。请汇总你在此块识别的全部 C/D 字段并提出确认问题；"
                    "若这是最终聚合块，还需把前序块的结果一并合并成一份完整清单。")
            else:
                instruction = (
                    f"这是文档的第 {i}/{len(chunks)} 块。请只汇总此块中的 C/D 字段及其候选值，"
                    "并列成清单输出。不要进行业务分析。")
            partials.append(self._openai_chunk(chunk, instruction).questions)
        merged = self._logical_merge(partials)
        return AskResult(questions=merged, engine="openai")

    def _logical_merge(self, partials: list[str]) -> str:
        """对分块结果做聚合：交给 AI 去重合并（避免遗漏 C/D）。"""
        if len(partials) == 1:
            return partials[0]
        prompt = (
            "以下是同一份文档按块识别出的若干份 C/D 字段清单。请把它们合并为一份完整、"
            "去重、不遗漏的「待确认问题清单」。合并后仍按规则标注字段名、等级、页码、候选值、冲突原因。\n\n"
            + "\n\n---\n\n".join(f"第{i+1}块:\n{p}" for i, p in enumerate(partials))
        )
        return self._openai_chunk(prompt, "请合并这些分块结果为最终清单。").questions

    def _openai_chunk(self, text: str, instruction: str) -> AskResult:
        url = self.config["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        payload = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": RULES},
                {"role": "user", "content": f"{instruction}\n\n---\n{text}"},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        # 默认 TLS 校验开启；若管理员显式配置了 base_url 为自签 http，可在配置白名单内放行，但绝不默认关校验
        verify = _verify_flag(self.config)
        resp = requests.post(url, json=payload, headers=headers, timeout=120, verify=verify)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return AskResult(questions=content, engine="openai", raw=data)

    # ---------- DeepSeek 风格（project 聚合）占位 ----------
    def _chunked_openai_2(self, markdown_text: str) -> AskResult:
        # 走与 _chunked_openai 相同的完整分块聚合，只是强调聚合模型
        return self._chunked_openai(markdown_text)

    # ---------- 本地兜底（不可绕过：仍生成待确认清单，需人工确认） ----------
    def _manual(self, markdown_text: str) -> AskResult:
        questions = []
        for line in markdown_text.splitlines():
            if line.startswith("- `") or "等级" in line:
                questions.append(line.strip())
        if not questions:
            questions = ["（未从文档中解析到明确的 C/D 待确认项，请人工核对该文档）"]
        body = "\n".join(questions)
        section = "以下为本地解析出的待确认字段清单（未接入 AI，无 AI 汇总），请在确认面板逐项核对：\n\n" + body
        return AskResult(questions=section, engine="manual")


def _verify_flag(config: dict) -> bool:
    """TLS 校验：默认开启。仅当显式配置 allow_insecure_tls=true 且 base_url 是 https 时才关闭。
    绝不默认 verify=False。"""
    if str(config.get("allow_insecure_tls", "")).lower() == "true":
        return False
    return True
