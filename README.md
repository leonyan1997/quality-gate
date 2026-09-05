# Quality Gate - 自动化代码质量保证工具

**增量门禁**：只卡新增/改动引入的问题，存量债务不阻塞。

**独立基础设施工具**：为所有代码项目（Rust / TypeScript / Python）服务，
不隶属任何业务仓库。使用方只需在项目根放一份 `quality-gate.yaml` 即可。

## 安装

```bash
git clone git@github.com:leonyan1997/quality-gate.git
cd quality-gate
python3 -m venv .venv
.venv/bin/pip install -e .

# 可选：全局命令（任何项目直接 quality-gate 调用）
ln -s "$(pwd)/.venv/bin/quality-gate" ~/.local/bin/quality-gate
```

### 依赖工具

#### Rust
```bash
rustup component add clippy
```

#### TypeScript
```bash
# 推荐（更快）
npm install -g oxlint
# 或
npm install -D eslint
```

#### Python
```bash
pip install ruff
```

#### 重复代码
```bash
npm install -g jscpd
```

#### 架构依赖检查（可选，未装时自动跳过）
```bash
cargo install cargo-deny          # Rust
npm install -g dependency-cruiser # TS
pip install import-linter         # Python
```

## 用法

### 增量检查（CI 核心命令）

```bash
# 检查配置声明的语言（quality-gate.yaml languages；缺省 = rust/typescript/python）
quality-gate check --diff

# 只检查 Rust（显式 --lang 覆盖配置）
quality-gate check --diff --lang rust

# 强制检查全部语言（覆盖 languages 配置）
quality-gate check --diff --lang all

# 指定检查类型（逗号分隔）
quality-gate check --checks lint,duplication,dependency

# 指定 diff 基线（CI: 对比远端主分支；默认 HEAD=本地未提交改动）
QUALITY_GATE_BASE=origin/main quality-gate check --diff

# 输出 JSON 报告
quality-gate check --diff --output report.json
```

语言选择语义：`check`/`scan` 只运行 `quality-gate.yaml languages` **声明**的
语言——纯 Python 项目配 `languages: [python]` 后不再尝试 clippy/oxlint 等
rust/ts 工具（也不会因缺工具误红）。显式 `--lang rust|ts|python` 覆盖配置；
`--lang all` 强制全语言（兼容 monorepo 里逐子目录 cd 后单语言检查）。

diff 基线说明：
- 默认 `HEAD` —— 本地预提交语义：只检查工作区相对上次提交的未提交改动
- CI 场景设置 `QUALITY_GATE_BASE=origin/main` —— 检查 PR 相对主分支的全部改动
- untracked 新文件始终按全文件视为新增（新增行=全文），不依赖基线

### 全仓扫描（周报）

```bash
# 全语言全仓扫描 + 存档 + 趋势对比
quality-gate scan

# 只扫 Python
quality-gate scan --lang python

# 报告写到指定文件（不存档）
quality-gate scan --output report.json

# 跳过历史存档
quality-gate scan --no-archive
```

scan 与 check 的区别:
- **check** 是增量门禁：git diff 过滤、只卡新增问题、exit 1 阻塞
- **scan** 是全仓快照：报告全部存量问题（lint/重复/依赖/CRAP/smell）、**不阻塞**
  （无论多少问题 exit code 都是 0，工具级错误除外）
- 每次 scan 结果存档到 `.quality-gate/history/scan-<ts>.json`（毫秒级时间戳防同秒覆盖），
  与上次对比输出周报趋势：lint/重复块/CRAP/smell 计数变化 + 新增/消失的高 CRAP 函数明细
- 趋势对比的 baseline 在保存前取，避免刚存的报告恒为"最新"导致对比失效

## 检查项

### 已实现：MVP 检查能力

| 检查项 | Rust | TS | Python | 说明 |
|--------|------|----|--------|------|
| Lint | ✅ clippy | ✅ oxlint/eslint | ✅ ruff | 增量行过滤 |
| 重复代码 | ✅ jscpd | ✅ jscpd | ✅ jscpd | 语言无关，新增行过滤 |
| 依赖检查 | ✅ cargo-deny | ✅ depcruise | ✅ import-linter | 全局阻塞，无配置自动跳过 |
| 覆盖率 | ✅ tarpaulin | ✅ vitest | ✅ coverage.py | 新增文件 >0%（TS 基于 json-summary，Python 基于 coverage.json） |
| 圈复杂度 | ✅ clippy | ✅ eslint | ⏸️ radon | 新增文件阻塞（Python CRAP 报告不阻塞） |
| CRAP 报告 | ⏸️ | ⏸️ | ✅ radon | 当前仅报告（crap-index 已有） |
| 结构坏味道 | ⏸️ | ⏸️ | ✅ AST 规则 | 8 条规则引擎；增量 P0/P1 阻塞、P2 仅报告 |

### 已实现：scan 全仓扫描与覆盖率扩展

- ✅ TS/Python 覆盖率在 web/、tests/ 实机跑通（vitest/coverage 需项目自带配置）
- ✅ scan 全仓扫描命令落地（full 模式 + 存档 + 周报趋势对比）
- ✅ CRAP 趋势报告（函数级新增/消失对比）

### 已实现：结构坏味道引擎（smell）

Python 结构坏味道由内置 AST 规则引擎检测（8 条规则：long-method /
large-class / long-parameter-list / dead-import / dead-code /
switch-statements / data-class / lazy-class），三条命令的行为差异：

| 命令 | smell 行为 |
|------|-----------|
| `check --diff` | ✅ 增量门禁：只评估 diff 触及的函数/类体与命中 diff 的 def/import 行。P0/P1（long-method / large-class / long-parameter-list / dead-import）进 issues 阻塞，exit 1；P2（其余 4 条）仅报告不阻塞 |
| `scan` | ✅ 全仓快照：`full` 模式报告全部存量坏味道（含 P2），不阻塞（exit 0）；计数入 `.quality-gate/history` 存档与周报趋势 |
| `fix` | ❌ 不处理：结构问题需要人工重构判断，自动修复只覆盖 lint 级问题 |

增量语义说明（check --diff）：

- **行级规则**（long-parameter-list / dead-import）：def/import 行被 diff
  命中才上报——存量超参函数只改函数体不会重报
- **块级规则**（long-method / large-class / lazy-class / data-class /
  switch-statements / dead-code）：diff 触及该函数/类体即重算整块——
  往超长函数里加代码会立即被卡
- 新增/untracked 文件按全文件评估（diff = 全文，天然全量）
- 存量坏味道在 diff 外 → 永不阻塞，债务自然收敛

严重度映射：P0 = long-method；P1 = large-class / long-parameter-list /
dead-import；P2 = lazy-class / dead-code / data-class / switch-statements。

dead-import 语义（对齐 pyflakes/ruff F401）：模块级 `__all__` 中声明的名字，
以及 `import x as x` / `from y import x as x`（PEP 484 显式 re-export 标记），
视为公共 API 导出，不算未使用 import；不在 `__all__` 保护内的未使用 import
照报。re-export 模式（如包门面的 `__init__.py`）不再误报。

### 阶段三（测试质量 · 规划中）

- 变异测试（TS 试点：tautest 增量 → 不过则回退 StrykerJS）
- `--mutation-report` 集成到 check --diff（默认关、非阻塞、3 分钟超时跳过）
- 变异测试周报（schedule workflow + artifact）

### 阶段四（精细化 · 部分启用）

- ✅ 函数级 allowlist（`function_ignore`，已接线）：长而聚焦的函数按名挂账，
  豁免 long-method / long-parameter-list 上报（详见下方配置说明）
- 函数级 diff（Python ast / Rust syn / TS Compiler API，弃 Tree-sitter）
- CRAP 阻塞（阈值 30，新增函数 <5 行豁免）

## 配置文件

使用方项目根放一份 `quality-gate.yaml`（工具向上递归 5 层查找）：

```bash
cp quality-gate.yaml.example <你的项目>/quality-gate.yaml
```

配置项说明：

```yaml
languages: [rust, typescript, python]  # 声明语言才跑；缺省 check/scan 只查这里声明的语言

thresholds:
  crap: 30                     # 阶段三启用
  cyclomatic_complexity: 15    # 圈复杂度阈值
  duplication: 3               # 新增重复行百分比

coverage_ignore:
  paths:
    - "**/models.py"           # 豁免纯数据类
    - "**/generated/**"

lint_ignore:
  paths:
    - "**/node_modules/**"
    - "**/target/**"

function_ignore:
  - "long_legacy_fn"            # 函数级豁免（C2 已接线）：长而聚焦函数按名挂账，
                                # 豁免 long-method / long-parameter-list 上报

smell:
  max_function_lines: 60      # 函数体超过 → long-method（P0 阻塞）
  max_class_methods: 10       # 类方法数超过 → large-class（P1 阻塞）
  max_class_lines: 300        # 类行数超过 → large-class
  max_parameters: 5           # 参数超过 → long-parameter-list（P1 阻塞）
  max_switch_branches: 3      # 分支超过 → switch-statements（P2 报告）
  min_lazy_class_methods: 2   # 方法数 ≤ → lazy-class（P2 报告）
  enabled_rules: []           # 只启用指定规则（空 = 全部启用）
  disabled_rules: []          # 排除指定规则（空 = 不排除）
  ignore:
    paths:
      - "tests/smell/fixtures/**"   # smell 专属豁免（故意样本/测试桩）
```

smell 段可省略——未配置时全部阈值用引擎内置默认（上例数值即默认值）。
各命令下的 smell 行为差异见「已实现：结构坏味道引擎（smell）」一节；
完整规则与阈值定义见 `src/quality_gate/smell/`。

smell 豁免路径（`smell.ignore.paths`）与 `lint_ignore.paths` 语义解耦：lint
豁免不该管 smell，故意样本/测试桩（按设计要触发规则的 fixtures）在
checker 层按 `smell.ignore.paths` 显式豁免；实际生效集合 =
`lint_ignore.paths` ∪ `smell.ignore.paths`（并集，向后兼容）。

函数级豁免 `function_ignore`（顶层，已接线）：长而聚焦的函数（机械拆分
伤连贯的单一算法）按**函数名**挂账，豁免其 long-method /
long-parameter-list 上报；不在清单中的函数照报。注意按纯函数名匹配——
多文件同名会全豁免，若有同名函数请改用拆分为上。

## 门禁语义（可靠性契约）

对 AI 把关场景，**"没真查却显示绿"比报错更危险**。quality-gate 遵循：

- **核心检查项（lint / duplication / smell）——查不了就报错**：已声明语言的
  工具缺失（ruff/clippy/oxlint+jscpd）→ error 级 issue + exit 1，绝不静默通过
- **扩展检查项（coverage / dependency / 复杂度 CRAP）——查不了就明说**：
  工具/项目配置缺失 → `⏭️ skipped` + 机器可读 reason（stdout 与 JSON 报告均
  可见），exit 0；radon 缺失/超时/解析失败不再裸返回空结果
- 依赖检查在无架构配置（deny.toml/.dependency-cruiser.js/.import-linter）时
  skipped 属**项目配置选择**而非工具缺陷——按需在 CI 装齐工具后即会真查

## CI 集成

### GitHub Actions 示例

```yaml
name: Quality Gate

on: [push, pull_request]

jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy
      
      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install tools
        run: |
          pip install ruff
          npm install -g oxlint
          git clone https://github.com/your-org/quality-gate.git /tmp/quality-gate
          cd /tmp/quality-gate && python3 -m venv .venv && .venv/bin/pip install -e .
      
      - name: Run tests (CI 独立步骤)
        run: |
          cargo test
          npm test
      
      - name: Quality Gate
        run: |
          /tmp/quality-gate/.venv/bin/quality-gate check --diff
```

完整的 CI 模板见 `.github/workflows/quality-gate.yml.example`（使用方复制改
<YOUR_ORG> 即可，语言 step 按需裁剪）。

## 合并门禁（Branch Protection）

master 分支建议启用原生 Branch Protection，required status checks 勾选
`Quality Gate` 与 `Test` 两个 job（job name 已与 workflow 对齐）。

轻量维护（无 branch protection 或 PR 流程的小仓库）可用「纪律 + push 兜底」：

- **推送 master 前必须本地跑绿**（约定，硬要求）：
  ```bash
  .venv/bin/python -m pytest tests/ -q
  .venv/bin/quality-gate check --diff       # exit 0
  ```
- **push 到 master 后 CI 自动兜底**：workflow 已配置 `push: branches: [master]`
  触发 Quality Gate + Test 两个 job，红了在 Actions 页可见，立即回退或修复。
- 大改动走 feature 分支 + PR（CI 跑 `pull_request` 事件），合并前确认两个
  check 绿。

## 输出格式

### JSON 报告

```json
{
  "success": false,
  "diff_mode": true,
  "results": {
    "rust": {
      "blocking": true,
      "issues": [
        {
          "file": "src/api.rs",
          "line": 13,
          "column": 5,
          "level": "warning",
          "code": "unused_imports",
          "message": "unused imports: `LogLevel` and `TaskStatus`"
        }
      ],
      "all_issues": [...],
      "diff_ranges": {
        "src/api.rs": [[13, 15]]
      }
    }
  }
}
```

### 命令行输出

```
🔍 检查 Rust 代码...
  运行 clippy...
  解析 clippy 输出...
  应用 diff 行过滤...
  发现 27 个问题，其中 27 个在 diff 范围内

============================================================
质量门禁检查结果:
  RUST: ❌ 阻塞
    发现问题：27 个
      - src/api.rs:13 unused imports: `LogLevel` and `TaskStatus`
      - src/task.rs:339 this expression creates a reference...

❌ 质量门禁失败 - 请修复上述问题
```

## 开发路线图

### 已落地（v2.0 + scan）
- ✅ Rust/TS/Python Lint 增量检查
- ✅ 重复代码检测 (jscpd)
- ✅ 架构依赖检查 (cargo-deny / depcruise / import-linter)
- ✅ 覆盖率 (Rust tarpaulin / TS vitest / Python coverage.py)
- ✅ 圈复杂度 + Python CRAP 报告
- ✅ 配置文件加载 (quality-gate.yaml，含 smell 阈值段)
- ✅ scan 全仓扫描（full 模式 + report 存档 + 周报趋势 + CRAP 函数级明细）
- ✅ 结构坏味道引擎（smell：8 条 AST 规则，check --diff 增量门禁 + scan 全量报告）

### 阶段一：门禁生效（当前）
- [x] GitHub Actions workflow（本仓库 dogfood 版 + 使用方模板）
- [x] fork PR base 获取幂等处理
- [x] Branch Protection —— 改用纪律替代（见「合并门禁」，GitHub Free + Private 无 required checks）
- [ ] --format markdown（增强，阶段一收尾后补）

### 阶段二：效率工具
- [ ] quality-gate fix（Python ruff 自动修 / TS oxlint+eslint / Rust 仅建议）
- [ ] --check 预览模式 + --language 参数 + ignore.paths 读取

### 阶段三：测试质量（TS 试点）
- [ ] 变异测试增量模式（tautest --since → 不过则 StrykerJS）
- [ ] --mutation-report（默认关、非阻塞、3 分钟超时）
- [ ] 变异测试周报 workflow（artifact 存储）

### 阶段四：精细化（按需）
- [ ] 函数级 diff（Python ast / Rust syn / TS Compiler API，弃 Tree-sitter）
- [ ] CRAP 阻塞（阈值 30，新增函数 <5 行豁免）+ 函数级 allowlist

## 设计原则

1. **增量门禁**：只卡新增问题，存量债务自然收敛
2. **机器能修的绝不让人看**：`--fix` 自动修复优先
3. **职责分离**：测试由 CI 独立步骤保证，quality-gate 专注静态质量
4. **AI 生成代码必须被自动验证**：形成"AI 写 → 自动查 → 自动修 → 门禁卡"闭环

## 故障排查

### clippy 执行超时

默认超时 5 分钟，大型项目可能超时。可在 `check_rust_lint_incremental` 中调整 `timeout` 参数。

### oxlint 找不到

确保已安装：`npm install -g oxlint` 或使用项目本地安装。

### diff 行过滤不准确

确保在 git 仓库根目录或子目录运行，且有未提交的改动或在正确的分支上。

## 许可证

GPL-3.0
