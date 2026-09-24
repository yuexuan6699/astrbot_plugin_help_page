"""帮助配置持久化：文本内容、画布内容与挖掘记录分开落盘。

- `help_content.json`：条目、分组、隐藏、覆写、手动排序（对应插件页「内容调整」页签）。
- `help_canvas.json`：外观参数与页面界面偏好（对应插件页「画布调整」页签）。
- `help_mining.json`：AI 挖掘结果与废弃堆，记录扫到过什么、哪些被否决过。

旧版单文件 `help_data.json` 只在三个新文件都不存在时读取一次用于迁移，
迁移后原样保留作为备份，之后不再参与读写。
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .help_image import Theme

PLUGIN_NAME = "astrbot_plugin_help_page"

CONTENT_FILE = "help_content.json"
"""文本内容文件名。"""

CANVAS_FILE = "help_canvas.json"
"""画布内容文件名。"""

MINING_FILE = "help_mining.json"
"""AI 挖掘记录文件名：上次扫到的候选与用户否决过的废弃堆。"""

LEGACY_FILE = "help_data.json"
"""旧版单文件配置，仅用于首次迁移。"""

CONTENT_FIELDS = ("hidden", "overrides", "custom", "groups", "order")
"""写入文本内容文件的字段。"""

CANVAS_FIELDS = ("appearance", "ui")
"""写入画布内容文件的字段。"""

MINING_FIELDS = ("mining", "dismissed", "mine_meta")
"""写入挖掘记录文件的字段。"""

PERMISSION_OPTIONS = [
    "everyone",
    "member",
    "group_admin",
    "shared_group_admin",
    "admin",
]
"""可选权限，与 AstrBot 指令权限语义一致。"""

KIND_OPTIONS = ["command", "feature"]
"""条目类型：真实指令，或 AI 挖掘出的、没有触发词的非指令功能。"""

UI_TABS = ["content", "canvas"]
"""插件页左栏的页签：内容调整、画布调整。"""

DEFAULT_STATE: dict[str, Any] = {
    "hidden": [],
    "overrides": {},
    "custom": [],
    "appearance": {},
    "groups": {},
    "order": [],
    "ui": {},
    "mining": [],
    "dismissed": [],
    "mine_meta": {},
}
"""帮助页可持久化的全部手动配置。"""


class HelpStore:
    """帮助条目的手动配置（隐藏、覆写、排序、外观、自定义条目）持久化存储。"""

    def __init__(self) -> None:
        directory = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.directory = directory
        self.content_path = directory / CONTENT_FILE
        self.canvas_path = directory / CANVAS_FILE
        self.mining_path = directory / MINING_FILE
        self.legacy_path = directory / LEGACY_FILE
        self.state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """读取三个配置文件；只有旧文件时迁移一次，缺失或损坏时回退到默认配置。"""
        content = _read_json(self.content_path)
        canvas = _read_json(self.canvas_path)
        mining = _read_json(self.mining_path)
        if content is None and canvas is None and mining is None:
            legacy = _read_json(self.legacy_path)
            if legacy is not None:
                state = normalize_state(legacy)
                self._write(state)
                logger.info(
                    "[help_page] 已把 help_data.json 拆分为 "
                    f"{CONTENT_FILE} / {CANVAS_FILE} / {MINING_FILE}，"
                    "原文件保留为备份"
                )
                return state
        return normalize_state(
            {**(content or {}), **(canvas or {}), **(mining or {})}
        )

    def save(self, state: dict[str, Any]) -> None:
        """保存完整的手动配置，按内容/画布/挖掘记录分别写入三个文件。

        Args:
            state: 页面提交的配置，字段与 DEFAULT_STATE 一致。
        """
        self.state = normalize_state(state)
        self._write(self.state)

    def reset(self) -> None:
        """清空全部手动配置，恢复为自动扫描结果。"""
        self.save(_default_state())

    def appearance(self, defaults: dict[str, Any] | None = None) -> Theme:
        """返回当前生效的外观配置。

        Args:
            defaults: 来自插件配置项的兜底外观值，仅在用户未在插件页改过对应项时生效。

        Returns:
            合并后的主题对象。
        """
        return merge_appearance(defaults, self.state.get("appearance"))

    def _write(self, state: dict[str, Any]) -> None:
        """把规整后的配置拆成三份写盘。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_json(
            self.content_path, {name: state[name] for name in CONTENT_FIELDS}
        )
        _write_json(self.canvas_path, {name: state[name] for name in CANVAS_FIELDS})
        _write_json(self.mining_path, {name: state[name] for name in MINING_FIELDS})


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取单个配置文件，缺失或损坏时返回 None 而不是抛错。"""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[help_page] 读取 {path.name} 失败，忽略该文件: {e}")
        return None
    return raw if isinstance(raw, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """把配置写成带缩进的 UTF-8 JSON。"""
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_appearance(
    defaults: dict[str, Any] | None,
    appearance: Any,
) -> Theme:
    """把插件配置的兜底外观与用户保存的外观合并成主题。

    Args:
        defaults: 来自插件配置项的兜底值。
        appearance: 用户保存的外观覆盖项。

    Returns:
        校验后的主题对象。
    """
    data = dict(defaults or {})
    data.update(appearance or {})
    return Theme.from_dict(data)


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """校验并规整配置结构，丢弃非法字段。

    插件页提交的预览配置与落盘配置共用这一套校验，保证「所见即所存」。

    Args:
        state: 页面提交的原始配置。

    Returns:
        字段齐全、取值范围合法的配置副本。
    """
    if not isinstance(state, dict):
        return _default_state()

    hidden = [
        key.strip()
        for key in state.get("hidden") or []
        if isinstance(key, str) and key.strip()
    ]

    overrides: dict[str, dict[str, Any]] = {}
    raw_overrides = state.get("overrides") or {}
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            if not isinstance(value, dict):
                continue
            entry: dict[str, Any] = {}
            command = str(value.get("command") or "").strip()
            if command:
                entry["command"] = command
            if value.get("description") is not None:
                entry["description"] = str(value["description"])
            group = str(value.get("group") or "").strip()
            if group:
                entry["group"] = group
            kind = str(value.get("kind") or "")
            if kind in KIND_OPTIONS:
                entry["kind"] = kind
            aliases = value.get("aliases")
            if isinstance(aliases, list):
                # 空列表是有效覆写（用户主动清掉了全部别名），必须原样保留
                entry["aliases"] = [
                    alias.strip()
                    for alias in aliases
                    if isinstance(alias, str) and alias.strip()
                ]
            mark = value.get("mark")
            if isinstance(mark, str):
                # 空串同样有效（这条不标注），要和「没单独设过」区分开，所以原样保留
                entry["mark"] = mark.strip()
            if entry:
                overrides[str(key)] = entry

    custom: list[dict[str, Any]] = []
    for value in state.get("custom") or []:
        if not isinstance(value, dict):
            continue
        command = str(value.get("command") or "").strip()
        if not command:
            continue
        permission = str(value.get("permission") or "everyone")
        if permission not in PERMISSION_OPTIONS:
            permission = "everyone"
        kind = str(value.get("kind") or "command")
        if kind not in KIND_OPTIONS:
            kind = "command"
        item: dict[str, Any] = {
            "id": str(value.get("id") or f"c{uuid4().hex[:8]}"),
            "group": str(value.get("group") or "自定义").strip() or "自定义",
            "command": command,
            "aliases": [
                alias.strip()
                for alias in value.get("aliases") or []
                if isinstance(alias, str) and alias.strip()
            ],
            "description": str(value.get("description") or ""),
            "permission": permission,
            "kind": kind,
        }
        # 只有单独设过的条目才带 mark，没设过的保持缺省以沿用默认标注
        mark = value.get("mark")
        if isinstance(mark, str):
            item["mark"] = mark.strip()
        custom.append(item)

    groups: dict[str, dict[str, Any]] = {}
    raw_groups = state.get("groups") or {}
    if isinstance(raw_groups, dict):
        for name, value in raw_groups.items():
            clean_name = str(name).strip()
            if not clean_name or not isinstance(value, dict):
                continue
            try:
                order = int(value.get("order", 0))
            except (TypeError, ValueError):
                order = 0
            groups[clean_name] = {
                "description": str(value.get("description") or ""),
                "order": max(0, min(9999, order)),
            }

    order: list[str] = []
    seen: set[str] = set()
    for key in state.get("order") or []:
        clean_key = str(key).strip()
        if clean_key and clean_key not in seen:
            seen.add(clean_key)
            order.append(clean_key)

    return {
        "hidden": hidden,
        "overrides": overrides,
        "custom": custom,
        "appearance": _normalize_appearance(state.get("appearance")),
        "groups": groups,
        "order": order,
        "ui": _normalize_ui(state.get("ui")),
        "mining": _normalize_mining(state.get("mining")),
        "dismissed": _normalize_dismissed(state.get("dismissed")),
        "mine_meta": _normalize_mine_meta(state.get("mine_meta")),
    }


def _normalize_mining(raw: Any) -> list[dict[str, Any]]:
    """校验 AI 挖掘结果，保留页面展示与再次加入所需的字段。

    Args:
        raw: 页面提交的挖掘结果列表。

    Returns:
        只保留 id 与触发词都合法的项。
    """
    if not isinstance(raw, list):
        return []
    mining: list[dict[str, Any]] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        item_id = str(value.get("id") or "").strip()
        command = str(value.get("command") or "").strip()
        if not item_id or not command:
            continue
        kind = str(value.get("kind") or "command")
        confidence = str(value.get("confidence") or "medium")
        mining.append(
            {
                "id": item_id,
                "handler": str(value.get("handler") or ""),
                "plugin": str(value.get("plugin") or ""),
                "group": str(value.get("group") or ""),
                "command": command,
                "aliases": [
                    alias.strip()
                    for alias in value.get("aliases") or []
                    if isinstance(alias, str) and alias.strip()
                ],
                "description": str(value.get("description") or ""),
                "kind": kind if kind in KIND_OPTIONS else "command",
                "confidence": (
                    confidence
                    if confidence in {"high", "medium", "low"}
                    else "medium"
                ),
                "regex": str(value.get("regex") or ""),
                "condition": str(value.get("condition") or ""),
                "comment": str(value.get("comment") or ""),
            }
        )
    return mining


def _normalize_dismissed(raw: Any) -> list[dict[str, Any]]:
    """校验废弃堆：记录被用户否决过的候选，避免下次挖掘重复出现。

    这里保留与挖掘结果同样的字段，页面才能把废弃项原样恢复回候选列表。

    Args:
        raw: 页面提交的废弃堆列表。

    Returns:
        去重后的废弃记录，按 id 唯一。
    """
    if not isinstance(raw, list):
        return []
    dismissed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, dict):
            continue
        item_id = str(value.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue
        record = _normalize_mining([value])
        if not record:
            continue
        seen.add(item_id)
        entry = record[0]
        entry["at"] = str(value.get("at") or "")
        dismissed.append(entry)
    return dismissed


def _normalize_mine_meta(raw: Any) -> dict[str, Any]:
    """校验「上次挖掘」的摘要信息，用于页面显示扫过什么、什么时候扫的。

    Args:
        raw: 页面提交的挖掘摘要。

    Returns:
        含 scanned / model / truncated / at 的映射；没扫过则为空字典。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    try:
        scanned = max(0, int(raw.get("scanned") or 0))
    except (TypeError, ValueError):
        scanned = 0
    return {
        "scanned": scanned,
        "model": str(raw.get("model") or ""),
        "truncated": bool(raw.get("truncated")),
        "at": str(raw.get("at") or ""),
    }


def _normalize_ui(raw: Any) -> dict[str, Any]:
    """校验插件页的界面偏好，只保留认识的字段。

    Args:
        raw: 页面提交的界面偏好。

    Returns:
        含 tab / swapped 的映射；页面没提交过则为空字典。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    ui: dict[str, Any] = {}
    tab = str(raw.get("tab") or "")
    if tab in UI_TABS:
        ui["tab"] = tab
    if isinstance(raw.get("swapped"), bool):
        ui["swapped"] = raw["swapped"]
    return ui


def _normalize_appearance(raw: Any) -> dict[str, Any]:
    """校验外观配置，保留插件页显式提交的每一个字段。

    这里刻意不丢弃「与默认值相同」的项：插件配置项可能提供了不同的兜底值
    （例如配置里填了字体，用户在插件页又想改回内置字体），只有原样保留用户
    的显式选择，才能做到「所见即所存」。借 Theme 完成取值范围钳制与非法值回退。

    Args:
        raw: 插件页提交的外观覆盖项。

    Returns:
        字段名到校验后取值的映射。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    theme = Theme.from_dict(raw)
    known = {field.name for field in fields(Theme)}
    return {name: getattr(theme, name) for name in raw if name in known}


def _default_state() -> dict[str, Any]:
    """返回一份可安全修改的默认配置副本。"""
    return json.loads(json.dumps(DEFAULT_STATE))