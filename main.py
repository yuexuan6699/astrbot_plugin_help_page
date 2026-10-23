from __future__ import annotations

import base64
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.config.default import VERSION
from astrbot.core.dashboard_assets import get_dashboard_version
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .core.ai import (
    AIUnavailableError,
    describe_candidates,
    polish_entries,
    resolve_provider_id,
)
from .core.help_image import Theme, describe_font_source, theme_schema
from .core.miner import collect_candidates
from .core.renderer import (
    build_groups,
    build_subtitle,
    build_text,
    render_help_png,
    render_help_png_bytes,
)
from .core.scanner import merge_entries, permission_visible, scan_entries
from .core.store import HelpStore, merge_appearance, normalize_state

PLUGIN_NAME = "astrbot_plugin_help_page"

CACHE_KEEP = 20
"""帮助图缓存目录保留的文件数量。"""

BUILTIN_HELP_MODULE = "astrbot.builtin_stars.builtin_commands.main"
"""AstrBot 自带 `/help` 所在的模块路径（内置插件 builtin_commands）。"""

BUILTIN_HELP_HANDLER = f"{BUILTIN_HELP_MODULE}_help"
"""AstrBot 自带 `/help` 的 handler 全名。"""


class HelpPagePlugin(Star):
    """自动扫描插件指令生成帮助页，支持 WebUI 页面编辑与聊天图片输出。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.store = HelpStore()
        self._apply_command_names(self.config.get("commands") or [])
        self._version_cache: str | None = None
        self._builtin_help_original: bool | None = None
        self._apply_builtin_help_block()

        self.context.register_web_api(
            f"/{PLUGIN_NAME}/state",
            self.api_get_state,
            ["GET"],
            "获取帮助页数据",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/state",
            self.api_save_state,
            ["POST"],
            "保存帮助页配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/reset",
            self.api_reset_state,
            ["POST"],
            "重置帮助页配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/preview",
            self.api_preview,
            ["POST"],
            "按当前配置实时合成帮助图预览",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/ai/polish",
            self.api_ai_polish,
            ["POST"],
            "AI 翻译或润色指令说明",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/ai/mine",
            self.api_ai_mine,
            ["POST"],
            "AI 挖掘未注册但可直接使用的功能",
        )

    def _apply_command_names(self, names: list) -> None:
        """按配置覆盖本插件指令的主名与别名。

        Args:
            names: 配置中的触发词列表，第一项为主指令名，其余为别名。
        """
        cleaned = [str(name).strip() for name in names if str(name).strip()]
        if not cleaned:
            return
        for handler in star_handlers_registry:
            if handler.handler_module_path != __name__:
                continue
            for filter_ref in handler.event_filters:
                if not isinstance(filter_ref, CommandFilter):
                    continue
                filter_ref.command_name = cleaned[0]
                filter_ref.alias = set(cleaned[1:])
                filter_ref._cmpl_cmd_names = None

    def _apply_builtin_help_block(self) -> None:
        """屏蔽 AstrBot 自带的 `/help`。

        本插件以 priority=1 抢先处理 `/help` 并 stop_event，正常情况已经轮不到
        内置 `/help`；但用户改了 `commands` 配置、或别名没有包含 help 时，
        内置 `/help` 就会露出来。这里直接把内置 handler 的 `enabled` 置为 False，
        与「优先级抢占」形成双保险，并在 terminate() 时恢复原状。

        只处理 `builtin_commands` 里名为 `help` 的那一条，不影响其他内置指令。
        """
        if not self.config.get("block_builtin_help", True):
            return
        for handler in star_handlers_registry:
            if handler.handler_module_path != BUILTIN_HELP_MODULE:
                continue
            if handler.handler_name != "help":
                continue
            self._builtin_help_original = bool(handler.enabled)
            handler.enabled = False
            self.logger.info("已屏蔽 AstrBot 自带 /help，改由本插件接管")
            return

    def _restore_builtin_help(self) -> None:
        """把 `terminate()` 之前屏蔽掉的内置 `/help` 恢复回原状态。"""
        if self._builtin_help_original is None:
            return
        for handler in star_handlers_registry:
            if handler.handler_full_name != BUILTIN_HELP_HANDLER:
                continue
            handler.enabled = self._builtin_help_original
            break
        self._builtin_help_original = None

    async def initialize(self) -> None:
        """插件全部加载完成后再确认一次内置 `/help` 已被屏蔽。

        插件加载顺序并不保证 `builtin_commands` 一定在本插件之前完成注册，
        所以 `__init__` 里没命中时，这里补一次，避免 /help 漏回内置指令。
        """
        if self._builtin_help_original is None:
            self._apply_builtin_help_block()

    async def terminate(self) -> None:
        """插件卸载时恢复被屏蔽的内置 `/help`，避免插件移除后指令永久失效。"""
        self._restore_builtin_help()

    async def _version_text(self) -> str:
        """生成帮助页的版本行文字：`AstrBot v本体版本 · WebUI v前端版本`。

        WebUI 版本可能读不到（未下载前端资源时返回 None），读不到就只显示本体版本；
        结果在插件生命周期内缓存，避免每次出图都去读一次磁盘。

        Returns:
            版本行文字；配置里关掉版本行时返回空串。
        """
        if not self.config.get("show_version", True):
            return ""
        if self._version_cache is not None:
            return self._version_cache

        text = f"AstrBot v{VERSION}"
        try:
            dashboard = await get_dashboard_version()
        except Exception as e:  # noqa: BLE001 - 前端资源读取失败不应影响帮助输出
            self.logger.warning(f"读取 WebUI 版本失败，版本行只显示本体版本: {e}")
            dashboard = None
        if dashboard:
            text = f"{text} · WebUI v{dashboard}"
        self._version_cache = text
        return text

    async def _with_version(self, theme: Theme) -> Theme:
        """给主题注入版本行文字。

        版本号是运行时信息，不写进配置文件，只在出图/预览前临时替换到 Theme 上。

        Args:
            theme: 已合并外观配置的主题。

        Returns:
            带版本行文字的主题副本。
        """
        return replace(theme, version_text=await self._version_text())

    # ------------------------------------------------------------------ 聊天指令

    @filter.command("帮助", alias={"help", "菜单"}, priority=1)
    async def help_command(self, event: AstrMessageEvent, query: str = ""):
        """查看插件功能帮助，发送「帮助 关键词」可按插件名或指令筛选。"""
        event.stop_event()

        entries = await self._collect_entries(self.store.state, query, event)
        if not entries:
            yield event.plain_result(
                f"没有找到与「{query.strip()}」相关的帮助。"
                if query.strip()
                else "当前没有可用的帮助条目。"
            )
            return

        groups = build_groups(entries, self.store.state)
        subtitle = build_subtitle(query.strip(), len(entries))
        theme = await self._with_version(
            self.store.appearance(self._appearance_defaults())
        )
        try:
            path = await render_help_png(
                groups,
                theme,
                self._new_image_path(),
                subtitle,
            )
        except Exception as e:  # noqa: BLE001 - 合成可能因字体/参数异常失败，统一降级为文本
            self.logger.warning(f"帮助图合成失败，改用文本输出: {e}")
            yield event.plain_result(
                build_text(
                    groups,
                    subtitle,
                    theme.show_group_header,
                    theme.feature_mark,
                    theme.version_text,
                    theme.show_version,
                )
            )
            return
        yield event.image_result(str(path))

    # ------------------------------------------------------------------ 插件页 API

    async def api_get_state(self):
        """返回插件页所需的扫描结果、手动配置与外观面板描述。"""
        defaults = self._appearance_defaults()
        ignored = self._ignored_plugins()
        return json_response(
            {
                "scanned": await scan_entries(ignored, self._include_builtin()),
                "state": self.store.state,
                "schema": theme_schema(),
                "appearance": asdict(self.store.appearance(defaults)),
                "appearance_base": defaults,
                "font": describe_font_source(str(self.config.get("font_path") or "")),
                "options": {
                    "ignored_plugins": sorted(ignored),
                    "filter_by_permission": bool(
                        self.config.get("filter_by_permission", True)
                    ),
                    "ai_provider": str(self.config.get("ai_provider") or ""),
                    "show_builtin_commands": self._include_builtin(),
                    "block_builtin_help": bool(
                        self.config.get("block_builtin_help", True)
                    ),
                    "show_version": bool(self.config.get("show_version", True)),
                    "version": await self._version_text(),
                },
            }
        )

    async def api_save_state(self):
        """保存插件页提交的完整手动配置。"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        self.store.save(payload)
        return json_response({"saved": True, "state": self.store.state})

    async def api_reset_state(self):
        """清空全部手动配置，恢复为自动扫描结果。"""
        self.store.reset()
        return json_response({"saved": True, "state": self.store.state})

    async def api_preview(self):
        """按页面当前（可能未保存的）配置实时合成一张帮助图。"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        state = normalize_state(payload.get("state") or {})

        entries = await self._collect_entries(state)
        if not entries:
            return error_response("当前没有任何条目可以预览", status_code=400)

        groups = build_groups(entries, state)
        subtitle = build_subtitle("", len(entries))
        try:
            data = await render_help_png_bytes(
                groups,
                await self._with_version(
                    merge_appearance(
                        self._appearance_defaults(), state.get("appearance")
                    )
                ),
                subtitle,
            )
        except Exception as e:  # noqa: BLE001 - 参数越界或字体缺失都可能抛错，统一回给页面
            self.logger.error(f"预览合成失败: {e}")
            return error_response(f"预览合成失败：{e}", status_code=500)

        return json_response(
            {
                "image": "data:image/png;base64,"
                + base64.b64encode(data).decode("ascii"),
                "bytes": len(data),
                "count": len(entries),
                "groups": len(groups),
                "subtitle": subtitle,
            }
        )

    async def api_ai_polish(self):
        """把选中条目的说明翻译成中文或润色。"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)

        mode = str(payload.get("mode") or "translate").strip()
        if mode not in {"translate", "polish"}:
            return error_response("mode 只能是 translate 或 polish", status_code=400)

        items: list[dict[str, str]] = []
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip()
            if not key:
                continue
            items.append(
                {
                    "key": key,
                    "command": str(raw.get("command") or ""),
                    "description": str(raw.get("description") or ""),
                }
            )
        if not items:
            return error_response("没有需要处理的条目", status_code=400)

        try:
            provider_id = await resolve_provider_id(self.context, self._ai_provider())
            result = await polish_entries(self.context, provider_id, items, mode)
        except AIUnavailableError as e:
            return error_response(str(e), status_code=503)
        except Exception as e:  # noqa: BLE001 - 模型调用失败原因多样，回给页面提示即可
            self.logger.error(f"AI 文案处理失败: {e}")
            return error_response(f"AI 处理失败：{e}", status_code=502)

        return json_response(
            {"items": result, "count": len(result), "model": provider_id}
        )

    async def api_ai_mine(self):
        """挖掘未注册成指令、但能直接响应用户消息的功能。"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)

        ignored = self._ignored_plugins()
        registered = {
            entry["key"]
            for entry in await scan_entries(ignored, self._include_builtin())
        }
        candidates, truncated = collect_candidates(registered, ignored)
        if not candidates:
            return json_response(
                {
                    "candidates": [],
                    "scanned": 0,
                    "truncated": False,
                    "reason": "no_candidates",
                    "message": "没有扫到未注册的消息处理函数，已装插件里可能确实没有这类功能",
                }
            )

        # 已否决过的候选不再重复推给用户，避免每次挖掘都被同一批噪声淹没
        dismissed = {
            str(item.get("id") or "")
            for item in self.store.state.get("dismissed") or []
            if isinstance(item, dict)
        }
        candidates = [item for item in candidates if item["id"] not in dismissed]
        if not candidates:
            return json_response(
                {
                    "candidates": [],
                    "scanned": 0,
                    "truncated": truncated,
                    "reason": "all_dismissed",
                    "message": "本次扫到的候选都在废弃堆里；想重新确认请到废弃堆逐条恢复",
                }
            )

        try:
            provider_id = await resolve_provider_id(self.context, self._ai_provider())
            results = await describe_candidates(self.context, provider_id, candidates)
        except AIUnavailableError as e:
            return error_response(str(e), status_code=503)
        except Exception as e:  # noqa: BLE001 - 模型调用失败原因多样，回给页面提示即可
            self.logger.error(f"AI 挖掘失败: {e}")
            return error_response(f"AI 挖掘失败：{e}", status_code=502)

        # 结果为空说明模型完全没给出可用内容。这里必须报错而不是回空列表，
        # 否则页面会把上一次辛苦挖到的结果覆盖成空。
        if not results:
            return error_response(
                "AI 没有返回可用的挖掘结果，请检查模型是否可用后重试；上次的结果已保留",
                status_code=502,
            )

        return json_response(
            {
                "candidates": results,
                "scanned": len(candidates),
                "truncated": truncated,
                "model": provider_id,
                "reason": "",
                "message": "",
            }
        )

    # ------------------------------------------------------------------ 内部工具

    def _ignored_plugins(self) -> set[str]:
        """插件黑名单：名单内的插件不参与帮助图扫描，也不参与 AI 挖掘。

        本插件始终排除，避免扫描到自己的帮助指令。
        """
        names = {
            str(name).strip()
            for name in (self.config.get("ignored_plugins") or [])
            if str(name).strip()
        }
        names.add(PLUGIN_NAME)
        return names

    def _ai_provider(self) -> str:
        """插件配置中指定的文案处理模型 ID，空表示使用默认模型。"""
        return str(self.config.get("ai_provider") or "").strip()

    def _include_builtin(self) -> bool:
        """是否把 AstrBot 自带指令纳入帮助图。

        为 True 时内置插件 `builtin_commands` 会覆盖 `ignored_plugins` 里的同名项，
        否则现网配置里默认就带着它，内置指令永远进不了帮助。
        """
        return bool(self.config.get("show_builtin_commands", True))

    def _appearance_defaults(self) -> dict[str, Any]:
        """插件配置项提供的兜底外观，插件页未覆盖时生效。"""
        defaults: dict[str, Any] = {}
        for key, value in (
            ("title", self.config.get("image_title")),
            ("footer", self.config.get("image_footer")),
            ("font_path", self.config.get("font_path")),
        ):
            text = str(value or "").strip()
            if text:
                defaults[key] = text
        return defaults

    async def _collect_entries(
        self,
        state: dict[str, Any],
        keyword: str = "",
        event: AstrMessageEvent | None = None,
    ) -> list[dict[str, Any]]:
        """把扫描结果与手动配置合并成最终条目，并按需过滤。

        Args:
            state: 手动配置。
            keyword: 聊天指令里的关键词，空表示不过滤。
            event: 传入时按发送者权限过滤；插件页预览不传，始终看到全部条目。

        Returns:
            最终条目列表。
        """
        entries = merge_entries(
            await scan_entries(self._ignored_plugins(), self._include_builtin()),
            state,
        )
        if event is not None and self.config.get("filter_by_permission", True):
            entries = [e for e in entries if permission_visible(e["permission"], event)]

        keyword = keyword.strip().lower()
        if keyword:
            entries = [e for e in entries if _matches(e, keyword)]
        return entries

    def _new_image_path(self) -> Path:
        """为本次帮助图分配一个缓存文件路径，并清理过期文件。"""
        directory = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME / "cache"
        directory.mkdir(parents=True, exist_ok=True)
        self._prune_cache(directory)
        return directory / f"help_{int(time.time() * 1000)}_{uuid4().hex[:6]}.png"

    @staticmethod
    def _prune_cache(directory: Path, keep: int = CACHE_KEEP) -> None:
        """只保留最近生成的若干张帮助图，避免缓存目录无限增长。"""
        try:
            files = sorted(
                directory.glob("help_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for stale in files[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass


def _matches(entry: dict[str, Any], keyword: str) -> bool:
    """判断条目是否命中搜索关键词。"""
    return (
        keyword in entry["command"].lower()
        or keyword in entry["group"].lower()
        or keyword in (entry["description"] or "").lower()
        or any(keyword in alias.lower() for alias in entry["aliases"])
    )
