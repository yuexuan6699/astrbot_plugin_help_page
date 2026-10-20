from __future__ import annotations

from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.core.star.command_management import list_commands

BUILTIN_PLUGINS = frozenset({"builtin_commands"})
"""AstrBot 自带指令所在的插件名（`astrbot/builtin_stars/builtin_commands`）。"""

BUILTIN_GROUP = "AstrBot 内置"
"""内置指令在帮助图里统一使用的分组名，避免直接显示 `builtin_commands`。"""

BUILTIN_HIDDEN_COMMANDS = frozenset({"help"})
"""内置指令里不纳入帮助的条目。

`/help` 本身就是这个帮助指令，列进帮助图会自相矛盾，因此始终排除；
AstrBot 自带 `/help` 的输出也把 help 排除在外，这里保持一致。
"""


def _flatten(items: list[dict[str, Any]], out: list[dict[str, Any]]) -> None:
    """把指令组与子指令展开成平铺列表。"""
    for item in items:
        out.append(item)
        _flatten(item.get("sub_commands") or [], out)


async def scan_entries(
    ignored_plugins: set[str] | None = None,
    include_builtin: bool = False,
) -> list[dict[str, Any]]:
    """扫描当前已注册且启用的全部插件指令。

    Args:
        ignored_plugins: 插件黑名单，名单内的插件整块跳过。
        include_builtin: 是否把 AstrBot 自带指令一并纳入。为 True 时
            `builtin_commands` 会覆盖黑名单（现网配置里默认就带着它，
            否则内置指令永远进不了帮助），并统一归入「AstrBot 内置」分组。

    Returns:
        平铺后的指令条目列表，每项包含 key、group、command、aliases 等字段。
    """
    ignored = {
        str(name).strip() for name in (ignored_plugins or set()) if str(name).strip()
    }
    raw: list[dict[str, Any]] = []
    _flatten(await list_commands(), raw)

    entries: list[dict[str, Any]] = []
    for item in raw:
        if not item.get("handler_full_name"):
            continue
        if not item.get("enabled") or not item.get("plugin_activated"):
            continue

        plugin = item.get("plugin") or ""
        is_builtin = plugin in BUILTIN_PLUGINS
        if is_builtin:
            if not include_builtin:
                continue
        elif plugin in ignored:
            continue

        command = item.get("effective_command") or ""
        if is_builtin and command.strip().lower() in BUILTIN_HIDDEN_COMMANDS:
            continue

        entries.append(
            {
                "key": item.get("handler_full_name") or "",
                "plugin": plugin,
                "group": (
                    BUILTIN_GROUP
                    if is_builtin
                    else (
                        item.get("plugin_display_name")
                        or item.get("plugin")
                        or "其他"
                    )
                ),
                "command": command,
                "aliases": item.get("aliases") or [],
                "description": item.get("description") or "",
                "permission": item.get("permission") or "everyone",
                "is_group": bool(item.get("is_group")),
                "reserved": bool(item.get("reserved")),
                "builtin": is_builtin,
                "kind": "command",
            }
        )
    return entries


def sort_entries(
    entries: list[dict[str, Any]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """按「分组顺序 → 组内手动顺序 → 指令名」对条目排序。

    分组顺序来自 state["groups"][name]["order"]，未配置的分组排在已配置分组之后；
    组内顺序来自 state["order"] 中的 key 位置，未列出的条目按指令名兜底。

    Args:
        entries: 待排序的条目。
        state: HelpStore.state。

    Returns:
        排序后的新列表。
    """
    order_index = {key: index for index, key in enumerate(state.get("order") or [])}
    group_meta = state.get("groups") or {}
    unlisted = len(order_index)

    def group_rank(name: str) -> tuple[int, int, str]:
        meta = group_meta.get(name)
        if isinstance(meta, dict):
            return (0, int(meta.get("order", 0)), name)
        return (1, 0, name)

    def entry_rank(entry: dict[str, Any]) -> tuple[int, int, str]:
        index = order_index.get(entry["key"])
        if index is None:
            return (1, unlisted, entry.get("command") or "")
        return (0, index, "")

    return sorted(
        entries,
        key=lambda entry: (group_rank(entry["group"]), entry_rank(entry)),
    )


def merge_entries(
    scanned: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """把自动扫描结果与手动配置合并成最终条目列表。

    插件黑名单已在 scan_entries() 阶段过滤，这里只处理隐藏、覆写、分组与排序。

    Args:
        scanned: scan_entries() 的输出。
        state: HelpStore.state。

    Returns:
        已应用隐藏、覆写、分组归属与手动排序，并追加自定义条目的条目列表。
    """
    hidden = set(state.get("hidden") or [])
    overrides = state.get("overrides") or {}

    entries: list[dict[str, Any]] = []
    for item in scanned:
        if item["key"] in hidden:
            continue
        entry = dict(item)
        entry["source"] = "auto"
        override = overrides.get(item["key"]) or {}
        if override.get("command"):
            entry["command"] = override["command"]
        if override.get("description") is not None:
            entry["description"] = override["description"]
        if override.get("group"):
            entry["group"] = override["group"]
        if override.get("kind") in {"command", "feature"}:
            entry["kind"] = override["kind"]
        if isinstance(override.get("aliases"), list):
            entry["aliases"] = override["aliases"]
        # 标注是逐条设置的：覆写里没写就保持缺省，渲染时回落到默认标注
        if isinstance(override.get("mark"), str):
            entry["mark"] = override["mark"]
        entries.append(entry)

    for custom in state.get("custom") or []:
        key = str(custom.get("id") or "")
        if key in hidden:
            continue
        override = overrides.get(key) or {}
        description = custom.get("description") or ""
        if override.get("description") is not None:
            description = override["description"]
        kind = str(custom.get("kind") or "command")
        entry: dict[str, Any] = {
            "key": key,
            "plugin": "",
            "group": override.get("group") or custom.get("group") or "自定义",
            "command": override.get("command") or custom.get("command") or "",
            "aliases": custom.get("aliases") or [],
            "description": description,
            "permission": custom.get("permission") or "everyone",
            "is_group": False,
            "reserved": False,
            "source": "custom",
            "kind": kind if kind in {"command", "feature"} else "command",
        }
        mark = custom.get("mark")
        if isinstance(override.get("mark"), str):
            mark = override["mark"]
        if isinstance(mark, str):
            entry["mark"] = mark
        entries.append(entry)

    return sort_entries(entries, state)


def permission_visible(permission: str, event: AstrMessageEvent) -> bool:
    """判断某权限的条目是否对当前发送者可见。

    与 AstrBot PermissionTypeFilter 的判定保持一致。

    Args:
        permission: 条目声明的权限。
        event: 当前消息事件。

    Returns:
        当前发送者是否有权看到该条目。
    """
    if permission == "admin":
        return event.is_admin()
    if permission == "group_admin":
        return event.is_admin() if event.get_group_id() else True
    if permission == "shared_group_admin":
        if not event.get_group_id():
            return True
        return event.is_admin() or bool(event.get_extra("_session_isolated", False))
    return True
