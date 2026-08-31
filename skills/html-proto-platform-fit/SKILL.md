---
name: html-proto-platform-fit
description: 让 HTML 原型适配「产品方案展示平台」：新做或改造原型时，使 DOM 结构友好支持后续 PRD↔原型锚点双向联动、评论定位、截图红框与自动揭示（文档定位时自动打开弹窗/Tab/折叠区）。触发词：HTML 原型、交互原型、适配平台、原型改造、双向联动、锚点联动前置。职责边界：本技能不打 data-pa 锚点、不做对账检查（那是 prd-html-anchor 的职责），也不负责把原型本身做得更好，只补充「什么样的结构能被平台友好消费」的必要约束与改造方法。
agent_created: true
---

# HTML 原型适配产品方案展示平台（锚点联动前置）

## 0. 职责边界（先读这个）

| 本技能做 | 本技能不做 |
|---|---|
| 告诉 AI：什么 DOM 结构能被平台「找得到、看得见、唯一存在」 | 打 `data-pa` / PRD 侧 `<!-- pa: -->`（→ 技能 `prd-html-anchor`） |
| 新做原型时的结构约束与代码范式 | 锚点对账 / 孤儿检查（→ `prd-html-anchor` 的 `check_orphan_anchors.py`） |
| 已有原型的适配改造（审计→修改→自检） | 评价原型本身的视觉/交互质量 |
| 排除会让平台联动失效的写法 | 教学 SPA 框架 / 工程化 |

**平台如何消费原型（30 秒背景，解释下面所有规则的 why）**：原型被 zip 上传后，平台把它放进沙箱 iframe 展示，并向每个 HTML 页面**注入桥接脚本 bridge.js**。bridge 靠 postMessage 与宿主通信，承担：锚点 icon 与上报、反向定位（滚动+闪烁）、评论元素采集、整页截图。后续标注锚点时（另一技能），平台还需要「文档定位 → 原型自动打开弹窗/Tab/折叠区 → 滚动到元素」的**揭示流水线**——它的机械依据就是本技能要求的 `aria-controls` 关联与常驻 DOM。任何破坏 iframe 内正常 JS 执行、或让元素「不在 DOM 里 / 不可见且无法打开」的写法，都会让上述能力失效。

---

## 1. 四条铁律（新做原型的硬约束）

### 1.1 静态优先：能用纯 HTML 就不用 JS 生成

- 页面骨架、核心内容直接写在 HTML 里——平台的锚点对账是**服务端静态解析**（BeautifulSoup），JS 运行时才出现的元素它看不到。
- JS 只用于：数据填充、交互反馈、真正运行时才确定的内容。

### 1.2 常驻 DOM：交互状态「切换显隐」，禁止「销毁重建」

- 弹窗、抽屉、Tab 面板、折叠区内容**常驻 DOM**：关闭 = 加 `hidden` / 切 class，**绝不 `remove()` 后重建**。
- ❌ `panel.remove()` → 之后 `insertAdjacentHTML()` 重建
- ✅ `panel.hidden = true` / `panel.classList.toggle('is-open')`
- 原因：销毁重建让元素「关闭后从 DOM 里消失」，后续锚点与揭示都会断链。

### 1.3 标准关联：开合控件声明「谁控制谁」+ 容器有 id

Tab、手风琴、弹窗、抽屉、浮层的触发控件统一写标准属性，目标容器必须有稳定 `id`：

```html
<!-- Tab -->
<button role="tab" aria-controls="panel-a" aria-selected="true">Tab A</button>
<div id="panel-a" role="tabpanel">…</div>

<!-- 弹窗 / 浮层（绝对定位小浮层同样适用） -->
<button aria-controls="confirm-dialog" aria-expanded="false">删除</button>
<div id="confirm-dialog" class="modal" hidden>…</div>

<!-- 折叠区：优先用原生 details/summary（免写关联） -->
<details><summary>高级设置</summary>…</details>
```

这是平台揭示流水线的机械依据：bridge 靠 `aria-controls="容器id"` 确定性地找到「哪个控件负责打开它」，程序化点击即可自动揭示。没有它，藏在弹窗/Tab/浮层里的元素定位时只能降级报错。

### 1.4 真实触发器：开合控件的 handler 必须真的驱动显隐

平台揭示是**程序化 `el.click()`**（隐藏元素也能触发 handler）。因此每个开合控件的 onclick 绑定的函数必须真实切换目标容器的显隐——原型演示里常见的「假触发器」会让揭示静默失败：

- ❌ `<a onclick="return false">`（纯展示、不切页）
- ❌ 点击后只 `showToast('演示')` 不改 DOM
- ✅ `onclick="go('community')"` 且 `go()` 真实切换容器显隐

---

## 2. 页面与导航形态

**多页原型（推荐，成熟链路）**
- 每个页面独立 `.html`（如 `pages/login.html`），页面间 `<a href>` 跳转；zip 根顶层要有一个 `.html`（否则平台按目录壳下钻剥层）。
- 平台跨页定位 = 换 iframe src 等 READY，已验证稳定。

**单文件 SPA 假页（可用，需自觉）**
- 用 `.screen{display:none}` + `.screen.on{display:block}` 之类的「假页」+ JS `go('page')` 切换是允许的形态。要求：
  - 切换函数真实驱动显隐（铁律 1.4），支持 `hashchange`/`pushState` 更佳（bridge 会自动上报路由）；
  - **每个假页都要有自己的入口触发器**：页内导航/侧边栏里必须有控件能切到它（平台揭示会去点它）；同一假页被复制多份侧边栏时，保证「页面上存在至少一个能打开它的真实控件」即可；
  - 假页内容仍写静态 HTML（铁律 1.1），动态挂载的内容留给后续标注时用 `setAttribute` 注入。

**禁止**
- `window.open` 承载核心流程——新窗口在平台 postMessage 链路之外，一切联动失效。
- `document.write` 整页重写（含 head 内）——会连平台注入的桥接脚本一起销毁，无法自愈。
- 虚拟列表 / 懒渲染承载核心内容——锚点会间歇性不在 DOM。原型阶段直接全量渲染。
- 非常规隐藏：`opacity:0 + pointer-events:none`、`position:absolute; left:-9999px`——平台可见性判定可能误判。用 `hidden`、`display:none`、未展开 `<details>` 这些标准形态。

---

## 3. 已知踩坑实录（改造时的高频雷区，均来自真实项目）

1. **伪触发器**：`onclick="return false"` 的导航项人工点看不出问题（鼠标点了没反应=正常没反应），但揭示流水线点了它什么都不会发生。审计时重点排查所有「看起来能点但 handler 不改 DOM」的元素。
2. **querySelector 误选中**：如页面里有两个 `.nt-box`（游戏输入框 + 日常输入框），`document.querySelector('.nt-box')` 永远拿第一个——切换显隐时操作错对象。用更精确的选择器（`:not(.xxx)`）或 id。
3. **模板串嵌套引号**：在 JS 模板字符串里拼 `onclick="fn()" aria-controls="id"` 时，双引号容易与外层 HTML 属性的双引号嵌套冲突，生成坏 HTML（浏览器报 `Unexpected identifier`）。对策：拼 HTML 片段时属性值用单引号，或把 aria 属性放在 onclick 属性之外。
4. **JS 里查询锚点要写不带引号的属性选择器**：`document.querySelector('[data-pa=xxx]')`——带引号版本会被对账脚本按「锚点出现」计数，造成重复误报（这是为后续 `prd-html-anchor` 技能铺路，现在就养成习惯）。
5. **公共模块复制 N 份**：SPA 多假页各自带一份侧边栏是常见写法，允许；但要知道后续打锚时同一逻辑元素只打第一份（`prd-html-anchor` 的规则），结构上尽量让各份拷贝 DOM 一致，避免对账困惑。
6. **全局 click 关闭浮层**：`document.addEventListener('click', ()=>pop.classList.remove('open'))` 这类「点外部关闭」与平台采集点击会互相干扰（评论模式点击会关掉浮层）。可接受，但要知道这个行为存在。

---

## 4. 场景一：新做原型（工作方式）

按第 1、2 节约束直接生成。生成后自检（不涉及锚点）：

- [ ] 每个 `.html` 能独立打开，控制台零报错（原型自身脚本报错会让桥接脚本连带失效）
- [ ] 所有弹窗/抽屉/Tab/浮层内容常驻 DOM，关闭只切显隐
- [ ] 所有开合控件有 `aria-controls="容器id"`（或用原生 `<details>`），容器有 id
- [ ] 每个假页/弹层都存在至少一个能真实打开它的控件（handler 真实驱动显隐，无 `return false` 伪触发器）
- [ ] 无 `document.write`、无 `window.open` 核心流程、无虚拟列表、无非常规隐藏
- [ ] 隐藏形态只用了：`hidden` 属性 / `display:none` / 未展开 `<details>`

---

## 5. 场景二：改造已有原型（审计 → 修改 → 自检）

对已有 HTML 按此顺序审计并修复（**全程不加 `data-pa`、不跑对账**）：

1. **全局风险扫描**（grep 级）：`document.write`、`window.open`、虚拟列表/懒渲染迹象 → 有则改（核心流程移回 iframe 内 / 全量渲染）。
2. **隐藏形态审计**：找 `display:none` / `hidden` / `.open`/`.show` 类切换，确认弹层、Tab 面板、抽屉内容常驻 DOM（关闭只切显隐）。销毁重建 → 改为常驻+显隐。非常规隐藏（9999px、opacity:0）→ 改标准隐藏。
3. **触发器关联审计**：对每个可开合容器：
   - 容器无 `id` → 补稳定 id；
   - 找到「负责打开它的控件」→ 补 `aria-controls="容器id"`（ Tab/手风琴顺带补 `role` 与 `aria-expanded/aria-selected`）；
   - 折叠区优先改造成原生 `<details>/<summary>`；
   - **验证 handler 真实性**：控件 onclick 是否真的切换该容器显隐；`return false` / 只 toast 的伪触发器 → 接上真实显隐逻辑。
4. **假页审计**（SPA 单文件）：每个假页有入口控件且 `go()` 类函数真实切换；导航按钮 `aria-controls` 指向假页根元素。
5. **JS 质量修复**：querySelector 误选中（加 `:not()` 或换 id）、模板串引号嵌套错误、JS 内锚点查询用不带引号选择器（为后续打锚铺路）。
6. **浏览器实测**（强烈建议，静态审计看不出运行时问题）：无头浏览器加载原型，逐个「模拟揭示」——对每个开合容器执行 `document.querySelector('[aria-controls=id]').click()` 后断言容器可见。这一步能抓出伪触发器、JS 报错、选择器误选等静态检查漏掉的问题（真实案例：改造 2116 行 SPA 原型时，靠这一步挖出 3 个静态审计看不见的真 bug）。
7. **控制台零报错**确认。

修改纪律：只做上述结构适配，不改视觉、不改交互含义、不删功能；改动保持最小。

---

## 6. 快速参考卡（拼进生成 prompt 的精简版）

> 该原型将上传到「产品方案展示平台」，需支持 PRD↔原型锚点双向联动，遵守：
> 1. 页面骨架写在静态 HTML；JS 只做数据填充与交互反馈。
> 2. 弹窗/抽屉/Tab/浮层内容常驻 DOM，关闭只切换 hidden/class，禁止 remove 后重建。
> 3. 开合控件必须写 `aria-controls="目标容器id"`（Tab 另加 role/aria-selected，弹窗加 aria-expanded）；容器必须有稳定 id；折叠优先用 `<details>/<summary>`。
> 4. 触发控件的 handler 必须真实切换目标显隐，禁止 `return false` 或只弹 toast 的假触发器。
> 5. 多页用独立 html + `<a href>`；单文件假页需每页有真实入口控件。核心流程禁止 window.open 与 document.write；不用虚拟列表；隐藏只用 hidden/display:none/details 未展开。
> 6. JS 内查询锚点属性用不带引号选择器（`[data-pa=xxx]`），为后续标注留好习惯。
>
> （锚点标注本身由 `prd-html-anchor` 技能负责，本阶段不打 data-pa。）
