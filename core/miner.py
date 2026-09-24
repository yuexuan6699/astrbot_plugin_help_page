"""静态挖掘「没注册成指令、但会直接响应用户消息」的插件处理函数。

筛选分两步：

1. 事件类型必须是适配器消息事件，带正则或消息类型过滤器，且不含任何指令过滤器。
2. 兜底型 handler（`@filter.regex('.*', priority=1)` 这类匹配任意消息的写法）整段扫描
   函数体，逐个 `if` 分支拆成候选——分支的条件、上方注释与字符串字面量往往就是真实
   触发词；函数体内一个 `if` 都没有的，整体作为一个「非指令功能」候选。

命中后交给 AI 归纳触发词，由用户人工确认。
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from typing import Any

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.event_message_type import EventMessageTypeFilter
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.star import star_registry
from astrbot.core.star.star_handler import EventType, star_handlers_registry

MAX_PLUGINS = 12
"""单次挖掘分析的插件数量上限。"""

MAX_PER_HANDLER = 6
"""单个兜底 handler 最多拆出的 if 分支数量。"""

MAX_PER_PLUGIN = 16
"""单个插件最多贡献的候选函数数量。"""

MAX_TOTAL = 60
"""单次挖掘返回的候选函数总数上限。"""

MAX_LITERALS = 6
"""单个 if 条件里最多保留的字符串字面量数量。"""

CATCH_ALL_PATTERNS = {"", ".*", ".*?", "^.*$", "(?s).*", "(?s).*?"}
"""等同于「匹配任意消息」的正则写法，这类 handler 的触发条件藏在函数体里。"""

_IF_LINE = re.compile(r"^\s*if\s+(.+?)\s*:\s*(?:#\s*(.*))?$")


def _source_of(handler: Any) -> str:
    """取回处理函数的源码，失败时返回空串。"""
    try:
        return inspect.getsource(handler)
    except (OSError, TypeError):
        return ""


def _is_catch_all(regexes: list[str]) -> bool:
    """判断 handler 是否匹配任意消息（含只按消息类型过滤的情况）。"""
    if not regexes:
        return True
    return all(item.strip() in CATCH_ALL_PATTERNS for item in regexes)


def _string_literals(node: ast.AST) -> list[str]:
    """收集条件表达式里的字符串字面量，它们通常就是用户要发的触发词。"""
    found: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            text = child.value.strip()
            if text and text not in found:
                found.append(text)
    return found[:MAX_LITERALS]


def _leading_comment(lines: list[str], lineno: int) -> str:
    """取 if 语句上方紧邻的注释，注释常写着这块功能叫什么。"""
    index = lineno - 2
    collected: list[str] = []
    while index >= 0:
        text = lines[index].strip()
        if not text.startswith("#"):
            break
        stripped = text.lstrip("#").strip()
        if stripped:
            collected.append(stripped)
        index -= 1
    return " ".join(reversed(collected))


def _if_branches(source: str) -> list[dict[str, Any]]:
    """整段扫描函数源码，抽出每个 if 分支的条件、注释与字面量。

    Args:
        source: handler 的完整源码（含装饰器）。

    Returns:
        形如 [{"condition", "comment", "literals"}, ...]，按源码出现顺序去重。
    """
    body = textwrap.dedent(source)
    if not body.strip():
        return []
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        return _if_branches_by_regex(body)

    lines = body.splitlines()
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.If)]
    nodes.sort(key=lambda node: (node.lineno, node.col_offset))

    branches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        try:
            condition = ast.unparse(node.test).strip()
        except (AttributeError, ValueError):
            continue
        if not condition or condition in seen:
            continue
        seen.add(condition)
        branches.append(
            {
                "condition": condition,
                "comment": _leading_comment(lines, node.lineno),
                "literals": _string_literals(node.test),
            }
        )
    return branches


def _if_branches_by_regex(body: str) -> list[dict[str, Any]]:
    """ast 解析失败时的兜底：按行提取单行 if 条件。"""
    lines = body.splitlines()
    branches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = _IF_LINE.match(line)
        if not match:
            continue
        condition = match.group(1).strip()
        if not condition or condition in seen:
            continue
        seen.add(condition)
        literals = re.findall(r"['\"]([^'\"]{1,20})['\"]", condition)
        branches.append(
            {
                "condition": condition,
                "comment": _leading_comment(lines, index + 1),
                "literals": literals[:MAX_LITERALS],
            }
        )
    return branches


def _branch_candidates(
    base: dict[str, Any],
    branches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把一个兜底 handler 的 if 分支拆成多条候选。

    每条候选都带上整个函数的全部 if 条件，让 AI 在不截断源码的前提下看到全貌。
    """
    conditions = [branch["condition"] for branch in branches]
    picked: list[dict[str, Any]] = []
    for index, branch in enumerate(branches[:MAX_PER_HANDLER]):
        picked.append(
            {
                **base,
                "id": f"{base['handler']}#{index}",
                "branch": index,
                "kind": "command",
                "catch_all": True,
                "condition": branch["condition"],
                "comment": branch["comment"],
                "literals": branch["literals"],
                "conditions": conditions,
            }
        )
    return picked


def collect_candidates(
    registered_keys: set[str],
    ignored_plugins: set[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """收集疑似未注册但可用的消息处理函数。

    Args:
        registered_keys: 已注册为指令的 handler_full_name 集合，用于排除已收录项。
        ignored_plugins: 插件黑名单，名单内的插件整块跳过。

    Returns:
        (候选列表, 是否因数量上限被截断)。候选每项含 id、handler、plugin、group、
        kind、condition、comment、literals、conditions、regex、source 等字段。
    """
    ignored = {
        str(name).strip() for name in (ignored_plugins or set()) if str(name).strip()
    }
    candidates: list[dict[str, Any]] = []
    truncated = False

    for metadata in star_registry:
        if not metadata.activated or not metadata.module_path:
            continue

        plugin_name = metadata.name or metadata.module_path
        if plugin_name in ignored:
            continue

        picked: list[dict[str, Any]] = []
        for handler in star_handlers_registry.get_handlers_by_module_name(
            metadata.module_path
        ):
            if handler.handler_full_name in registered_keys:
                continue
            if handler.event_type is not EventType.AdapterMessageEvent:
                continue

            filters = handler.event_filters
            if any(
                isinstance(item, (CommandFilter, CommandGroupFilter))
                for item in filters
            ):
                continue

            regexes = [
                item.regex_str for item in filters if isinstance(item, RegexFilter)
            ]
            listens_all = any(
                isinstance(item, EventMessageTypeFilter) for item in filters
            )
            if not regexes and not listens_all:
                continue

            source = _source_of(handler.handler)
            base: dict[str, Any] = {
                "id": handler.handler_full_name,
                "handler": handler.handler_full_name,
                "handler_name": handler.handler_name,
                "plugin": plugin_name,
                "group": metadata.display_name or plugin_name,
                "doc": handler.desc or "",
                "regex": " | ".join(regexes),
                "source": source,
                "condition": "",
                "comment": "",
                "literals": [],
                "conditions": [],
                "branch": -1,
            }

            if _is_catch_all(regexes):
                branches = _if_branches(source)
                if branches:
                    if len(branches) > MAX_PER_HANDLER:
                        truncated = True
                    picked.extend(_branch_candidates(base, branches))
                    if len(picked) >= MAX_PER_PLUGIN:
                        picked = picked[:MAX_PER_PLUGIN]
                        truncated = True
                        break
                    continue
                # 兜底监听且函数体内没有 if：整体算一个非指令功能
                base["kind"] = "feature"
                base["catch_all"] = True
                picked.append(base)
            else:
                base["kind"] = "command"
                base["catch_all"] = False
                picked.append(base)

            if len(picked) >= MAX_PER_PLUGIN:
                truncated = True
                break

        if picked:
            candidates.extend(picked)
            if len({item["plugin"] for item in candidates}) >= MAX_PLUGINS:
                truncated = True
                break

    if len(candidates) > MAX_TOTAL:
        candidates = candidates[:MAX_TOTAL]
        truncated = True

    return candidates, truncated