# POC 结论报告：沙箱 iframe 内原型截图

> 任务卡 T1.3 产出 · 验证周期：2026-08-21 ~ 2026-08-23 · 结论等级：**已定型，进入正式功能开发**

## 一、结论速览

| 验证项 | 结论 | 依据 |
|--------|------|------|
| 截图库选型 | **modern-screenshot 4.6.5**（平台自托管 vendor） | html2canvas 在沙箱内原理性不可用（见 §2.1） |
| 沙箱兼容性 | 可用，需「假 sandbox」绕法 | E2E 全绿（见 §2.2） |
| 弹窗等交互状态保留 | ✅ 保留 | 场景二 E2E：弹窗打开瞬间截图，弹窗与红框同时入图 |
| 长页滚动补偿 | ✅ 正确 | 场景三 E2E：滚到底部截图 540x1391，红框与目标元素对齐 |
| 红框标注精度 | ✅ 达标（≤1px 渲染偏移） | 染色法像素级断言：红框四边套住目标元素（T1.2 用例，含 margin≠0 场景） |
| 整页尺寸上限 | 一期场景无压力 | ~1400px 高长页 5s 内完成；10MB 上传上限为兜底约束 |

**换库决策**：产品方案 8.1 预设的「html2canvas 不达标则换 modern-screenshot」预案已触发并落地，无需引入服务端无头浏览器预案。

## 二、关键发现记录

### 2.1 html2canvas 淘汰原因（原理性，非保真度）

html2canvas 的实现方式是把文档克隆进一个隐藏 iframe 中渲染。在平台沙箱（`sandbox` 属性不含 `allow-same-origin`）下：

- 沙箱文档内的**任何嵌套 iframe 都继承 sandbox 标志**，成为新的不透明 origin；
- 库读取嵌套 iframe 的 `contentDocument` 被浏览器同源策略直接拦截；
- 用 Playwright 逐项探测验证：`about:blank` / `srcdoc` / 无 src 三种嵌套 iframe **全部不可读**，无绕行空间。

### 2.2 modern-screenshot 沙箱绕法（已固化进 bridge.js）

modern-screenshot（SVG foreignObject 纯内存克隆）默认路径同样依赖隐藏 iframe 取「元素默认样式表」。绕法：

```
createContext() 后 → context.sandbox = { contentWindow: null }
```

库对 sandboxWindow 判空后返回空样式表，走正常分支不抛异常。

**绕法的两个副作用及修正**（均已修复并有 E2E 防回归）：

1. **默认样式表为空 → 库用 `!important` 强制克隆 body 拉满画布尺寸**，body margin≠0 时布局整体错位（实测偏 +16,+20px）。修正：`onCloneNode` 把真实计算值（margin + width + height 三件套，只写 margin 不够）写回克隆 body。
2. 由此产生的红框偏移 bug（偏左上 8px）被「人眼验收 → 染色法像素断言」闭环修复，断言保留在 T1.2 用例集中。

### 2.3 沙箱通信协议结论（T1.1，随附）

- iframe 的 `event.origin` 为字符串 `"null"`，宿主不能按具体 origin 校验 → 采用 **URL nonce 认证**（src hash 携带 nonce，消息回带校验）；
- 宿主 → iframe 发消息 `targetOrigin` 用 `'*'`（写 `'null'` 在 Chrome 抛异常），安全性由 bridge 侧 referrer origin 校验 + nonce 兜底。

## 三、三场景验证明细

| 场景 | fixture | 断言 | 结果 |
|------|---------|------|------|
| 登录页（基础） | `tests/fixtures/prototype/pages/login.html` | 整页渲染 + 红框套住 #captcha | ✅ |
| 带弹窗 | `tests/fixtures/prototype/pages/modal.html` | 弹窗打开瞬间截图，弹窗入图且红框套住弹窗内按钮 | ✅ |
| 长页滚动 | `tests/fixtures/prototype/pages/scroll.html` | 整页高 1391px > 视口；滚后红框随文档坐标对齐 #remark | ✅ |

用例位置：`tests/e2e/poc.spec.ts`（随 `make smoke` 常驻回归）。

## 四、对后续开发的影响

1. **阶段 4 评论系统**的「提交评论自动截图」直接复用 T1.2 链路，无新增技术风险；
2. bridge.js 头注已完整记录绕法与副作用，阶段 3/4 开发不得改动 `captureFullPage` 的沙箱适配段与 `onCloneNode` 三件套；
3. 若未来出现更复杂 CSS（Web 字体、跨域图片）导致保真度问题，预案是开启 modern-screenshot 的 font embedding 与 fetch 配置，而非换库；
4. 每项目截图存储走 `/data/shots/`，一期无清理策略（评论删除时同步删文件，见 T4.4）。
