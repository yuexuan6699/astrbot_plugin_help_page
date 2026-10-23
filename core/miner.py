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
import hashlib
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

MAX_PLUGINS = 16
"""单次挖掘分析的插件数量上限。"""

MAX_PER_HANDLER = 12
"""单个兜底 handler 最多拆出的 if 分支数量。"""

MAX_PER_PLUGIN = 24
"""单个插件最多贡献的候选函数数量。"""

MAX_TOTAL = 72
"""单次挖掘返回的候选函数总数上限。"""

MAX_LITERALS = 6
"""单个 if 条件里最多保留的字符串字面量数量。"""

MAX_SNIPPET = 160
"""单个 if 分支体参与分析的源码片段长度，用来给模型判断分支到底在干什么。"""

CATCH_ALL_PATTERNS = {
    "",
    ".",
    ".*",
    ".+",
    ".*?",
    ".+?",
    "[\\s\\S]*",
    "[\\s\\S]+",
    "[\\s\\S]*?",
    "[^]*",
}
"""去掉内联标志与首尾锚点后，等同于「匹配任意消息」的正则写法。

这类 handler 的触发条件不在装饰器上，而藏在函数体的 if 里，需要逐条拆开。
"""

_INLINE_FLAGS = re.compile(r"\(\?[aiLmsux]+\)")
"""正则内联标志，如 `(?s)`、`(?is)`，判断是否为兜底监听前要先剥掉。"""

_TRIGGER_HINTS = (
    "message_str",
    "plain_text",
    "raw_message",
    "text",
    "msg",
    "content",
    "startswith",
    "endswith",
    "match",
    "in ",
    "==",
)
"""条件里出现这些痕迹，说明这一支在拿用户发的原文做判断，通常就是真实触发词。"""

_GUARD_HINTS = (
    "admin",
    "permission",
    "sender_id",
    "group_id",
    "role",
    "isinstance",
    "len(",
    "is none",
    "is not none",
    "enabled",
    "logger",
    "debug",
)
"""条件里出现这些痕迹，多半是权限、空值一类的守卫分支，不是用户可触发的功能。"""

_TERMINAL = (ast.Return, ast.Continue, ast.Break, ast.Pass, ast.Raise)
"""分支体只有这类语句时，这个 if 是个提前返回的守卫，不是功能入口。"""

_IF_LINE = re.compile(r"^\s*if\s+(.+?)\s*:\s*(?:#\s*(.*))?$")


def _source_of(handler: Any) -> str:
    """取回处理函数的源码，失败时返回空串。"""
    try:
        return inspect.getsource(handler)
    except (OSError, TypeError):
        return ""


def _is_wildcard(pattern: str) -> bool:
    r"""判断单条正则是否等于「匹配任意消息」。

    先把内联标志、非捕获分组括号、首尾锚点与空白去掉，再比对白名单。
    `(?s).*`、`^[\s\S]*$`、`.+` 这些写法相当常见，靠精确字符串比对会全部漏判。
    """
    text = _INLINE_FLAGS.sub("", str(pattern or "")).strip()
    text = re.sub(r"\s+", "", text)
    text = text.strip("^$")
    text = text.removeprefix("\\A").removesuffix("\\Z").strip("^$")
    if text.startswith("(?:"):
        text = text[3:-1] if text.endswith(")") else text[3:]
    if text in CATCH_ALL_PATTERNS:
        return True
    # 整串包一层分组也是常见写法，例如 `(.+)`、`(.*)`
    if text.startswith("(") and text.endswith(")"):
        return text[1:-1] in CATCH_ALL_PATTERNS
    return False


def _is_catch_all(regexes: list[str]) -> bool:
    """判断 handler 是否匹配任意消息（含只按消息类型过滤的情况）。"""
    if not regexes:
        return True
    return all(_is_wildcard(item) for item in regexes)


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


def _body_snippet(node: ast.If) -> str:
    """取 if 分支体的前几条语句，让模型看清这一支到底在做什么。"""
    parts: list[str] = []
    for statement in node.body[:2]:
        try:
            parts.append(ast.unparse(statement))
        except (AttributeError, ValueError):
            continue
    return " ".join(parts).strip()[:MAX_SNIPPET]


def _is_guard(node: ast.If, literals: list[str]) -> bool:
    """判断 if 是否只是提前返回/跳过，这种分支不是功能入口。"""
    if literals:
        return False
    body = node.body
    return len(body) == 1 and isinstance(body[0], _TERMINAL)


def _branch_score(branch: dict[str, Any]) -> int:
    """给 if 分支打分：越像「用户发某句话就触发」，分越高。

    兜底 handler 里通常前面挂着一堆权限、空值守卫，真实的触发分支排在后面。
    按分数排序后截断，才能保证截掉的是噪声而不是有用分支。
    """
    condition = str(branch.get("condition") or "").lower()
    score = 0
    if branch.get("literals"):
        score += 4
    if branch.get("comment"):
        score += 2
    if any(hint in condition for hint in _TRIGGER_HINTS):
        score += 2
    if any(hint in condition for hint in _GUARD_HINTS):
        score -= 2
    return score


def _candidate_score(item: dict[str, Any]) -> int:
    """给整条候选打分，用于跨插件截断时保留最像真实功能的那批。

    兜底拆出来的分支沿用分支分数；带限定正则的 handler 本身就是窄匹配，
    触发条件写在正则里，比只监听全部消息的兜底 handler 更可信，额外加分。
    """
    score = _branch_score(item)
    if item.get("regex") and not item.get("catch_all"):
        score += 3
    if item.get("kind") == "feature":
        score -= 1
    return score


def _if_branches(source: str) -> list[dict[str, Any]]:
    """整段扫描函数源码，抽出每个 if 分支的条件、注释、字面量与分支体。

    Args:
        source: handler 的完整源码（含装饰器）。

    Returns:
        形如 [{"condition", "comment", "literals", "snippet"}, ...]，
        已滤掉守卫分支并按「像真实触发词」的程度从高到低排序。
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

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        try:
            condition = ast.unparse(node.test).strip()
        except (AttributeError, ValueError):
            continue
        if not condition or condition in seen:
            continue
        seen.add(condition)
        literals = _string_literals(node.test)
        if _is_guard(node, literals):
            continue
        collected.append(
            {
                "condition": condition,
                "comment": _leading_comment(lines, node.lineno),
                "literals": literals,
                "snippet": _body_snippet(node),
            }
        )

    collected.sort(key=lambda item: -_branch_score(item))
    return collected


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
                "snippet": "",
            }
        )
    branches.sort(key=lambda item: -_branch_score(item))
    return branches


def _branch_id(handler: str, condition: str) -> str:
    """分支 id 由 handler 与条件内容共同决定。

    以前用分支序号当 id，函数里多一个守卫或排序一变，同一个 id 就指向了别的分支，
    废弃记录会误伤新候选、废弃过的噪声又会重新冒出来。改成条件摘要后，
    只要条件本身没变，废弃记录就一直对得上。
    """
    digest = hashlib.sha1(condition.encode("utf-8")).hexdigest()[:8]
    return f"{handler}#{digest}"


def _branch_candidates(
    base: dict[str, Any],
    branches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把一个兜底 handler 的 if 分支拆成多条候选。

    每条候选都带上整个函数的全部 if 条件，让 AI 在不截断源码的前提下看到全貌；
    分支体片段一并带上，模型才能判断这一支是「用户发某句话」还是内部逻辑。
    """
    conditions = [branch["condition"] for branch in branches]
    picked: list[dict[str, Any]] = []
    for index, branch in enumerate(branches[:MAX_PER_HANDLER]):
        picked.append(
            {
                **base,
                "id": _branch_id(base["handler"], branch["condition"]),
                "branch": index,
                "kind": "command",
                "catch_all": True,
                "condition": branch["condition"],
                "comment": branch["comment"],
                "literals": branch["literals"],
                "snippet": branch.get("snippet") or "",
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
    per_plugin: list[tuple[int, list[dict[str, Any]]]] = []
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
                "snippet": "",
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
            best = max(_candidate_score(item) for item in picked)
            per_plugin.append((best, picked))

    # 插件装载顺序是任意的，先按「这个插件里最像触发词的分支」排序再截断，
    # 否则噪声多的插件先占满名额，真正有功能的插件一次都扫不到。
    per_plugin.sort(key=lambda item: -item[0])
    if len(per_plugin) > MAX_PLUGINS:
        truncated = True
    candidates = [
        candidate for _, picked in per_plugin[:MAX_PLUGINS] for candidate in picked
    ]

    # 全局也按分数排序后截断：被丢掉的是最不像触发词的那批，而不是排在后面的插件
    candidates.sort(key=_candidate_score, reverse=True)
    if len(candidates) > MAX_TOTAL:
        candidates = candidates[:MAX_TOTAL]
        truncated = True

    return candidates, truncated