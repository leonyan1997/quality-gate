# Rust/TS 结构坏味道 + CRAP：现成工具盘点（spike · 2026-09）

> 状态：**调研盘点，不写实现代码**。产出「现成工具 × 8 条规则映射 + 误报/接入评估 +
> 可行方案建议」，供下一步决策（做 / 不做 / 怎么做 / 分期）。

## 背景与目标

quality-gate 的检查矩阵里，**CRAP 报告与结构坏味道目前只有 Python ✅**
（自研 AST 规则引擎 8 条 + radon CRAP），Rust/TS 两列 ⏸️。此前判断"成熟工具
不足以直接嵌入增量门禁"，故未做。用户疑问：**Rust/TS 难道没有现成成熟工具吗？**
本 spike 用证据回答：每个工具**真实形态**（默认开关、阈值可配性、行号粒度、
误报、成熟度、与 oxlint/clippy 引擎的关系），再决定是否值得做、怎么做。

## 接入约束：为什么"现成工具"不能拿来就用

quality-gate 的增量门禁有 4 条硬约束，决定了可接入工具的形态：

1. **函数/类粒度行号**：判定"只卡 diff 新增问题"，需要工具给出函数/类起止行，
   与 git diff 相交；function_ignore 按函数名豁免。
2. **严重度分级**：P0/P1 阻塞（exit 1）、P2 仅报告。
3. **低误报**：AI 协作场景，误报即噪音；查不了宁可口径化 skipped，不装懂。
4. **阈值可配**：对齐自有阈值（函数 60 行、参数 5 个、类 300 行等）。

主流"成熟工具"（SonarQube 全家桶、clippy 规则、eslint 规则集）多为
**整仓报告型 / 规则型**：能报"哪个函数超标"，但不带"是否 diff 新增"的增量判定
协议。所以接入 = 工具负责"定位+度量"，quality-gate 负责"增量判定+分级+豁免"。
关键问题是：工具能否**只报规则、带行号 span、阈值可配、误报可控**。

## 本地基准（Python 侧已实现，作为映射参照）

| 规则 | 严重度 | Python 判定 | 备注（跨语言含义差异） |
|------|--------|-------------|------------------------|
| long-method | P0 | 函数体 >60 行 | 语言无关，直接映射 |
| long-parameter-list | P1 | 参数 >5 个 | 语言无关 |
| large-class | P1 | 类 >300 行 | Rust/TS 类少，价值待估 |
| dead-import | P1 | 未使用 import（`__all__`/PEP484 re-export 豁免） | 各语言 import 语义不同 |
| dead-code | P2 | 顶层 `_` 前缀项零引用 | Python 保守语义，跨语言需重定 |
| switch-statements | P2 | match/switch 分支 >3 | Rust: match 分支；TS: switch |
| data-class | P2 | 仅字段赋值类 → 建议 @dataclass | **Python 习语**，TS/Rust 含义需重定义 |
| lazy-class | P2 | ≤2 个 ≤3 行简单方法的类 | TS/Rust 类生态弱，价值待估 |
| CRAP 报告 | 周报 | 复杂度²×(1-覆盖率)³+复杂度（radon） | 需复杂度+覆盖率合成 |

> 注意：data-class / lazy-class / large-class 是**类生态**坏味道；Rust/TS
> 现代代码大量用 struct/函数式而非类，照搬 Python 规则会产生误报或空转。

## Rust 侧盘点（clippy 等）

| 工具/lint | 映射规则 | 默认开/关 | 阈值可配 | 行号 span | 成熟度/备注 |
|-----------|----------|-----------|----------|-----------|-------------|
| clippy `too_many_arguments` | long-parameter-list | 默认 warn（complexity 组） | `too-many-arguments-threshold`（默认 7 → 可调 5） | ✅ JSON spans 含行号 | 官方成熟。**未改名**（无 `too_many_parameters`，已核实官方索引+master 源码） |
| clippy `too_many_lines` | long-method（近似） | pedantic 组，默认关 | `too-many-lines-threshold`（默认 100 → 可调 60） | ✅（span 覆盖整函数） | 官方成熟。span=整函数：旧函数被 diff 触及一行即整函数重算——与 Python 侧块级规则语义一致，可接受 |
| clippy `cognitive_complexity` | （圈复杂度类，非 8 规则之一） | restriction 组，默认关 | `cognitive-complexity-threshold`（默认 25） | ✅ | 官方源码注明"非精确度量"，仅参考 |
| rustc `dead_code` | dead-code | 默认 warn | 按 lint code 细粒度开闭 | ✅ JSON | 官方。只报**未 pub 且未用**项，pub = 天然豁免（外部可能引用）→ 与 Python 保守语义对齐 |
| rustc `unused_imports` | dead-import | 默认 warn | 同上 | ✅ JSON | 官方。`pub use` re-export 天然豁免 ≈ Python 侧 `__all__`/PEP484 豁免 |
| semgrep（Rust） | — | — | — | — | GA 支持 Rust 但无计数/度量型内置规则，不适用 |
| SonarQube Community Build（Rust） | 多规则 | — | — | 服务端重 | 2025+ 官方 Rust 支持（rust-analyzer+clippy，圈/认知复杂度，吃 LCOV/Cobertura）——整仓报告型，非增量门禁 |
| cargo-udeps | （Cargo.toml 未用依赖） | — | — | — | 成熟活跃；依赖层而非函数层，可作日后 dependency 增强的候选 |
| cargo-bloat | — | — | — | — | 二进制品大小分析，不适用 |
| codemetrics（crate） | CRAP(Rust) | — | — | — | 2026-04 新建、0 star、未发布 crates.io → 不可用 |
| debtmap（crate 0.23.0） | CRAP 近似 | — | — | ✅ 函数级 JSON | 活跃（syn 全量 AST），函数级"风险分 = 复杂度×LCOV 覆盖缺口"——**非标准 CRAP 公式**，可借鉴不可照抄 |

**Rust 关键结论**
- 现成可接（走既有 clippy/rustc JSON 行过滤通道）：long-parameter-list、dead-code、
  dead-import，long-method 可用 `too_many_lines` 调阈值 60 近似。
  注意：too_many_lines 为 pedantic 默认关、cognitive_complexity 为 restriction——启用需
  在调用侧加 `-W clippy::...` 与临时 clippy.toml 阈值（不改使用方代码）。
- 必须自研（syn + 行号）：switch-statements（clippy 无 match 分支数 lint）、
  large-class/struct 字段数、data-class/lazy-class（Rust struct 生态语义需重定义）。
- CRAP：**无成熟现成工具**；严谨实现 = 自研复杂度（syn/自算）+
  cargo-tarpaulin 行级覆盖，按函数 span 聚合为 CRAP。
- 唯一不确定项：SonarQube/debtmap 的函数级行号键与精确公式细节未逐字段核验（标注 uncertain）。

## TS 侧盘点（oxlint / sonarjs / eslint 等）

| 工具/规则 | 映射规则 | 默认开/关 | 阈值可配 | 行号 span | 成熟度/备注 |
|-----------|----------|-----------|----------|-----------|-------------|
| oxlint `max-lines-per-function` | long-method | 非默认开 | 默认 50 → 可调 60 | ✅ | oxlint 原生（eslint 兼容），规则索引 870/114 默认开；**.vue 实测可解析 script 块并报结构类规则** |
| oxlint `max-params` | long-parameter-list | 非默认开 | 可调对齐 5 | ✅ | 同上 |
| oxlint `no-unused-vars` | dead-import | 默认开之一 | — | ✅ | **.vue 同文件对未用 import 不触发**（实测）→ 引用类规则在 .vue 维持 skipped |
| eslint core `max-params` / `max-lines-per-function` / `complexity` | 同上/圈复杂度 | 均不在 eslint:recommended（仅 no-unused-vars 在） | 3 / 50 / 20 → 可配 | ✅ `--format json` 带 line/endLine | oxlint 缺失回退通道已存在 |
| eslint-plugin-sonarjs（oxlint JS 插件 alpha 或 eslint 加载） | switch-statements（`max-switch-cases` S1479） | 需显式加载 | 可配 | ✅ | oxlint conformance 359/360 通过；**不支持自定义 parser → .vue 不可用** |
| tsc `noUnusedLocals`/`noUnusedParameters` | dead-code / dead-import 补充 | 需 tsconfig 开启 | — | ⚠️ 仅文本 `file(line,col): TS6133`，无 JSON（TypeScript#57198 未闭） | 实测输出可解析；仅 .ts/.tsx |
| codopsy-ts | — | — | — | — | 0★、2026-02 停更、忽略 .vue → 不可用 |
| SonarJS analyzer | 多规则 | — | — | 服务端体系 | SonarQube/SonarLint 生态，非增量 CLI |
| crap4js / @gligor/crap4ts | CRAP(TS) | — | — | — | 2026 新实验包 0-1★，吃 lcov / istanbul coverage-final.json；vitest json-summary **无函数级覆盖** → 换格式后仍需自研聚合 |
| 自研（TS Compiler API / oxc / 自定义 eslint 规则） | large-class / data-class / lazy-class / 判别式 switch / CRAP | — | — | — | 官方无现成 |

**TS 关键结论**
- `.ts/.tsx` 可低成本接入：long-method、long-parameter-list、dead-import
  （oxlint 或 eslint fallback，阈值对齐 60/5）；dead-code 可加 tsc TS6133 文本解析补充。
- `.vue`：结构类规则（max-params / max-lines-per-function）实测可报 → **可查**；
  未用 import 等引用类规则不触发 + sonarjs 等 JS 插件不可用 → 维持"诚实 skipped"。
- switch-statements（P2）可选走 sonarjs `max-switch-cases`（oxlint JS 插件 alpha 或 eslint）。
- CRAP / large-class / data-class / lazy-class / 判别式 switch：无现成，需自研 → 维持 backlog。

## 结论与建议

### 跨语言综合

- **用户的直觉部分成立**：现成"规则型"工具确实存在，且覆盖了最有价值的一簇
  ——函数过长、参数过多、未用 import/代码。Rust 走 rustc/clippy，TS 走
  oxlint/eslint/tsc：均成熟、阈值可配、输出带行号 span。
- **但没有任何工具自带增量门禁协议**（diff 相交、按函数豁免、P0/P1 分级）。
  接入 = 复用 quality-gate 现有 lint 通道（本就是吃 JSON 行号做行过滤）+
  开规则 + 阈值映射 + 沿用 Python 侧 diff/块级语义。**不需要新写 AST 引擎**，
  成本远低于"从零自研"——这正是当初 Python 侧自研、Rust/TS 侧搁置的差异点：
  Python 没有可嵌的成熟规则工具，Rust/TS 有。
- **真正无现成工具的部分**：CRAP（Rust/TS 均无成熟品——本质要"函数级覆盖率
  聚合"，两生态只提供到文件级）；类生态规则（large-class / data-class /
  lazy-class：主流工具缺失，且 Rust/TS 类生态弱，语义需重定义）；Rust 侧
  match 分支统计（clippy 无此 lint）；TS 判别式 switch。→ 维持 backlog 合理。

### 推荐做（档一 · 低成本，点亮 ⏸️ 中最有价值部分）

把现成规则接入为 Rust/TS 增量 smell 检查（沿用 Python 严重度映射：
long-method P0 / long-parameter-list+dead-import P1 → 阻塞；dead-code P2 → 报告）：

| 语言 | 点亮规则 | 工具 | 要点 |
|------|----------|------|------|
| Rust | long-parameter-list | clippy `too_many_arguments` | 阈值 `too-many-arguments-threshold` 5 |
| Rust | dead-import | rustc `unused_imports` | 默认 warn；`pub use` re-export 天然豁免 |
| Rust | dead-code | rustc `dead_code` | 默认 warn；pub = 天然豁免 |
| Rust | long-method（可选） | clippy `too_many_lines` | pedantic 单开，阈值 60 |
| TS .ts/.tsx | long-parameter-list | oxlint `max-params`（回退 eslint） | 阈值 5 |
| TS .ts/.tsx | long-method | oxlint `max-lines-per-function` | 阈值 60 |
| TS .ts/.tsx | dead-import | oxlint `no-unused-vars`（默认开） | 回退 eslint |
| TS .ts/.tsx | dead-code（可选） | tsc TS6133 解析 | 文本输出需自写解析 |
| TS .vue | 结构类规则可查 | oxlint 实测对 script 块生效 | 引用类规则维持诚实 skipped |

工作量评估：**中等偏小**——不改架构（扩规则名+阈值+严重度映射 + span/diff
语义适配 + 测试），且自带 dogfood 验证。开工前需钉 3 个实现细节：
1. clippy 单开某 pedantic/restriction lint 的方式（`-W clippy::x` 单开 vs
   临时 clippy.toml），避免连带其它 pedantic；
2. rustc `dead_code`/`unused_imports` 是否已流过现有 clippy JSON 通道，还是
   需补 cargo check 通道；
3. .vue 点亮结构规则后，novel-indexing web 结果会从"部分 skipped"变"部分有报"
   ——预期提升，需回归确认无假红。

### 暂缓（档二 · 维持 backlog）

- **CRAP（Rust/TS）**：无工具捷径，严谨实现需自研函数级覆盖率聚合 → 缓。
- large-class / data-class / lazy-class 跨语言：类生态弱、需重定义语义 → 缓。
- TS switch-statements P2：sonarjs `max-switch-cases` 可借（oxlint JS 插件
  alpha 或 eslint），但 JS 插件不支持 .vue → 按需再启。
- Rust long-method 近似、Rust switch 统计：同上，按需再启。

### 参考链接（主证据）

- [rust-clippy lint 索引](https://rust-lang.github.io/rust-clippy/master/index.html)、
  [clippy lint 配置](https://doc.rust-lang.org/clippy/lint_configuration.html)、
  [rustc warn-by-default lints](https://doc.rust-lang.org/rustc/lints/listing/warn-by-default.html)、
  [rustc JSON 诊断](https://doc.rust-lang.org/rustc/json.html)
- [oxlint 规则索引](https://oxc.rs/docs/guide/usage/linter/rules.html)、
  [oxlint JS 插件](https://oxc.rs/docs/guide/usage/linter/js-plugins.html)、
  [oxc×sonarjs conformance](https://raw.githubusercontent.com/oxc-project/oxc/main/apps/oxlint/conformance/snapshots/sonarjs.md)
- [eslint max-lines-per-function](https://eslint.org/docs/latest/rules/max-lines-per-function)、
  [eslint max-params](https://eslint.org/docs/latest/rules/max-params)、
  [eslint no-unused-vars](https://eslint.org/docs/latest/rules/no-unused-vars)
- [TypeScript JSON 诊断 feature 请求](https://github.com/microsoft/TypeScript/issues/57198)
- [tarpaulin](https://github.com/xd009642/tarpaulin)、[debtmap（CRAP 近似参考）](https://crates.io/crates/debtmap)

> 不确定项（调研中标注 uncertain，未逐字段核验）：SonarQube/debtmap 的函数级
> 行号键与精确公式细节；sonarjs 对 .vue 的官方支持说明页拉取为空。
