/**
 * 「更好的帮助」插件页：左栏在「内容调整 / 画布调整」两个页签间切换，右栏实时预览，
 * 两栏可左右互换。
 *
 * 页面只通过 window.AstrBotPluginPage 与宿主通信，所有落盘都由后端 core.store 校验，
 * 因此这里维护的 state 结构与 help_content.json / help_canvas.json 完全一致。
 */

const bridge = window.AstrBotPluginPage;

const KIND_LABELS = {
  command: "指令",
  feature: "非指令",
};

const KINDS = Object.keys(KIND_LABELS);

const PERMISSION_LABELS = {
  everyone: "所有人",
  member: "成员",
  group_admin: "群管理员",
  shared_group_admin: "共享群管理员",
  admin: "管理员",
};

const PERMISSIONS = Object.keys(PERMISSION_LABELS);

const CONFIDENCE_LABELS = {
  high: "较可信",
  medium: "待确认",
  low: "存疑",
};

const AI_BATCH = 60;
/** 与后端 core.ai.MAX_ITEMS 一致，超出后分批提交。 */

const PREVIEW_DELAY = 600;
/** 改动后自动刷新预览的防抖间隔（毫秒）。 */

/* ------------------------------------------------------------------ DOM 工具 */

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function button(label, className, onClick) {
  const node = el("button", className || "btn", label);
  node.type = "button";
  if (onClick) node.addEventListener("click", onClick);
  return node;
}

function textInput(value, placeholder, onInput) {
  const node = el("input");
  node.type = "text";
  node.value = value === undefined || value === null ? "" : String(value);
  if (placeholder) node.placeholder = placeholder;
  if (onInput) node.addEventListener("input", () => onInput(node.value));
  return node;
}

function textArea(value, placeholder) {
  const node = el("textarea");
  node.value = value === undefined || value === null ? "" : String(value);
  if (placeholder) node.placeholder = placeholder;
  return node;
}

function checkbox(checked, onToggle) {
  const node = el("input");
  node.type = "checkbox";
  node.checked = Boolean(checked);
  if (onToggle) node.addEventListener("change", () => onToggle(node.checked));
  return node;
}

function chip(text, className) {
  return el("span", className ? `chip ${className}` : "chip", text);
}

function fieldNode(label, control, hint) {
  const node = el("div", "field");
  node.append(el("label", null, label), control);
  if (hint) node.append(el("div", "field-hint", hint));
  return node;
}

/** 触发词分隔符：斜杠、逗号、顿号、空白都算，前后多余的空格会被丢掉。 */
function splitTriggers(value) {
  return String(value || "")
    .split(/[/,，、\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 触发词去重（忽略大小写），保留首次出现的顺序。 */
function uniqueTriggers(triggers) {
  const seen = new Set();
  const result = [];
  for (const value of triggers) {
    const text = String(value || "").trim();
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(text);
  }
  return result;
}

/** 一个条目在帮助图里展示的全部触发词：主指令在前，别名去重跟在后面。 */
function entryTriggers(entry) {
  return uniqueTriggers([entry.command, ...(entry.aliases || [])]);
}

/** 解析「指令」输入框：第一个触发词作主指令，其余自动并入别名。
 *
 * 别名没有单独的输入框，全部写在指令框里（`签到/打卡` 或 `签到 打卡`），
 * 存下来后仍然是「主指令 + 别名」两段，帮助图渲染时按触发词逐个加斜杠。
 */
function parseCommandInput(value) {
  const tokens = splitTriggers(value);
  const command = tokens[0] || "";
  const aliases = uniqueTriggers(tokens.slice(1)).filter(
    (item) => item.toLowerCase() !== command.toLowerCase(),
  );
  return { command, aliases };
}

function compareRank(left, right) {
  const size = Math.max(left.length, right.length);
  for (let index = 0; index < size; index += 1) {
    const a = left[index] === undefined ? "" : left[index];
    const b = right[index] === undefined ? "" : right[index];
    if (a < b) return -1;
    if (a > b) return 1;
  }
  return 0;
}

function formatBytes(size) {
  const value = Number(size) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "");
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function toHexColor(value, fallback) {
  const text = String(value || "").trim();
  const hex = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.exec(text);
  if (hex) {
    let body = hex[1];
    if (body.length === 3) {
      body = body
        .split("")
        .map((char) => char + char)
        .join("");
    }
    return `#${body.slice(0, 6)}`.toLowerCase();
  }
  const rgb = /^\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/.exec(text);
  if (rgb) {
    const channels = [rgb[1], rgb[2], rgb[3]]
      .map((part) => Math.min(255, Number(part)).toString(16).padStart(2, "0"))
      .join("");
    return `#${channels}`;
  }
  return String(fallback || "#000000").slice(0, 7);
}

/* ------------------------------------------------------------------ 状态 */

const model = {
  scanned: [],
  state: {
    hidden: [],
    overrides: {},
    custom: [],
    appearance: {},
    groups: {},
    order: [],
    ui: {},
    mining: [],
    dismissed: [],
    mine_meta: {},
  },
  schema: [],
  appearance: {},
  appearanceBase: {},
  font: "",
  options: { ignored_plugins: [], filter_by_permission: true, ai_provider: "" },
  search: "",
  selection: new Set(),
  autoPreview: true,
  previewTimer: null,
  previewSeq: 0,
  dragging: null,
  dirty: false,
  busy: false,
  tab: "content",
  swapped: false,
};

function applyState(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const ui = source.ui && typeof source.ui === "object" ? source.ui : {};
  model.state = {
    hidden: Array.isArray(source.hidden) ? source.hidden.slice() : [],
    overrides:
      source.overrides && typeof source.overrides === "object"
        ? source.overrides
        : {},
    custom: Array.isArray(source.custom) ? source.custom.slice() : [],
    appearance:
      source.appearance && typeof source.appearance === "object"
        ? source.appearance
        : {},
    groups:
      source.groups && typeof source.groups === "object" ? source.groups : {},
    order: Array.isArray(source.order) ? source.order.slice() : [],
    ui,
    mining: Array.isArray(source.mining) ? source.mining.slice() : [],
    dismissed: Array.isArray(source.dismissed) ? source.dismissed.slice() : [],
    mine_meta:
      source.mine_meta && typeof source.mine_meta === "object"
        ? source.mine_meta
        : {},
  };
  model.tab = ui.tab === "canvas" ? "canvas" : "content";
  model.swapped = ui.swapped === true;
}

function markDirty() {
  model.dirty = true;
  renderSummary();
}

/* ------------------------------------------------------------------ 派生数据 */

function groupDescription(name) {
  const meta = model.state.groups[name];
  return meta && typeof meta === "object" ? String(meta.description || "") : "";
}

function isHidden(key) {
  return model.state.hidden.includes(key);
}

/** 条目是否为「非指令功能」：帮助图里不显示斜杠，并追加标注。 */
function isFeature(entry) {
  return (entry.kind || "command") === "feature";
}

/** 没单独设过标注时用的默认文字（外观参数 feature_mark）。 */
function defaultFeatureMark() {
  const value = appearanceValue("feature_mark");
  return value === undefined || value === null ? "" : String(value).trim();
}

/** 某种类型没单独设过标注时默认显示的文字：只有非指令类型才默认带 [非指令]。 */
function markDefaultForKind(kind) {
  return kind === "feature" ? defaultFeatureMark() : "";
}

/** 条目没单独设过标注时默认显示的文字。 */
function defaultMark(entry) {
  return markDefaultForKind((entry && entry.kind) || "command");
}

/** 条目实际用的标注：自己设过就用自己的（空串表示不标注），没设过用默认的。 */
function entryMark(entry) {
  const own = entry ? entry.mark : undefined;
  return typeof own === "string" ? own.trim() : defaultMark(entry);
}

/** 标注芯片：按这一条自己的标注取文字，空的就不加芯片。 */
function markChip(entry) {
  const mark = entryMark(entry);
  return mark ? chip(mark, "feature") : null;
}

/** 「类型」下拉的说明文字。 */
function featureKindHint() {
  return "选「非指令」则整条命令都去掉斜杠";
}

/** 「标注」输入框的说明文字。 */
function markHint(entry) {
  const fallback = defaultMark(entry);
  return fallback
    ? `这一条后面追加的标注；留空表示这条不标注（默认 ${fallback}）`
    : "这一条后面追加的标注；留空表示这条不标注";
}

/** 把输入框里的标注转成存盘值：等于这一条的默认值就不存，继续跟随默认。 */
function markOverride(value, entry) {
  const text = String(value || "").trim();
  return text === defaultMark(entry) ? null : text;
}

/** 让标注框跟着「类型」切换走：只要用户没动过，就同步成新类型的默认值。 */
function bindMarkDefault(input, kindField) {
  let untouched = input.value.trim();
  kindField.addEventListener("change", () => {
    const next = markDefaultForKind(kindField.value);
    if (input.value.trim() === untouched) input.value = next;
    untouched = next;
  });
}

/** 扫描结果 + 自定义条目，应用覆写与隐藏后的完整条目列表（未排序）。
 *
 * 插件黑名单由后端在扫描阶段过滤，这里不再重复判断内置插件。
 *
 * @param {boolean} includeHidden 为 true 时连已隐藏的条目一起返回，
 *   「不展示的条目」弹窗需要用它才能拿到被隐藏条目的当前配置。
 */
function buildEntries(includeHidden = false) {
  const entries = [];
  for (const item of model.scanned) {
    if (!includeHidden && isHidden(item.key)) continue;
    const override = model.state.overrides[item.key] || {};
    const entry = {
      key: item.key,
      plugin: item.plugin || "",
      group: override.group || item.group || "其他",
      command: override.command || item.command || "",
      real: item.command || "",
      aliases: override.aliases || item.aliases || [],
      description:
        override.description === undefined
          ? item.description || ""
          : override.description,
      permission: item.permission || "everyone",
      kind: override.kind || item.kind || "command",
      source: "auto",
    };
    applyEntryMark(entry, override, item);
    entries.push(entry);
  }
  for (const item of model.state.custom) {
    if (!includeHidden && isHidden(item.id)) continue;
    const override = model.state.overrides[item.id] || {};
    const entry = {
      key: item.id,
      plugin: "",
      group: override.group || item.group || "自定义",
      command: override.command || item.command || "",
      real: "",
      aliases: override.aliases || item.aliases || [],
      description:
        override.description === undefined
          ? item.description || ""
          : override.description,
      permission: item.permission || "everyone",
      kind: override.kind || item.kind || "command",
      source: "custom",
    };
    applyEntryMark(entry, override, item);
    entries.push(entry);
  }
  return entries;
}

/** 把逐条设置的标注并进条目。
 *
 * 覆写优先于条目本身；两边都没设过时不写这个键，渲染时回落到默认标注；
 * 空串是有效值，表示这条不追加标注。
 */
function applyEntryMark(entry, override, source) {
  const mark = override.mark === undefined ? source.mark : override.mark;
  if (typeof mark === "string") entry.mark = mark;
}

/** 与后端 scanner.sort_entries 保持同一套排序规则。 */
function sortEntries(entries) {
  const orderIndex = new Map();
  model.state.order.forEach((key, index) => {
    if (!orderIndex.has(key)) orderIndex.set(key, index);
  });
  const unlisted = orderIndex.size;
  const groupMeta = model.state.groups;

  const groupRank = (name) => {
    const meta = groupMeta[name];
    if (meta && typeof meta === "object") {
      return [0, Number(meta.order) || 0, name];
    }
    return [1, 0, name];
  };

  const entryRank = (entry) => {
    const index = orderIndex.get(entry.key);
    if (index === undefined) return [1, unlisted, entry.command];
    return [0, index, ""];
  };

  return entries.slice().sort((left, right) => {
    const byGroup = compareRank(groupRank(left.group), groupRank(right.group));
    if (byGroup !== 0) return byGroup;
    const byEntry = compareRank(entryRank(left), entryRank(right));
    if (byEntry !== 0) return byEntry;
    return compareRank([left.command], [right.command]);
  });
}

function orderedEntries() {
  return sortEntries(buildEntries());
}

function groupEntries(entries) {
  const groups = new Map();
  for (const entry of entries) {
    if (!groups.has(entry.group)) {
      groups.set(entry.group, {
        name: entry.group,
        description: groupDescription(entry.group),
        entries: [],
      });
    }
    groups.get(entry.group).entries.push(entry);
  }
  return Array.from(groups.values());
}

function matchesSearch(entry, keyword) {
  if (!keyword) return true;
  const text = keyword.toLowerCase();
  return (
    entry.command.toLowerCase().includes(text) ||
    entry.group.toLowerCase().includes(text) ||
    (entry.description || "").toLowerCase().includes(text) ||
    (entry.plugin || "").toLowerCase().includes(text) ||
    entry.aliases.some((alias) => alias.toLowerCase().includes(text))
  );
}

function selectedEntries() {
  return orderedEntries().filter((entry) => model.selection.has(entry.key));
}

/** 页面提交给后端的完整配置，字段与 help_data.json 一致。 */
function collectState() {
  const entries = orderedEntries();
  const groups = {};
  entries.forEach((entry, index) => {
    if (!groups[entry.group]) {
      groups[entry.group] = {
        description: groupDescription(entry.group),
        order: index,
      };
    }
  });
  for (const [name, meta] of Object.entries(model.state.groups)) {
    if (!groups[name]) {
      groups[name] = {
        description: String((meta && meta.description) || ""),
        order: Number((meta && meta.order) || 0) || 0,
      };
    }
  }
  return {
    hidden: model.state.hidden.slice(),
    overrides: model.state.overrides,
    custom: model.state.custom,
    appearance: model.state.appearance,
    groups,
    order: entries.map((entry) => entry.key),
    ui: { tab: model.tab, swapped: model.swapped },
    mining: model.state.mining,
    dismissed: model.state.dismissed,
    mine_meta: model.state.mine_meta,
  };
}

/* ------------------------------------------------------------------ 改动入口 */

function afterChange() {
  markDirty();
  renderAll();
  schedulePreview();
}

function setEntryGroup(key, group) {
  const custom = model.state.custom.find((item) => item.id === key);
  if (custom) {
    custom.group = group;
    return;
  }
  const override = { ...(model.state.overrides[key] || {}), group };
  model.state.overrides[key] = override;
}

function setGroupDescription(name, description) {
  const meta = model.state.groups[name];
  model.state.groups[name] = {
    description,
    order: meta && typeof meta === "object" ? Number(meta.order) || 0 : 0,
  };
}

function hideEntry(key) {
  if (!isHidden(key)) model.state.hidden.push(key);
  model.selection.delete(key);
  afterChange();
}

/** 彻底删除自定义条目（含 AI 挖掘加入的），并清掉它在其它配置里的残留。 */
function removeCustomEntry(key) {
  const entry = buildEntries(true).find((item) => item.key === key);
  const label = entry ? entry.command : key;
  openModal({
    title: "删除条目",
    body: el(
      "div",
      "row-desc",
      `「${label}」会从配置里彻底删除，帮助图和预览都不再出现，且无法撤销。`,
    ),
    okLabel: "删除",
    onConfirm: () => {
      model.state.custom = model.state.custom.filter((item) => item.id !== key);
      model.state.hidden = model.state.hidden.filter((item) => item !== key);
      model.state.order = model.state.order.filter((item) => item !== key);
      model.selection.delete(key);
      delete model.state.overrides[key];
      closeModal();
      afterChange();
      toast(`已删除「${label}」`);
    },
  });
}

function restoreEntry(key) {
  model.state.hidden = model.state.hidden.filter((item) => item !== key);
  if (!model.state.order.includes(key)) model.state.order.push(key);
  afterChange();
}

function hideGroup(name) {
  let count = 0;
  for (const entry of buildEntries()) {
    if (entry.group !== name || isHidden(entry.key)) continue;
    model.state.hidden.push(entry.key);
    model.selection.delete(entry.key);
    count += 1;
  }
  if (count) afterChange();
  else toast("该分组没有可隐藏的条目");
}

function applyDescription(key, description) {
  const custom = model.state.custom.find((item) => item.id === key);
  if (custom) {
    if ((custom.description || "") === description) return false;
    custom.description = description;
    return true;
  }
  const entry = model.scanned.find((item) => item.key === key);
  if (!entry) return false;
  if ((entry.description || "") === description) return false;
  model.state.overrides[key] = {
    ...(model.state.overrides[key] || {}),
    description,
  };
  return true;
}

/* ------------------------------------------------------------------ 顶部信息 */

function renderSummary() {
  const entries = buildEntries();
  const hidden = model.state.hidden.length;
  const custom = model.state.custom.filter((item) => !isHidden(item.id)).length;
  const features = entries.filter(isFeature).length;
  const parts = [
    `自动扫描 ${model.scanned.length} 条`,
    `输出 ${entries.length} 条`,
    `隐藏 ${hidden} 条`,
    `自定义 ${custom} 条`,
  ];
  if (features) parts.push(`非指令 ${features} 条`);
  if (model.state.mining.length) {
    parts.push(`待确认挖掘 ${model.state.mining.length} 条`);
  }
  if (model.dirty) parts.push("有未保存改动");
  $("summary").textContent = parts.join(" · ");
  $("list-stats").textContent = `${groupEntries(entries).length} 组`;
  $("btn-trash").textContent = `不展示的条目 (${hidden})`;
  $("btn-mine").textContent = model.state.mining.length
    ? `AI 挖掘 (${model.state.mining.length})`
    : "AI 挖掘未注册功能";
  $("sel-count").textContent = `已选 ${model.selection.size}`;
}

/* ------------------------------------------------------------------ 左侧清单 */

function buildRowNode(entry) {
  const row = el("div", "row");
  row.draggable = true;
  row.addEventListener("dragstart", (event) => startDrag(event, {
    type: "row",
    key: entry.key,
  }));
  row.addEventListener("dragend", endDrag);
  row.addEventListener("dragover", (event) => onRowDragOver(event, row));
  row.addEventListener("dragleave", () =>
    row.classList.remove("drop-before", "drop-after"),
  );
  row.addEventListener("drop", (event) => onRowDrop(event, row, entry));

  const pick = checkbox(model.selection.has(entry.key), (checked) => {
    if (checked) model.selection.add(entry.key);
    else model.selection.delete(entry.key);
    renderSummary();
  });
  pick.className = "row-pick";
  pick.title = "勾选后可批量 AI 翻译 / 润色";
  row.append(pick);

  const main = el("div", "row-main");
  const title = el("div", "row-title");
  const cmd = el("span", "cmd");
  const triggers = entryTriggers(entry);
  if (isFeature(entry)) {
    cmd.append(document.createTextNode(triggers.join("、")));
  } else {
    triggers.forEach((trigger, index) => {
      if (index) cmd.append(el("span", "sep", "、"));
      cmd.append(el("span", "slash", "/"), document.createTextNode(trigger));
    });
  }
  title.append(cmd);
  // 标注对指令与非指令一视同仁，这条有就显示
  const mark = markChip(entry);
  if (mark) title.append(mark);
  if (entry.source === "custom") title.append(chip("自定义", "custom"));
  if (entry.permission !== "everyone") {
    title.append(
      chip(PERMISSION_LABELS[entry.permission] || entry.permission, "perm"),
    );
  }
  main.append(title);
  main.append(
    el(
      "div",
      entry.description ? "row-desc" : "row-desc empty",
      entry.description || "（无说明）",
    ),
  );
  row.append(main);

  const tools = el("div", "row-tools");
  tools.append(
    button("编辑", "btn ghost", () => openEntryEditor(entry)),
    button("隐藏", "btn ghost", () => hideEntry(entry.key)),
  );
  if (entry.source === "custom") {
    tools.append(
      button("删除", "btn ghost danger", () => removeCustomEntry(entry.key)),
    );
  }
  row.append(tools);
  return row;
}

function buildGroupNode(group) {
  const node = el("div", "group");
  node.addEventListener("dragover", (event) => onGroupDragOver(event, node));
  node.addEventListener("dragleave", () =>
    node.classList.remove("drop-before", "drop-after"),
  );
  node.addEventListener("drop", (event) => onGroupDrop(event, node, group));

  const head = el("div", "group-head");
  const grip = el("span", "grip", "⋮⋮");
  grip.title = "拖动调整分组顺序";
  grip.draggable = true;
  grip.addEventListener("dragstart", (event) => startDrag(event, {
    type: "group",
    group: group.name,
  }));
  grip.addEventListener("dragend", endDrag);

  const desc = textInput(group.description, "分组说明（显示在分组标题右侧）", (value) => {
    setGroupDescription(group.name, value);
    markDirty();
    schedulePreview();
  });
  desc.className = "g-desc";

  const tools = el("div", "row-tools");
  tools.append(
    button("全选", "btn ghost", () => {
      for (const entry of group.entries) model.selection.add(entry.key);
      renderSummary();
      renderList();
    }),
    button("隐藏该组", "btn ghost", () => hideGroup(group.name)),
  );

  head.append(grip, el("span", "g-name", group.name));
  head.append(el("span", "badge", String(group.entries.length)));
  head.append(desc, tools);
  node.append(head);

  const rows = el("div", "group-rows");
  rows.addEventListener("dragover", (event) => {
    if (!model.dragging) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  });
  rows.addEventListener("drop", (event) => {
    const drag = model.dragging;
    if (!drag) return;
    event.preventDefault();
    event.stopPropagation();
    if (drag.type === "row") moveIntoGroup(drag.key, group.name, null, "after");
    else if (drag.type === "group") moveGroup(drag.group, group.name, "after");
    afterChange();
  });
  for (const entry of group.entries) rows.append(buildRowNode(entry));
  node.append(rows);
  return node;
}

function renderList() {
  const list = $("list");
  list.replaceChildren();
  const entries = orderedEntries().filter((entry) =>
    matchesSearch(entry, model.search),
  );
  if (!entries.length) {
    list.append(
      el(
        "div",
        "empty-hint",
        model.search ? "没有匹配的条目" : "没有可展示的条目",
      ),
    );
    return;
  }
  for (const group of groupEntries(entries)) list.append(buildGroupNode(group));
}

/* ------------------------------------------------------------------ 拖拽 */

function startDrag(event, payload) {
  model.dragging = payload;
  event.stopPropagation();
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", payload.key || payload.group || "");
  event.currentTarget.closest(".row")?.classList.add("is-dragging");
  event.currentTarget.closest(".group")?.classList.add("is-dragging");
}

function endDrag() {
  model.dragging = null;
  for (const node of document.querySelectorAll(".is-dragging")) {
    node.classList.remove("is-dragging");
  }
  for (const node of document.querySelectorAll(".drop-before, .drop-after")) {
    node.classList.remove("drop-before", "drop-after");
  }
}

function dropPosition(event, node) {
  const rect = node.getBoundingClientRect();
  return event.clientY > rect.top + rect.height / 2 ? "after" : "before";
}

function onRowDragOver(event, row) {
  if (!model.dragging) return;
  event.preventDefault();
  event.stopPropagation();
  event.dataTransfer.dropEffect = "move";
  const position = dropPosition(event, row);
  row.classList.toggle("drop-before", position === "before");
  row.classList.toggle("drop-after", position === "after");
}

function onRowDrop(event, row, entry) {
  const drag = model.dragging;
  if (!drag) return;
  event.preventDefault();
  event.stopPropagation();
  const position = row.classList.contains("drop-after") ? "after" : "before";
  if (drag.type === "group") {
    if (drag.group !== entry.group) moveGroup(drag.group, entry.group, position);
    else return;
  } else if (drag.type === "row") {
    if (drag.key === entry.key) return;
    moveIntoGroup(drag.key, entry.group, entry.key, position);
  } else {
    return;
  }
  afterChange();
}

function onGroupDragOver(event, node) {
  if (!model.dragging) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  const position = dropPosition(event, node.querySelector(".group-head") || node);
  node.classList.toggle("drop-before", position === "before");
  node.classList.toggle("drop-after", position === "after");
}

function onGroupDrop(event, node, group) {
  const drag = model.dragging;
  if (!drag) return;
  event.preventDefault();
  event.stopPropagation();
  const position = node.classList.contains("drop-after") ? "after" : "before";
  if (drag.type === "group") {
    if (drag.group !== group.name) moveGroup(drag.group, group.name, position);
    else return;
  } else if (drag.type === "row") {
    moveIntoGroup(drag.key, group.name, null, "after");
  } else {
    return;
  }
  afterChange();
}

/** 当前显示顺序（可见条目）。 */
function currentOrder() {
  return orderedEntries().map((entry) => entry.key);
}

/** 按当前显示顺序重排分组：分组序号取各组首次出现的位置。
 *
 * 必须依据 order 数组而不是 orderedEntries()：后者本身就依赖分组序号排序，
 * 用旧序号推导新序号会互相打架。
 */
function refreshGroupOrder() {
  const groupOf = new Map();
  for (const entry of buildEntries()) groupOf.set(entry.key, entry.group);
  const seen = new Set();
  let order = 0;
  for (const key of model.state.order) {
    const group = groupOf.get(key);
    if (!group || seen.has(group)) continue;
    seen.add(group);
    model.state.groups[group] = {
      description: groupDescription(group),
      order,
    };
    order += 1;
  }
}

function commitOrder(keys) {
  model.state.order = keys.slice();
  refreshGroupOrder();
}

/** 把条目移入目标分组，并放到锚点前后；没有锚点时追加到分组末尾。 */
function moveIntoGroup(key, group, anchorKey, position) {
  setEntryGroup(key, group);
  const keys = currentOrder().filter((item) => item !== key);
  if (anchorKey && anchorKey !== key) {
    const index = keys.indexOf(anchorKey);
    if (index >= 0) {
      keys.splice(position === "after" ? index + 1 : index, 0, key);
      commitOrder(keys);
      return;
    }
  }
  const others = sortEntries(buildEntries().filter((entry) => entry.key !== key));
  let insertAt = keys.length;
  for (let index = others.length - 1; index >= 0; index -= 1) {
    if (others[index].group !== group) continue;
    insertAt = keys.indexOf(others[index].key) + 1;
    break;
  }
  keys.splice(insertAt, 0, key);
  commitOrder(keys);
}

/** 整体移动分组，position 为相对目标分组的位置。 */
function moveGroup(source, target, position) {
  const groups = groupEntries(orderedEntries());
  const from = groups.findIndex((item) => item.name === source);
  if (from < 0) return;
  const [moved] = groups.splice(from, 1);
  let to = groups.findIndex((item) => item.name === target);
  if (to < 0) to = groups.length;
  else if (position === "after") to += 1;
  groups.splice(to, 0, moved);
  commitOrder(groups.flatMap((group) => group.entries.map((entry) => entry.key)));
}

/* ------------------------------------------------------------------ 右侧参数面板 */

function buildIntControl(field, value) {
  const low = Number(field.min) || 0;
  const high = Number(field.max) || 100;
  const wrap = el("div", "field-int");
  const range = el("input");
  range.type = "range";
  range.min = String(low);
  range.max = String(high);
  range.value = String(value);
  const number = el("input");
  number.type = "number";
  number.min = String(low);
  number.max = String(high);
  number.value = String(value);

  const apply = (raw) => {
    const parsed = Math.round(Number(raw));
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(high, Math.max(low, parsed));
    range.value = String(clamped);
    number.value = String(clamped);
    setAppearance(field.name, clamped);
  };
  range.addEventListener("input", () => apply(range.value));
  number.addEventListener("change", () => apply(number.value));
  wrap.append(range, number);
  return wrap;
}

function buildColorControl(field, value) {
  const wrap = el("div", "field-color");
  const picker = el("input");
  picker.type = "color";
  picker.value = toHexColor(value, field.default);
  const text = textInput(value, "#rrggbb 或 (r,g,b)", (next) => {
    picker.value = toHexColor(next, picker.value);
    setAppearance(field.name, next);
  });
  picker.addEventListener("input", () => {
    text.value = picker.value;
    setAppearance(field.name, picker.value);
  });
  wrap.append(picker, text);
  return wrap;
}

function setAppearance(name, value) {
  model.appearance[name] = value;
  const base = model.appearanceBase[name];
  if (String(value) === String(base === undefined ? "" : base)) {
    delete model.state.appearance[name];
  } else {
    model.state.appearance[name] = value;
  }
  markDirty();
  schedulePreview();
}

function buildBoolControl(field, value) {
  const wrap = el("div", "field-bool");
  const state = el("span", "field-hint");
  const box = checkbox(Boolean(value), (checked) => {
    state.textContent = checked ? "已开启" : "已关闭";
    setAppearance(field.name, checked);
  });
  state.textContent = box.checked ? "已开启" : "已关闭";
  wrap.append(box, state);
  return wrap;
}

function buildField(field) {
  const value =
    model.appearance[field.name] === undefined
      ? field.default
      : model.appearance[field.name];
  let control;
  if (field.kind === "int") control = buildIntControl(field, value);
  else if (field.kind === "bool") control = buildBoolControl(field, value);
  else if (field.kind === "color") control = buildColorControl(field, value);
  else control = textInput(value, "", (next) => setAppearance(field.name, next));

  let hint = "";
  if (field.name === "font_path") hint = `当前生效：${model.font}`;
  else if (field.name === "show_group_header") {
    hint = "关闭后不渲染分组标题条，所有条目连排成一组";
  }
  return fieldNode(field.label, control, hint);
}

/** 不在折叠面板里出现的项：主标题、副标题已提到页签顶部，标注改成了每条单独设置。 */
const PANEL_HIDDEN_FIELDS = new Set(["title", "subtitle", "feature_mark"]);

function buildSection(section) {
  const node = el("details", "section");
  node.open = true;
  node.append(el("summary", null, section.title));
  const body = el("div", "section-body");
  for (const field of section.fields) {
    if (PANEL_HIDDEN_FIELDS.has(field.name)) continue;
    body.append(buildField(field));
  }
  node.append(body);
  return node;
}

/** 按名字找外观字段描述，拿它的默认值。 */
function appearanceField(name) {
  for (const section of model.schema) {
    for (const field of section.fields) {
      if (field.name === name) return field;
    }
  }
  return null;
}

/** 外观项当前生效值：用户改过的优先，其次插件配置兜底，最后是主题默认值。 */
function appearanceValue(name) {
  if (model.appearance[name] !== undefined) return model.appearance[name];
  if (model.appearanceBase[name] !== undefined) return model.appearanceBase[name];
  const field = appearanceField(name);
  return field ? field.default : "";
}

/** 页签顶部的两项固定文字：名字、副标题，改完即时进预览。 */
function renderCanvasHead() {
  const head = $("canvas-head");
  head.replaceChildren();
  for (const [name, label, placeholder] of [
    ["title", "名字", ""],
    ["subtitle", "副标题", "留空则显示自动统计的条数"],
  ]) {
    head.append(
      fieldNode(
        label,
        textInput(appearanceValue(name), placeholder, (next) =>
          setAppearance(name, next),
        ),
      ),
    );
  }
}

function buildInfoSection() {
  const node = el("details", "section");
  node.open = false;
  node.append(el("summary", null, "当前设置"));
  const body = el("div", "section-body");
  body.append(
    fieldNode(
      "权限过滤",
      el(
        "div",
        "field-hint",
        model.options.filter_by_permission
          ? "聊天输出时按发送者权限过滤（插件配置）"
          : "聊天输出不做权限过滤（插件配置）",
      ),
    ),
    fieldNode(
      "插件黑名单",
      el(
        "div",
        "field-hint",
        (model.options.ignored_plugins || []).length
          ? `不参与扫描与挖掘：${(model.options.ignored_plugins || []).join("、")}（插件配置）`
          : "没有排除任何插件（插件配置）",
      ),
    ),
    fieldNode(
      "AI 模型",
      el(
        "div",
        "field-hint",
        model.options.ai_provider || "使用当前默认对话模型",
      ),
    ),
  );
  node.append(body);
  return node;
}

function renderPanel() {
  const panel = $("panel");
  panel.replaceChildren();
  for (const section of model.schema) panel.append(buildSection(section));
  panel.append(buildInfoSection());
}

/* ------------------------------------------------------------------ 页签与布局 */

function setTab(tab) {
  if (model.tab === tab) return;
  model.tab = tab;
  renderTabs();
  markDirty();
}

function setSwapped(swapped) {
  const next = Boolean(swapped);
  if (model.swapped === next) return;
  model.swapped = next;
  renderTabs();
  markDirty();
}

/** 同步左栏页签与左右互换状态；两者都会随配置一起保存。 */
function renderTabs() {
  const canvasActive = model.tab === "canvas";
  $("tab-btn-content").classList.toggle("is-active", !canvasActive);
  $("tab-btn-canvas").classList.toggle("is-active", canvasActive);
  $("tab-content").classList.toggle("is-hidden", canvasActive);
  $("tab-canvas").classList.toggle("is-hidden", !canvasActive);
  // 清单标题与组数挤在页签行里，只有「内容调整」页签用得到
  $("list-title").classList.toggle("is-hidden", canvasActive);
  $("list-stats").classList.toggle("is-hidden", canvasActive);
  $("workspace").classList.toggle("swapped", model.swapped);
}

/* ------------------------------------------------------------------ 预览 */

function schedulePreview() {
  if (!model.autoPreview) return;
  clearTimeout(model.previewTimer);
  model.previewTimer = setTimeout(refreshPreview, PREVIEW_DELAY);
}

async function refreshPreview() {
  clearTimeout(model.previewTimer);
  const sequence = (model.previewSeq += 1);
  const meta = $("preview-meta");
  const image = $("preview-img");
  const empty = $("preview-empty");
  meta.textContent = "合成中…";
  try {
    const result = await bridge.apiPost("preview", { state: collectState() });
    if (sequence !== model.previewSeq) return;
    image.src = result.image;
    image.hidden = false;
    empty.hidden = true;
    meta.textContent = `${result.count} 条 · ${result.groups} 组 · ${formatBytes(result.bytes)}`;
  } catch (error) {
    if (sequence !== model.previewSeq) return;
    image.hidden = true;
    image.removeAttribute("src");
    empty.hidden = false;
    empty.textContent = `预览失败：${error.message}`;
    meta.textContent = "预览失败";
  }
}

/* ------------------------------------------------------------------ 弹窗 */

let modalConfirm = null;

function openModal(options) {
  $("modal-title").textContent = options.title;
  const body = $("modal-body");
  body.replaceChildren();
  if (options.body) body.append(options.body);
  $("modal-note").textContent = options.note || "";
  $("modal-ok").textContent = options.okLabel || "确定";
  $("modal-ok").hidden = !options.onConfirm;
  $("modal-box").classList.toggle("wide", Boolean(options.wide));
  modalConfirm = options.onConfirm || null;
  $("modal").classList.remove("is-hidden");
}

function closeModal() {
  $("modal").classList.add("is-hidden");
  $("modal-body").replaceChildren();
  modalConfirm = null;
}

function permissionSelect(value) {
  const select = el("select");
  for (const name of PERMISSIONS) {
    const option = el("option", null, PERMISSION_LABELS[name]);
    option.value = name;
    if (name === value) option.selected = true;
    select.append(option);
  }
  return select;
}

function kindSelect(value) {
  const select = el("select");
  for (const name of KINDS) {
    const option = el("option", null, KIND_LABELS[name]);
    option.value = name;
    if (name === value) option.selected = true;
    select.append(option);
  }
  return select;
}

function openEntryEditor(entry) {
  const isCustom = entry.source === "custom";
  const body = el("div");
  // 别名和主指令共用这一个输入框：预填时把已有别名一起写进来，保存时再拆回两段
  const nameInput = textInput(entryTriggers(entry).join(" "));
  const groupInput = textInput(entry.group);
  const descArea = textArea(entry.description, "显示在条目下方的注释");
  const kindField = kindSelect(entry.kind || "command");
  // 标注逐条设置：预填当前生效值，清空就表示这一条不要标注
  const markInput = textInput(entryMark(entry));
  bindMarkDefault(markInput, kindField);
  let permSelect = null;

  // 帮助图里展示的就是这个字段：自动扫描的条目默认填真实指令，改了只影响展示。
  const commandHint = entry.real
    ? `实际指令：/${entry.real}（改这里只影响帮助图展示，不会改动插件）`
    : "自定义条目没有真实指令，这里就是它在帮助图里的名称";
  body.append(
    fieldNode(
      "指令",
      nameInput,
      `${commandHint}。写多个用 / 或空格分隔，第一个作主指令，其余自动进别名`,
    ),
  );
  body.append(fieldNode("类型", kindField, featureKindHint()));
  body.append(fieldNode("标注", markInput, markHint(entry)));
  if (isCustom) {
    const custom = model.state.custom.find((item) => item.id === entry.key) || {};
    permSelect = permissionSelect(custom.permission || "everyone");
    body.append(fieldNode("权限", permSelect));
  }
  body.append(fieldNode("分组", groupInput), fieldNode("注释", descArea));

  openModal({
    title: isCustom ? "编辑自定义条目" : "编辑条目",
    body,
    okLabel: "保存",
    onConfirm: () => {
      const parsed = parseCommandInput(nameInput.value);
      if (!parsed.command) {
        toast("指令不能为空");
        return;
      }
      applyEntryEdit(entry, {
        command: parsed.command,
        aliases: parsed.aliases,
        group: groupInput.value.trim() || entry.group,
        description: descArea.value,
        kind: kindField.value,
        mark: markOverride(markInput.value, { kind: kindField.value }),
        permission: permSelect ? permSelect.value : null,
      });
      closeModal();
      afterChange();
    },
  });
}

function applyEntryEdit(entry, values) {
  const key = entry.key;
  if (entry.source === "custom") {
    const custom = model.state.custom.find((item) => item.id === key);
    if (!custom) return;
    custom.command = values.command;
    custom.group = values.group;
    custom.description = values.description;
    custom.kind = values.kind;
    custom.aliases = values.aliases;
    if (values.permission) custom.permission = values.permission;
    if (values.mark === null) delete custom.mark;
    else custom.mark = values.mark;
    delete model.state.overrides[key];
    return;
  }

  const original = model.scanned.find((item) => item.key === key) || {};
  const override = {};
  if (values.command && values.command !== original.command) {
    override.command = values.command;
  }
  if (values.group && values.group !== original.group) {
    override.group = values.group;
  }
  if (values.description !== (original.description || "")) {
    override.description = values.description;
  }
  if (values.kind && values.kind !== (original.kind || "command")) {
    override.kind = values.kind;
  }
  if (values.aliases.join(" ") !== (original.aliases || []).join(" ")) {
    override.aliases = values.aliases;
  }
  // 只有单独设过的条目才写 mark：写成 null 表示跟随默认，写空串才是这条不标注
  if (values.mark !== null) override.mark = values.mark;
  if (Object.keys(override).length) model.state.overrides[key] = override;
  else delete model.state.overrides[key];
}

function openCustomCreator() {
  const body = el("div");
  const groupInput = textInput("自定义", "所属分组，如 娱乐");
  const commandInput = textInput("", "帮助图里展示的触发词，如 签到 或 签到/打卡");
  const kindField = kindSelect("command");
  const permSelect = permissionSelect("everyone");
  const descArea = textArea("", "显示在条目下方的注释");
  // 预填当前类型的默认标注，不动它就等于跟随默认，存盘时不会多写这段
  const markInput = textInput(markDefaultForKind("command"));
  bindMarkDefault(markInput, kindField);
  body.append(
    fieldNode("分组", groupInput),
    fieldNode(
      "指令",
      commandInput,
      "帮助图里展示的触发词，填插件实际支持的指令；别名也写在这里，用 / 或空格分隔，第一个作主指令，其余自动进别名",
    ),
    fieldNode("类型", kindField, featureKindHint()),
    fieldNode("标注", markInput, markHint(null)),
    fieldNode("权限", permSelect),
    fieldNode("注释", descArea),
  );

  openModal({
    title: "新增自定义条目",
    body,
    okLabel: "添加",
    onConfirm: () => {
      const parsed = parseCommandInput(commandInput.value);
      if (!parsed.command) {
        toast("指令不能为空");
        return;
      }
      const item = {
        id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
        group: groupInput.value.trim() || "自定义",
        command: parsed.command,
        aliases: parsed.aliases,
        description: descArea.value,
        permission: permSelect.value,
        kind: kindField.value,
      };
      const mark = markOverride(markInput.value, { kind: kindField.value });
      if (mark !== null) item.mark = mark;
      model.state.custom.push(item);
      closeModal();
      afterChange();
    },
  });
}

/** 「不展示的条目」弹窗：查看、编辑、恢复或彻底删除被隐藏的条目。
 *
 * 扫描来的条目只能恢复（来源在插件里，删不掉）；自定义条目（含 AI 挖掘加入的）
 * 可以在这里彻底删除。
 */
function openTrash() {
  const hidden = model.state.hidden;
  if (!hidden.length) {
    toast("没有不展示的条目");
    return;
  }

  // 必须用 buildEntries(true)：默认的 buildEntries() 会跳过隐藏项，而这里要的正是隐藏项
  const all = buildEntries(true);
  const body = el("div");
  const customKeys = [];

  for (const key of hidden) {
    const entry = all.find((item) => item.key === key);
    const custom = model.state.custom.find((item) => item.id === key);
    const node = el("div", "candidate");
    const main = el("div", "candidate-main");
    const title = el("div", "row-title");
    const triggers = entry ? entryTriggers(entry) : [key];
    const kind = (entry && entry.kind) || "command";
    const cmd = el("span", "cmd");
    if (kind === "feature") {
      cmd.append(document.createTextNode(triggers.join("、")));
    } else {
      triggers.forEach((trigger, index) => {
        if (index) cmd.append(el("span", "sep", "、"));
        cmd.append(el("span", "slash", "/"), document.createTextNode(trigger));
      });
    }
    title.append(cmd);
    title.append(
      chip(custom ? "自定义" : entry ? entry.group || "扫描条目" : "已卸载"),
    );
    const mark = markChip(entry);
    if (mark) title.append(mark);
    main.append(title);
    main.append(
      el(
        "div",
        "row-desc",
        entry
          ? entry.description || "（无说明）"
          : "（该条目已不在扫描结果中）",
      ),
    );

    const tools = el("div", "row-tools");
    if (entry) {
      tools.append(button("编辑", "btn ghost", () => openEntryEditor(entry)));
    }
    tools.append(
      button("恢复", "btn ghost", () => {
        restoreEntry(key);
        closeModal();
      }),
    );
    if (custom) {
      customKeys.push(key);
      tools.append(
        button("删除", "btn ghost danger", () => {
          closeModal();
          removeCustomEntry(key);
        }),
      );
    }
    node.append(main, tools);
    body.append(node);
  }

  const note = customKeys.length
    ? "扫描来的条目只能恢复；自定义条目可以彻底删除"
    : "扫描来的条目来源在插件里，只能恢复";

  openModal({
    title: `不展示的条目（${hidden.length} 条）`,
    body,
    note,
    okLabel: "全部恢复",
    wide: true,
    onConfirm: () => {
      for (const key of hidden.slice()) restoreEntry(key);
      closeModal();
    },
  });
}

/* ------------------------------------------------------------------ AI */

function openMineModal() {
  let mode = "candidates";
  let rows = [];

  const action = button("开始查找", "btn tiny accent", () => runMineSearch(render));
  const pileBtn = button("废弃堆 (0)", "btn tiny", () => {
    mode = mode === "dismissed" ? "candidates" : "dismissed";
    render();
  });
  const status = el("span", "mine-status");
  const bar = el("div", "mine-bar");
  bar.append(action, pileBtn, status);

  const listWrap = el("div", "mine-list");
  const body = el("div");
  body.append(bar, listWrap);

  /** 候选列表：勾选的加入自定义条目，未勾选的进废弃堆。 */
  function renderCandidates() {
    const mining = model.state.mining;
    if (!mining.length) {
      listWrap.append(
        el(
          "div",
          "mine-empty",
          model.state.mine_meta.at
            ? "上次的结果已经处理完了，点「再次查找」重新扫一遍。"
            : "还没有挖掘结果。点上面的「开始查找」，让 AI 扫一遍未注册、但能直接响应用户消息的功能。",
        ),
      );
      return;
    }

    const existing = new Set(
      [
        ...model.scanned.map((item) => item.command),
        ...model.state.custom.map((item) => item.command),
      ].map((name) => String(name || "").toLowerCase()),
    );

    for (const candidate of mining) {
      const feature = candidate.kind === "feature";
      const node = el("div", "candidate");
      const taken = existing.has(String(candidate.command || "").toLowerCase());
      // 非指令功能与重名条目默认不勾选，避免把内部钩子混进帮助页
      const pick = checkbox(!feature && !taken, null);
      const main = el("div", "candidate-main");

      const title = el("div", "row-title");
      title.append(chip(candidate.plugin || "未知插件"));
      title.append(
        chip(feature ? defaultFeatureMark() || "非指令" : "指令", feature ? "feature" : "custom"),
      );
      title.append(
        chip(
          CONFIDENCE_LABELS[candidate.confidence] || candidate.confidence || "待确认",
          candidate.confidence === "high"
            ? "conf-high"
            : candidate.confidence === "low"
              ? "conf-low"
              : "",
        ),
      );
      if (taken) title.append(chip("与现有条目重名", "perm"));
      main.append(title);

      const source = candidate.condition
        ? `来自 if：${candidate.condition}`
        : candidate.regex
          ? `匹配 ${candidate.regex}`
          : "";
      if (source) main.append(el("div", "row-desc", source));
      if (candidate.comment) {
        main.append(el("div", "row-desc", `源码注释：${candidate.comment}`));
      }

      const commandInput = textInput(
        entryTriggers(candidate).join(" "),
        "帮助图里展示的触发词",
      );
      const groupInput = textInput(candidate.group || candidate.plugin, "分组");
      const descArea = textArea(candidate.description, "显示在条目下方的注释");
      main.append(
        fieldNode(
          "指令",
          commandInput,
          "默认取自源码里的 if 条件或注释，可自行修正成实际指令；别名也写在这里，用 / 或空格分隔，第一个作主指令，其余自动进别名",
        ),
        fieldNode("分组", groupInput),
        fieldNode("注释", descArea),
      );
      node.append(pick, main);
      listWrap.append(node);
      rows.push({ candidate, pick, commandInput, groupInput, descArea });
    }
  }

  /** 废弃堆：被否决过的候选，下次挖掘不再出现；也可以恢复成候选重新确认。 */
  function renderDismissed() {
    const pile = model.state.dismissed;
    if (!pile.length) {
      listWrap.append(el("div", "mine-empty", "废弃堆是空的。"));
      return;
    }
    for (const item of pile) {
      const feature = item.kind === "feature";
      const node = el("div", "candidate");
      const main = el("div", "candidate-main");
      const title = el("div", "row-title");
      const triggers = entryTriggers(item);
      const cmd = el("span", "cmd");
      if (feature) {
        cmd.append(document.createTextNode(triggers.join("、")));
      } else {
        triggers.forEach((trigger, index) => {
          if (index) cmd.append(el("span", "sep", "、"));
          cmd.append(el("span", "slash", "/"), document.createTextNode(trigger));
        });
      }
      title.append(cmd);
      title.append(chip(item.plugin || "未知插件"));
      const mark = markChip(item);
      if (mark) title.append(mark);
      if (item.at) title.append(chip(formatTime(item.at)));
      main.append(title);
      main.append(
        el("div", "row-desc", item.description || item.condition || "（无说明）"),
      );

      const tools = el("div", "row-tools");
      tools.append(
        button("恢复为候选", "btn ghost", () => {
          restoreDismissed(item.id);
          mode = "candidates";
          render();
          toast(`已恢复「${item.command}」，勾选后即可加入`);
        }),
        button("移出废弃堆", "btn ghost danger", () => {
          model.state.dismissed = model.state.dismissed.filter(
            (value) => value.id !== item.id,
          );
          markDirty();
          render();
          toast("已移出废弃堆，下次挖掘会重新出现");
        }),
      );
      node.append(main, tools);
      listWrap.append(node);
    }
  }

  function render() {
    rows = [];
    listWrap.replaceChildren();
    if (mode === "dismissed") renderDismissed();
    else renderCandidates();

    const mining = model.state.mining;
    action.textContent = mining.length ? "再次查找" : "开始查找";
    action.disabled = model.busy;
    pileBtn.textContent =
      mode === "dismissed" ? "返回候选" : `废弃堆 (${model.state.dismissed.length})`;
    pileBtn.classList.toggle("is-active", mode === "dismissed");
    status.textContent = mineStatusText();

    $("modal-ok").textContent = "加入所选条目";
    $("modal-ok").hidden = mode === "dismissed" || !mining.length;
    $("modal-note").textContent = mineNote();
  }

  openModal({
    title: "AI 挖掘未注册功能",
    body,
    wide: true,
    okLabel: "加入所选条目",
    onConfirm: () => confirmMine(rows),
  });
  render();
}

/** 挖掘页顶部状态：上次扫到多少、用的哪个模型、什么时候扫的。 */
function mineStatusText() {
  const meta = model.state.mine_meta || {};
  const parts = [];
  if (meta.at) parts.push(`上次 ${formatTime(meta.at)}`);
  if (meta.scanned) parts.push(`候选 ${meta.scanned} 条`);
  if (meta.model) parts.push(`模型 ${meta.model}`);
  if (meta.truncated) parts.push("候选较多已截断");
  return parts.join(" · ");
}

function mineNote() {
  if (model.state.mining.length) {
    return "勾选的会加入为自定义条目（请自行验证触发词）；未勾选的进废弃堆，下次挖掘不再出现";
  }
  return "挖掘结果会记在本页，关掉窗口也不会丢；未勾选的候选会进废弃堆";
}

/** 确认挖掘结果：勾选的加入自定义条目，未勾选的进废弃堆。 */
function confirmMine(rows) {
  const now = new Date().toISOString();
  const added = new Set();
  const dropped = new Set();
  let count = 0;

  for (const row of rows) {
    if (row.pick.checked) {
      const parsed = parseCommandInput(row.commandInput.value);
      if (!parsed.command) continue;
      model.state.custom.push({
        id: `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
        group: row.groupInput.value.trim() || row.candidate.plugin || "自定义",
        command: parsed.command,
        aliases: parsed.aliases,
        description: row.descArea.value,
        permission: "everyone",
        kind: row.candidate.kind === "feature" ? "feature" : "command",
      });
      added.add(row.candidate.id);
      count += 1;
    } else {
      dropped.add(row.candidate.id);
    }
  }

  for (const row of rows) {
    if (!dropped.has(row.candidate.id)) continue;
    if (model.state.dismissed.some((item) => item.id === row.candidate.id)) continue;
    model.state.dismissed.push({ ...row.candidate, at: now });
  }
  model.state.mining = model.state.mining.filter(
    (item) => !added.has(item.id) && !dropped.has(item.id),
  );

  closeModal();
  afterChange();
  persistState();
  const skipped = dropped.size;
  toast(
    count
      ? `已加入 ${count} 条${skipped ? `，${skipped} 条进废弃堆` : ""}`
      : skipped
        ? `${skipped} 条已进废弃堆`
        : "没有勾选任何条目",
  );
}

/** 把废弃堆里的一条恢复回候选列表，等用户重新确认。 */
function restoreDismissed(id) {
  const index = model.state.dismissed.findIndex((item) => item.id === id);
  if (index < 0) return;
  const [record] = model.state.dismissed.splice(index, 1);
  if (!model.state.mining.some((item) => item.id === record.id)) {
    model.state.mining.push(record);
  }
  markDirty();
}

async function runMineSearch(render) {
  if (model.busy) return;
  setBusy(true, "正在分析未注册功能…");
  try {
    const result = await bridge.apiPost("ai/mine", {});
    const candidates = result.candidates || [];
    model.state.mining = candidates;
    model.state.mine_meta = {
      scanned: Number(result.scanned) || 0,
      model: String(result.model || ""),
      truncated: Boolean(result.truncated),
      at: new Date().toISOString(),
    };
    await persistState();
    toast(
      candidates.length
        ? `扫到 ${candidates.length} 条候选，请逐条确认`
        : "没有发现可直接触发的未注册功能",
    );
  } catch (error) {
    toast(`AI 挖掘失败：${error.message}`);
  } finally {
    setBusy(false);
    render();
  }
}

async function runPolish(mode) {
  const entries = selectedEntries();
  if (!entries.length) {
    toast("请先勾选要处理的条目");
    return;
  }
  setBusy(true, mode === "polish" ? "正在润色说明…" : "正在翻译为中文…");
  let updated = 0;
  try {
    for (let start = 0; start < entries.length; start += AI_BATCH) {
      const batch = entries.slice(start, start + AI_BATCH);
      const result = await bridge.apiPost("ai/polish", {
        mode,
        items: batch.map((entry) => ({
          key: entry.key,
          command: entry.command,
          description: entry.description,
        })),
      });
      for (const [key, description] of Object.entries(result.items || {})) {
        if (applyDescription(key, String(description))) updated += 1;
      }
    }
  } catch (error) {
    setBusy(false);
    toast(`AI 处理失败：${error.message}`);
    return;
  }
  setBusy(false);
  toast(`AI 处理完成，更新 ${updated} 条说明`);
  if (updated) afterChange();
  else renderAll();
}

/* ------------------------------------------------------------------ 保存 / 重置 */

function setBusy(flag, message) {
  model.busy = flag;
  for (const id of [
    "btn-save",
    "btn-reset",
    "btn-ai-translate",
    "btn-ai-polish",
    "btn-mine",
  ]) {
    $(id).disabled = flag;
  }
  const status = $("status");
  status.textContent = flag ? message || "处理中…" : "";
  status.classList.toggle("busy", flag);
}

async function save() {
  setBusy(true, "正在保存…");
  try {
    const result = await bridge.apiPost("state", collectState());
    if (result && result.state) applyState(result.state);
    model.dirty = false;
    renderAll();
    toast("已保存");
  } catch (error) {
    toast(`保存失败：${error.message}`);
  } finally {
    setBusy(false);
  }
}

/** 静默落盘：挖掘记录这类数据需要立刻保留，但不必打断用户，所以不提示、不弹错。 */
async function persistState() {
  try {
    const result = await bridge.apiPost("state", collectState());
    if (result && result.state) applyState(result.state);
    model.dirty = false;
    renderAll();
  } catch (error) {
    console.error(error);
  }
}

let resetArmed = false;
let resetTimer = null;

function handleReset() {
  const node = $("btn-reset");
  if (!resetArmed) {
    resetArmed = true;
    node.textContent = "确认恢复默认？";
    resetTimer = setTimeout(() => {
      resetArmed = false;
      node.textContent = "恢复默认";
    }, 4000);
    return;
  }
  clearTimeout(resetTimer);
  resetArmed = false;
  node.textContent = "恢复默认";
  resetAll();
}

async function resetAll() {
  setBusy(true, "正在恢复默认…");
  try {
    const result = await bridge.apiPost("reset", {});
    if (result && result.state) applyState(result.state);
    model.selection.clear();
    model.dirty = false;
    renderAll();
    refreshPreview();
    toast("已恢复默认");
  } catch (error) {
    toast(`操作失败：${error.message}`);
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ 渲染与事件 */

function renderAll() {
  renderSummary();
  renderList();
  renderPanel();
  renderCanvasHead();
  renderTabs();
}

let toastTimer = null;

function toast(message, duration = 2400) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = duration > 0 ? setTimeout(hideToast, duration) : null;
}

function hideToast() {
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = null;
  $("toast").classList.remove("show");
}

function bindChrome() {
  $("tab-btn-content").addEventListener("click", () => setTab("content"));
  $("tab-btn-canvas").addEventListener("click", () => setTab("canvas"));
  $("btn-swap").addEventListener("click", () => setSwapped(!model.swapped));
  $("search").addEventListener("input", (event) => {
    model.search = event.target.value.trim();
    renderList();
  });
  $("btn-save").addEventListener("click", save);
  $("btn-reset").addEventListener("click", handleReset);
  $("btn-select-visible").addEventListener("click", () => {
    for (const entry of orderedEntries()) {
      if (matchesSearch(entry, model.search)) model.selection.add(entry.key);
    }
    renderSummary();
    renderList();
  });
  $("btn-clear-select").addEventListener("click", () => {
    model.selection.clear();
    renderSummary();
    renderList();
  });
  $("btn-ai-translate").addEventListener("click", () => runPolish("translate"));
  $("btn-ai-polish").addEventListener("click", () => runPolish("polish"));
  $("btn-mine").addEventListener("click", openMineModal);
  $("btn-new").addEventListener("click", openCustomCreator);
  $("btn-trash").addEventListener("click", openTrash);
  $("btn-preview").addEventListener("click", refreshPreview);
  $("auto-preview").addEventListener("change", (event) => {
    model.autoPreview = event.target.checked;
    if (model.autoPreview) schedulePreview();
  });
  $("modal-close").addEventListener("click", closeModal);
  $("modal-cancel").addEventListener("click", closeModal);
  $("modal-ok").addEventListener("click", () => {
    if (modalConfirm) modalConfirm();
  });
  $("modal").addEventListener("click", (event) => {
    if (event.target === $("modal")) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });
}

async function init() {
  bindChrome();
  try {
    const context = await bridge.ready();
    if (context && context.pageTitle) document.title = context.pageTitle;
  } catch (error) {
    console.error(error);
  }

  let data;
  try {
    data = await bridge.apiGet("state");
  } catch (error) {
    $("summary").textContent = `加载失败：${error.message}`;
    return;
  }

  model.scanned = data.scanned || [];
  model.schema = (data.schema && data.schema.sections) || [];
  model.appearance = { ...(data.appearance || {}) };
  model.appearanceBase = buildAppearanceBase(data.appearance_base || {});
  model.font = data.font || "";
  model.options = { ...model.options, ...(data.options || {}) };
  applyState(data.state);

  renderAll();
  refreshPreview();
}

/** 兜底外观 = Theme 默认值叠加插件配置项提供的值，用于判断某项是否被用户改过。 */
function buildAppearanceBase(configured) {
  const base = {};
  for (const section of model.schema) {
    for (const field of section.fields) base[field.name] = field.default;
  }
  for (const [name, value] of Object.entries(configured)) {
    if (value === undefined || value === null || value === "") continue;
    base[name] = value;
  }
  return base;
}

init();