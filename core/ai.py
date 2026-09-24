"""AI 文案助手：指令说明的翻译/润色，以及未注册功能的归纳。

只通过 AstrBot 的 `context.llm_generate` 调用对话模型，不自行发起网络请求。
模型输出统一按 JSON 解析，解析失败时由调用方降级处理。
"""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context

MAX_ITEMS = 60
"""单次翻译/润色提交的条目上限，避免提示词过长。"""

MAX_DESCRIPTION = 200
"""单条说明参与处理的最大字符数。"""

MAX_SOURCE = 1500
"""单个 handler 源码片段参与分析的最大字符数。"""

MAX_CANDIDATES = 60
"""单次挖掘提交给模型的候选 handler 上限，与 miner.MAX_TOTAL 对齐。"""

MAX_CONDITIONS = 8
"""单个候选随附的 if 条件数量上限，用来在不截断源码的前提下呈现函数全貌。"""

MAX_CONDITION_LEN = 120
"""单条 if 条件的最大字符数。"""

MAX_COMMENT = 80
"""if 上方注释的最大字符数。"""

MAX_LITERALS = 6
"""单个候选保留的字符串字面量数量上限。"""

KINDS = {"command", "feature"}
"""条目类型：真实指令，或没有触发词的非指令功能。"""


class AIUnavailableError(RuntimeError):
    """当前没有可用于文案处理的对话模型。"""


def _extract_json_array(text: str) -> list[Any] | None:
    """从模型输出中提取 JSON 数组，容忍 Markdown 代码块与前后缀说明。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


async def resolve_provider_id(context: Context, configured: str = "") -> str:
    """确定用于文案处理的模型 Provider ID。

    Args:
        context: 插件上下文。
        configured: 插件配置中指定的 Provider ID，为空时使用当前默认模型。

    Returns:
        可用的 Provider ID。

    Raises:
        AIUnavailableError: 既没有指定 Provider，也没有默认对话模型。
    """
    configured = (configured or "").strip()
    if configured:
        return configured
    provider = await context.get_using_provider_async()
    if provider is None:
        raise AIUnavailableError("没有可用的对话模型，请先在 AstrBot 中配置模型提供商")
    return provider.meta().id


async def _generate(context: Context, provider_id: str, prompt: str) -> str:
    """调用模型并返回纯文本结果。"""
    response = await context.llm_generate(
        chat_provider_id=provider_id,
        prompt=prompt,
        system_prompt=(
            "你是 AstrBot 插件帮助页的文案助手，只输出被要求的 JSON，"
            "不要输出任何解释、客套话或 Markdown 代码块标记。"
        ),
    )
    return response.completion_text or ""


def _build_polish_prompt(items: list[dict[str, str]], mode: str) -> str:
    """构造翻译/润色提示词。"""
    if mode == "polish":
        task = (
            "请把每项的 description 润色成简洁、易懂的简体中文说明。"
            "如果原文是英文或其他语言，先翻译成简体中文再润色。"
        )
    else:
        task = "请把每项的 description 统一翻译成简体中文，并顺带润色得更简洁易懂。"
    payload = [
        {
            "key": item["key"],
            "command": item.get("command") or "",
            "description": (item.get("description") or "")[:MAX_DESCRIPTION],
        }
        for item in items
    ]
    return (
        f"{task}\n"
        "要求：\n"
        "1. 每条说明不超过 40 个字，不重复指令名本身，不添加原文没有的功能。\n"
        "2. 保留原文中的参数名、占位符（如 xxx、<name>）和专有名词。\n"
        "3. key 必须与输入完全一致，不得新增、遗漏或修改顺序。\n"
        "4. 原文为空时输出空字符串。\n"
        "5. 只输出一个 JSON 数组，每项形如 "
        '{"key": "原样返回", "description": "处理后的中文说明"}。\n'
        f"输入：\n{json.dumps(payload, ensure_ascii=False)}"
    )


async def polish_entries(
    context: Context,
    provider_id: str,
    items: list[dict[str, str]],
    mode: str = "translate",
) -> dict[str, str]:
    """把若干条指令说明翻译或润色成简体中文。

    Args:
        context: 插件上下文。
        provider_id: 使用的模型 Provider ID。
        items: 形如 [{"key", "command", "description"}, ...] 的条目列表。
        mode: `translate`（英译中）或 `polish`（直接润色）。

    Returns:
        key 到处理后说明的映射；模型输出无法解析时为空字典。

    Raises:
        AIUnavailableError: 条目为空。
    """
    cleaned = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    ][:MAX_ITEMS]
    if not cleaned:
        raise AIUnavailableError("没有需要处理的条目")

    text = await _generate(context, provider_id, _build_polish_prompt(cleaned, mode))
    parsed = _extract_json_array(text)
    if parsed is None:
        logger.warning("[help_page] AI 文案处理返回内容无法解析为 JSON")
        return {}

    allowed = {item["key"] for item in cleaned}
    result: dict[str, str] = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        if key not in allowed:
            continue
        description = entry.get("description")
        if description is None:
            continue
        result[key] = str(description).strip()
    return result


def _trim_list(values: Any, limit: int) -> list[str]:
    """把候选里的字符串列表裁剪成提示词可接受的规模。"""
    if not isinstance(values, list):
        return []
    return [str(item)[:MAX_CONDITION_LEN] for item in values[:limit]]


def _build_candidate_prompt(candidates: list[dict[str, Any]]) -> str:
    """构造未注册功能归纳的提示词。"""
    payload: list[dict[str, Any]] = []
    for item in candidates:
        entry: dict[str, Any] = {
            "id": item["id"],
            "plugin": item["plugin"],
            "kind": item.get("kind") or "command",
            "doc": (item.get("doc") or "")[:MAX_DESCRIPTION],
            "regex": item.get("regex") or "",
            "condition": (item.get("condition") or "")[:MAX_CONDITION_LEN],
            "comment": (item.get("comment") or "")[:MAX_COMMENT],
            "literals": _trim_list(item.get("literals"), MAX_LITERALS),
            "conditions": _trim_list(item.get("conditions"), MAX_CONDITIONS),
        }
        # 已拆出 if 分支的候选靠 condition/conditions 还原全貌，无需再塞整段源码；
        # 整函数候选没有分支信息，才回退到源码片段。
        if not entry["condition"]:
            entry["source"] = (item.get("source") or "")[:MAX_SOURCE]
        payload.append(entry)

    return (
        "下面是若干 AstrBot 插件里「没有注册成指令、但会直接响应用户消息」的处理函数，"
        "以及从兜底监听函数（@filter.regex('.*') 这类匹配任意消息的写法）里拆出来的 if 分支。\n"
        "兜底监听的函数，真实触发条件就藏在函数体的 if 里，已逐条拆开；"
        "conditions 字段是该函数全部 if 条件的清单，供你判断整体用途。\n"
        "请为每一项推断用户在聊天里实际要发的触发词，并写一条中文说明。\n"
        "要求：\n"
        "1. 只输出一个 JSON 数组，每项形如 "
        '{"id": "原样返回", "command": "触发词", "aliases": ["别名"], '
        '"description": "中文说明", "kind": "command|feature", '
        '"confidence": "high|medium|low"}。\n'
        "2. 触发词优先取 literals 里出现过的字符串；没有字面量时取 comment 描述的功能名；"
        "两者都没有才依据 condition 与 doc 推断。不要臆造源码里没有的功能。\n"
        "3. condition 只是内部判断（权限校验、空值判断、前缀判断、日志分支等），"
        "或无法还原出用户可发送的触发词时，把 kind 标为 feature，或把 confidence 标为 low。\n"
        "4. 能由用户直接触发、有明确触发词的标 kind = command。\n"
        "5. command 不带斜杠、不带空格；aliases 可为空数组；description 不超过 40 个字。\n"
        "6. id 必须与输入完全一致，不得新增、遗漏或修改顺序。\n"
        f"输入：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _fallback_name(origin: dict[str, Any]) -> str:
    """模型没给出触发词时，用函数说明或函数名兜底，保证条目标签非空。"""
    doc = str(origin.get("doc") or "").strip().splitlines()
    if doc and doc[0].strip():
        return doc[0].strip()[:20]
    return str(origin.get("handler_name") or "").strip()[:20]


async def describe_candidates(
    context: Context,
    provider_id: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """让模型归纳未注册功能的触发词与说明。

    Args:
        context: 插件上下文。
        provider_id: 使用的模型 Provider ID。
        candidates: miner.collect_candidates() 的输出。

    Returns:
        形如 [{"id", "handler", "plugin", "command", "aliases", "description",
        "kind", "confidence"}]，仅保留模型给出且能对应回原始候选的项。
    """
    limited = candidates[:MAX_CANDIDATES]
    if not limited:
        return []

    text = await _generate(context, provider_id, _build_candidate_prompt(limited))
    parsed = _extract_json_array(text)
    if parsed is None:
        logger.warning("[help_page] AI 挖掘返回内容无法解析为 JSON")
        return []

    index = {str(item["id"]): item for item in limited}
    results: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        candidate_id = str(entry.get("id") or "")
        origin = index.get(candidate_id)
        if origin is None:
            continue

        kind = str(entry.get("kind") or "").strip().lower()
        if kind not in KINDS:
            kind = str(origin.get("kind") or "command")
        if kind not in KINDS:
            kind = "command"

        command = str(entry.get("command") or "").strip().lstrip("/")
        if not command:
            command = _fallback_name(origin)
        if not command:
            continue

        aliases = [
            str(alias).strip()
            for alias in entry.get("aliases") or []
            if isinstance(alias, str) and str(alias).strip()
        ]
        confidence = str(entry.get("confidence") or "medium").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        results.append(
            {
                "id": candidate_id,
                "handler": origin["handler"],
                "plugin": origin["plugin"],
                "group": origin.get("group") or origin["plugin"],
                "command": command,
                "aliases": aliases,
                "description": str(entry.get("description") or "").strip(),
                "kind": kind,
                "confidence": confidence,
                "regex": origin.get("regex") or "",
                "condition": origin.get("condition") or "",
                "comment": origin.get("comment") or "",
            }
        )
    return results
