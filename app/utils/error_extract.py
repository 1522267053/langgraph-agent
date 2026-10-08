"""
LLM 错误消息提取工具

LLM 服务商（OpenAI 兼容接口）出错时，openai SDK 异常消息往往携带原始 SSE 帧
或完整 JSON 响应体，直接透出给用户是一大串无法阅读的文本。本模块负责从中
提取 error.message（附错误码）直接输出——只做提取，不做文案映射。

兼容三种形态：
1. SSE 帧:  data: {"error": {"code": "...", "message": "...", ...}}
2. SDK 格式: Error code: 400 - {"error": {...}}
3. 纯 JSON: {"error": {...}} 或 {"message": "..."}

降级策略：提取不到 error.message 时，原样输出错误体 JSON 本身
（剥掉 data:/Error code 等外壳噪声）；连 JSON 都没有时返回原文。
"""

import json
import re
from typing import Optional

# SSE data 帧或 SDK 前缀中嵌出的 JSON 对象（非贪婪，取第一个完整平衡块由 json 解析兜底）
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_objects(text: str) -> list[dict]:
    """从文本中提取所有可解析的 JSON 对象（按出现顺序）"""
    results: list[dict] = []
    for match in _JSON_OBJ_RE.finditer(text):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                results.append(obj)
        except (json.JSONDecodeError, ValueError):
            continue
    return results


def _message_from_error_payload(payload: dict) -> Optional[str]:
    """从 error 载荷提取「message（[code]）」"""
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    code = error.get("code") or error.get("type")
    if isinstance(code, str) and code and code not in message:
        return f"{message.strip()}（错误码: {code}）"
    return message.strip()


def extract_llm_error(raw: str) -> str:
    """从 LLM 异常消息中提取可读错误（error.message + 错误码）

    降级链：error.message → 错误体 JSON 原样（剥外壳）→ 原文。
    """
    text = (raw or "").strip()
    if not text:
        return raw

    candidates: list[dict] = []
    # 优先 SSE data 帧（可能多帧）
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            candidates.extend(_extract_json_objects(line[5:]))
    # 兜底：全文任意位置的 JSON 对象
    candidates.extend(_extract_json_objects(text))

    for payload in candidates:
        message = _message_from_error_payload(payload)
        if message:
            return message

    # 找不到 error.message：原样输出 JSON 本体（剥掉 data:/Error code 等外壳）
    if candidates:
        return json.dumps(candidates[0], ensure_ascii=False)
    return text
