# AI 写码守则：quality-gate 默认使用约定

> 给 AI 协作会话（及人工复核）读。核心一句话：
> **凡涉及 python / typescript / rust 的代码改动，收尾必过 quality-gate；
> `check` 阻塞为 0 才允许交付。**

## 何时执行

任何 python / typescript / rust 代码改动，包括：

- **quality-gate 自身**（dogfood：写工具的人用自己的工具把关）
- **真实使用项目**（如 novel-indexing：rust / ts / python 混仓）

## 固定收尾三步

1. **自动修**：`quality-gate fix`
   - python → `ruff --fix`；ts → `oxlint --fix`（工具缺失回退 `eslint --fix`）；
     rust 仅输出建议、不自动改文件
   - 默认只动 diff 内文件；整仓用 `--all`（自动跳过 node_modules/.venv/target）
2. **门禁复核**：`quality-gate check --diff`
   - 阻塞必须为 0；有阻塞 → 修到绿才交付，**不许带病提交**（exit 1 是硬语义）
   - 常用完整子集：`--checks lint,duplication,dependency,complexity,smell`
   - 纯 python 项目可配 `languages: [python]` 只查声明语言
3. **阶段存档**：`quality-gate scan`
   - 全仓快照 + 趋势对比存档（`.quality-gate/history/`），让存量债务方向可见

## 原则

- **语言诚实**：只查 `quality-gate.yaml languages` 声明的语言；查不了的工具
  明说 skipped + 原因，不装懂、不静默。
- **增量哲学**：只卡新增问题；存量债务不阻塞、自然收敛，不借机大改存量代码。
- **机器优先**：能自动修的不让人看；duplication / dependency / smell 需人工
  判断 → `fix` 不碰，留给 `check` 报告。
- **合入纪律**：改动真实使用项目（如 novel-indexing）前先征求用户同意；
  本地绿 ≠ 一定绿，CI 用同款子集复核（`--checks` 同一子集）。

## dogfood 红线（quality-gate 自身仓库）

- 新函数 **≤5 参数、≤60 行**（自身 smell P0/P1 会真卡，超了先重构再提交）。
- 新测试代码用共享夹具（`tests/_git_helpers.py`），一行一 git 调用，防 duplication 误伤。
- 改动后必须自身回归：pytest 全绿 + `ruff check src tests` 干净 +
  `check --diff` 阻塞 0。
