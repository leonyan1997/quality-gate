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
# 检查所有语言
quality-gate check --diff

# 只检查 Rust
quality-gate check --diff --lang rust

# 指定检查类型（逗号分隔）
quality-gate check --checks lint,duplication,dependency

# 指定 diff 基线（CI: 对比远端主分支；默认 HEAD=本地未提交改动）
QUALITY_GATE_BASE=origin/main quality-gate check --diff

# 输出 JSON 报告
quality-gate check --diff --output report.json
```

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
- **scan** 是全仓快照：报告全部存量问题（lint/重复/依赖/CRAP）、**不阻塞**
  （无论多少问题 exit code 都是 0，工具级错误除外）
- 每次 scan 结果存档到 `.quality-gate/history/scan-<ts>.json`（毫秒级时间戳防同秒覆盖），
  与上次对比输出周报趋势：lint/重复块/CRAP 计数变化 + 新增/消失的高 CRAP 函数明细
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

### 已实现：scan 全仓扫描与覆盖率扩展

- ✅ TS/Python 覆盖率在 web/、tests/ 实机跑通（vitest/coverage 需项目自带配置）
- ✅ scan 全仓扫描命令落地（full 模式 + 存档 + 周报趋势对比）
- ✅ CRAP 趋势报告（函数级新增/消失对比）

### 阶段三（测试质量 · 规划中）

- 变异测试（TS 试点：tautest 增量 → 不过则回退 StrykerJS）
- `--mutation-report` 集成到 check --diff（默认关、非阻塞、3 分钟超时跳过）
- 变异测试周报（schedule workflow + artifact）

### 阶段四（精细化 · 规划中）

- 函数级 diff（Python ast / Rust syn / TS Compiler API，弃 Tree-sitter）
- CRAP 阻塞（阈值 30，新增函数 <5 行豁免）
- 函数级 allowlist（function_ignore）

## 配置文件

使用方项目根放一份 `quality-gate.yaml`（工具向上递归 5 层查找）：

```bash
cp quality-gate.yaml.example <你的项目>/quality-gate.yaml
```

配置项说明：

```yaml
languages: [rust, typescript, python]

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
```

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
- ✅ 配置文件加载 (quality-gate.yaml)
- ✅ scan 全仓扫描（full 模式 + report 存档 + 周报趋势 + CRAP 函数级明细）

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
