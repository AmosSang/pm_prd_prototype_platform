# 契约测试 fixture 目录

存放三份契约的固定输入输出样例，供单元测试与 E2E 共用：

- `prd/` — 带锚点注释的 PRD markdown 样例（T3.3 锚点解析器用例时填充）
- `prototype/` — 带 data-pa 的原型 HTML 样例（T3.3 / T1.x 用例时填充）
- `reviews/` — 评论 JSON 样例（T4.1 payload schema 用例时填充）

T0.2 阶段先放骨架与一个最小 PRD 样例，验证 fixture 机制本身跑得通。
