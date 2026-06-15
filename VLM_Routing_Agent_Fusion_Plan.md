# 面向复杂数字版图的 VLM 驱动的智能布线 Agent 技术方案

## 摘要

**目标**：构建一套以多模态大模型（VLM）为决策中枢、以 EDA 工具为执行引擎的智能布线 Agent，使系统具备“看懂版图、诊断根因、精确修复、持续学习”的能力，最终显著提升复杂设计的 DRC 收敛速度并降低人工干预成本。

**核心思路**：
1. **细粒度动作空间**：从传统的“net 级撕线重布”下沉到 segment/via/net/region 四级编辑，使 VLM 能输出可精确执行的几何修复动作。
2. **结构化状态表示**：将 DRC 报告、net 特征、局部版图裁剪图组织成 VLM 可推理的结构化输入，替代单纯的全局热力图。
3. **归因反馈闭环**：每次动作后精确计算“修复了哪些违例、新增了哪些违例、影响了哪些 net”，让 VLM 能从结果中学习。
4. **EDA 抽象层**：通过 `EDAProvider` 接口解耦上层算法与具体 EDA 工具，默认基于 OpenROAD 验证，未来可平滑迁移到 Cadence Innovus / Synopsys ICC2。
5. **设计记忆库与人机协同**：积累成功案例，支持相似场景检索；关键 checkpoint 暂停，允许工程师通过自然语言、圈注或 Tcl 介入。

**预期收益**：
- ISPD 2018/2019 benchmark 上，DRC 收敛迭代次数降低 50% 以上。
- Action 成功率从当前约 70% 提升至 85% 以上。
- Wirelength / Via 回退控制在 3% / 5% 以内。
- 沉淀可复用的布线知识资产，降低对资深工程师个人经验的依赖。

**实施节奏**：总计约 3 个月完成核心算法能力验证，6 个月内引入记忆库与 HITL，12 个月内具备商业 EDA 生产化条件。

---

## 1. 背景：为什么需要智能布线 Agent

### 1.1 当前布线自动化的瓶颈

数字后端物理设计中，布线（Routing）是时序、面积、功耗、可制造性的关键交汇点。传统自动化流程存在三类瓶颈：

1. **工具黑盒化**：工业级路由器（如 OpenROAD TritonRoute、Cadence Innovus nanoRoute）能力强大，但其决策过程对工程师不可见，遇到复杂违例时难以针对性干预。
2. **经验难以沉淀**：资深工程师通过长期实践形成“看版图、判根因、选策略”的直觉，但这种经验通常以 Tcl 脚本或个人笔记形式存在，难以规模化复用。
3. **反馈稀疏**：现有优化循环往往只返回全局指标（DRC 总数、wirelength、via count），无法回答“这次改动修复了哪个违例、恶化了哪个 net”，导致策略选择缺乏可学习性。

### 1.2 大模型带来的机会

多模态大模型（VLM）具备同时理解图像、结构化文本和指令的能力，为布线优化提供了新的可能性：

- **视觉理解**：可以直接阅读版图截图，识别拥塞热点、DRC 聚集区域、走线模式。
- **结构化推理**：在输入 DRC 表、net 特征后，能够像工程师一样诊断根因并选择修复策略。
- **自然语言交互**：工程师可以用“把这片 M3 的 net_123 改到 M4”这类语言直接指挥 Agent。

但大模型本身不具备精确几何操作能力，必须与 EDA 工具、结构化编辑器、归因引擎深度结合，才能真正产生工程价值。

---

## 2. 总体设计

### 2.1 设计原则

1. **精确修复优先于经验积累**：只有 Agent 能稳定输出可执行的修复动作并验证其效果，后续的记忆库才有意义。
2. **开源验证，商业兼容**：默认在 OpenROAD + ISPD benchmark 上快速迭代，但通过抽象层保留接入 Cadence/Synopsys 的能力。
3. **结构化先于感知**：先把 DRC、net、region 信息结构化，再叠加视觉/图嵌入与检索。
4. **每次动作必须可归因**：闭环学习的基础是知道“什么动作修复/引入了哪些违例”。
5. **人类保留最终决策权**：高风险节点自动暂停，支持多种干预方式。

### 2.2 系统架构

系统采用四层模型：

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 4: 人机交互与干预层                                       │
│ - 版图可视化、Checkpoint 审批、自然语言/圈注/Tcl 交互          │
├────────────────────────────────────────────────────────────────┤
│ Layer 3: Agent 编排与决策层                                     │
│ - 任务规划、状态机、算子调度、诊断→策略→动作推理               │
│ - 决定何时自主执行、何时暂停等待人工                           │
├────────────────────────────────────────────────────────────────┤
│ Layer 2: 多模态感知与算子层                                     │
│ - 结构化状态提取（DRC / net / region）                         │
│ - 视觉编码（全局 panel + 局部 violation crop）                 │
│ - 原子/组合/策略/细粒度编辑算子                                │
├────────────────────────────────────────────────────────────────┤
│ Layer 1: EDA 执行引擎（EDAProvider）                            │
│ - OpenROADProvider（默认）                                     │
│ - CadenceInnovusProvider / SynopsysICC2Provider（未来）        │
│ - 全局/详细布线、DRC/metrics 提取、数据库编辑                  │
└────────────────────────────────────────────────────────────────┘
```

### 2.3 核心工作流

Agent 的工作流是一个增强的闭环：

```
Baseline Routing
       │
       ▼
Rich State Extraction
       │
       ├── Global 6-panel PNG（congestion、layer、DRC、overlay、hotspot）
       ├── Per-violation DRC 表（类型、坐标、涉及 net、严重度）
       ├── Per-net 路由特征（fanout、HPWL、segment、via、DRC 数）
       └── Local violation crops（Top N 聚集区域放大图）
       │
       ▼
VLM Diagnose ──► 识别每个 violation cluster 的根因
       │
       ▼
VLM Strategize ──► 选择修复策略（segment / via / net / region）
       │
       ▼
VLM Policy ──► 输出 JSON 动作列表
       │
       ▼
Policy Executor ──► 分发到 DEFEditor / LocalRouter / ODBEditor / EDAProvider
       │
       ▼
Attribution Engine ──► fixed / new / moved / persisted violations
                       per-net DRC delta、per-action 结果
       │
       ▼
Checkpoint / Early Stop / HITL Pause
       │
       └──────────────────────► 下一轮迭代
```

---

## 3. 核心能力

### 3.1 EDA 后端抽象层

为了让上层算法不绑定特定工具，引入 `EDAProvider` 抽象接口：

```python
class EDAProvider(ABC):
    @abstractmethod
    def run_baseline_flow(self, def_file: str, guide_file: str | None) -> str: ...
    @abstractmethod
    def run_incremental_route(self, def_file: str, net_list: list[str] | None) -> str: ...
    @abstractmethod
    def extract_drc_report(self, def_file: str) -> list[DRCViolation]: ...
    @abstractmethod
    def extract_metrics(self, def_file: str) -> RoutingMetrics: ...
    @abstractmethod
    def extract_congestion_map(self, def_file: str, resolution: int) -> np.ndarray: ...
```

- **OpenROADProvider**：封装现有 Tcl 子进程调用、报告解析和全局/详细布线流程。
- **未来扩展**：新增 `CadenceInnovusProvider`、`SynopsysICC2Provider` 时，上层代码无需修改。
- **关键收益**：降低对单一工具的依赖，便于在不同项目/客户环境中部署。

### 3.2 细粒度动作空间

传统 Agent 只能输出“撕掉 net_1、net_5、net_9 重布”这类粗粒度指令。本方案将动作空间扩展到四级：

| 层级 | 作用 | 示例 |
|------|------|------|
| Control | 循环控制 | `terminate`, `noop`, `pause_for_human` |
| Segment/Via | 精确几何编辑 | `rip_up_segment`, `reassign_layer`, `insert_jog`, `move_via`, `change_via_type` |
| Net | 中粒度 net 修复 | `rip_up_net`, `reroute_net_with_constraints`, `fishbone_route_net` |
| Region | 局部资源调控 | `set_routing_blockage`, `set_soft_guidance`, `relax_region` |

这样的好处是：
- VLM 可以针对具体违例输出“把 net_123 的第 2 段 M3 改到 M4”，而不是盲目撕线。
- 大多数小编辑只需局部评估，不需要每次跑完整 detailed_route。
- 失败时可快速回滚，不影响全局。

### 3.3 结构化状态表示

#### 3.3.1 DRC Violation 表

将传统 DRC 报告解析为结构化对象：

```python
@dataclass
class DRCViolation:
    violation_id: str
    vtype: str          # spacing / short / min_width / end_of_line / via_spacing
    layer: str
    bbox: Tuple[int, int, int, int]
    center: Tuple[int, int]
    nets_involved: List[str]
    segment_indices: List[Tuple[str, int]]
    severity: float
    description: str
```

严重度综合考虑违例类型和所在位置拥塞程度：

```python
def compute_severity(v: DRCViolation, congestion_map: np.ndarray) -> float:
    base_weights = {
        "short": 10.0, "spacing": 2.0, "min_width": 3.0,
        "end_of_line": 2.5, "via_spacing": 1.5, "other": 1.0
    }
    base = base_weights.get(v.vtype.lower(), 1.0)
    cong_val = lookup_congestion(congestion_map, v.center)
    return base * (1.0 + cong_val)
```

#### 3.3.2 Per-net 路由特征

```python
@dataclass
class NetRoutingFeatures:
    net_name: str
    fanout: int
    hpwl_um: float
    routed_segments: int
    via_count: int
    layer_usage: Dict[str, int]
    drc_count: int
    drc_violation_ids: List[str]
    congestion_score: float
    is_critical: bool   # fanout > 50 或 HPWL > 500um
```

这些结构化信息让 VLM 能够进行根因推理，而不是仅凭全局热力图猜测。

#### 3.3.3 视觉输入

- **Global 6-panel PNG**：congestion heatmap、M3/M4 routing layer、DRC marker、overlay、hotspot。
- **Violation crops**：对 violation 做空间聚类，按严重度排序，对 Top N 生成 512×512 局部放大图，包含涉及 net 标注。

### 3.4 VLM 诊断-策略-动作闭环

VLM 的角色不是简单的动作选择器，而是：

1. **诊断器**：分析每个 violation cluster 的根因。
2. **策略选择器**：决定用 segment/via/net/region 哪一层级修复。
3. **动作生成器**：输出带参数、理由和预期影响的 JSON 动作。

System Prompt 核心要求：
- 优先使用 segment/via 级编辑处理孤立违例。
- 仅在 net 存在 >5 个违例或跨 >3 个 segment 时才用 net-level rip-up。
- 仅在多 net 拥塞热点才使用 region-level blockage。
- 每个动作必须附带 `reason` 和 `expected_impact`。

### 3.5 归因反馈引擎

每次动作执行后，归因引擎计算：

- 全局 DRC / Wirelength / Via delta。
- Fixed / New / Moved / Persisted violation IDs。
- Per-net DRC delta。
- Per-action 执行结果。

这些结果作为下一轮 VLM prompt 的 `Previous Action Result` 部分，让 VLM 知道哪些动作有效、哪些无效、哪些有副作用。

### 3.6 设计记忆库

当系统积累了一定数量的成功案例后，引入向量数据库存储：

```json
{
  "entry_id": "proj_a_dsp_2024q3_m3_congestion",
  "state": {
    "vision_embedding": [...],
    "graph_embedding": [...],
    "text_features": {"congestion_m3": 0.78, "fanout": 128}
  },
  "action": {
    "operator": "reassign_layer",
    "parameters": {"net_name": "net_123", "segment_index": 2, "from_layer": "M3", "to_layer": "M4"}
  },
  "outcome": {
    "drc_before": 45,
    "drc_after": 3,
    "success_score": 0.92
  }
}
```

检索策略：视觉 + 图 + 文本三路召回，加权排序，Top-3 作为 VLM 诊断参考。

### 3.7 人机协同

在状态机关键节点设置 Checkpoint，根据指标自动判断：

- **自动通过**：DRC < threshold，Slack > threshold。
- **建议干预**：指标异常，生成诊断报告并暂停等待确认。
- **强制干预**：严重违例自动回滚并通知人工。

支持三种干预方式：

| 方式 | 场景 |
|------|------|
| 自然语言 | “M3 太乱了，换到 M4 重新布” |
| 圈注截图 | 工程师圈出违例区域，Agent 局部 ECO |
| Tcl 直接输入 | 资深工程师直接写 Tcl 微调 |

---

## 4. 关键技术与实现细节

### 4.1 自研鱼骨布线算子 `fishbone_route_net`

针对高 fanout、长 HPWL 的 net，生成“trunk + branch + via”的鱼骨拓扑作为详细路由器的高质量初始解：

1. 读取 DEF/LEF，提取 net pin 坐标与 access 点。
2. 启发式选择 trunk 方向、层、位置：
   - 层：从 preferred_layers 最低层开始，优先选 LEF 中 preferred_direction 层。
   - 方向：默认与 pin 分布长边正交；bbox 接近方形时取层 preferred_direction。
   - 位置：在 bbox 内按 track 扫描，选 obstacle density 最低的 track。
3. 投影 pin 到 trunk，合并过近点。
4. 生成分支，遇到 obstacle 时插入局部 jog 避让。
5. 在 branch-trunk 交汇处插入 via。
6. 写回 DEF，调用 detailed_route 做 DRC 精修。
7. 若 DRC 恶化，保留清洁鱼骨路径，仅对冲突区域局部修复；局部修复失败再整 net fallback。

### 4.2 DEF 编辑器与 ODB 编辑器双轨

- **DEFEditor**：纯 Python 实现，零外部依赖，任何 DEF 都能用。支持 `rip_up_segment`、`reassign_layer`、`insert_jog`、`move_via`、`change_via_type`、`relax_region`。
- **ODBEditor**：使用 OpenROAD `odb` Python API，更稳健高效，尤其适用于复杂编辑（插入带 via 的 jog）。
- PolicyExecutor 优先尝试 ODBEditor，不可用时回退到 DEFEditor。

### 4.3 局部评估与全局 fallback

- **局部评估**：对于只影响 1-3 个 net 的小编缉，使用 odb 或 DEF 层面的局部 DRC 检查快速验证。
- **全局 fallback**：对于 net-level 或 region-level 的较大改动，始终跑完整 detailed_route 作为安全 fallback。

### 4.4 动作优先级与回滚

每个组合算子或策略算子执行前自动保存 Checkpoint，执行后若结果劣化（DRC 激增、时序恶化）则自动恢复。每个 action 在 PolicyExecutor 中包在 try/except 中，单个动作失败不影响整个 policy。

---

## 5. 实施路线图

### Phase 1：结构化状态提取（1-2 周）

- 扩展 `src/routing_toolkit.py`，新增 `DRCViolation`、`NetRoutingFeatures` 及提取方法。
- 扩展 `src/visual_renderer.py`，新增 violation crops。
- 重写 `src/vlm_policy.py` prompt，加入 DRC 表和 net 特征。
- 增加单元测试。

### Phase 2：EDA 抽象层与 DEF 编辑器（2-3 周）

- 新建 `src/eda_provider.py` + `src/openroad_provider.py`。
- 新建 `src/def_editor.py`，实现 `rip_up_segment`、`reassign_layer`。
- 将现有 `RoutingToolkit` 逐步迁移为 `OpenROADProvider` 的薄封装。

### Phase 3：PolicyExecutor、LocalRouter 与归因（3-4 周）

- 新建 `src/policy_executor.py`、`src/tcl_generator.py`、`src/attribution_engine.py`、`src/local_router.py`。
- 实现 `fishbone_route_net`（`src/fishbone_router.py`）。
- 修改 `src/agent_controller.py` 集成新模块。

### Phase 4：细粒度动作扩展与局部评估（4-5 周）

- 实现 `insert_jog`、`move_via`、`change_via_type`。
- 新建 `src/odb_editor.py`（可选）。
- 引入 region-level 动作：`set_soft_guidance`、`relax_region`。

### Phase 5：组合算子与策略算子（5-6 周）

- 实现 `route_in_box`、`route_channel`、`route_shielded`、`route_repair`。
- 实现 `optimize_congestion`、`optimize_timing`、`optimize_si`、`cleanup_routing`。
- 组合算子内部自动 checkpoint 与回滚。

### Phase 6：设计记忆库原型（6-8 周）

- 引入向量数据库。
- 对 violation crop 做视觉嵌入，对 net 子图做图嵌入。
- 将 (state, action, outcome) 三元组入库。
- VLM prompt 中加入 Top-3 相似案例。

### Phase 7：人机协同与生产化（8-12 周）

- 实现 checkpoint 暂停与 HITL UI。
- 支持自然语言、圈注、Tcl 三种干预方式。
- 实现 `CadenceInnovusProvider` 骨架。
- 在线学习与 success_score 更新。

---

## 6. 验证计划与预期收益

### 6.1 测试基准

| Benchmark | 复杂度 | 用途 |
|-----------|--------|------|
| ispd18_test1 | 低 | 快速验证端到端流程 |
| ispd18_test2-3 | 中 | 验证细粒度 action 效果 |
| ispd18_test4-5 | 高 | 验证规模化与稳定性 |

### 6.2 成功标准

| 指标 | 基线 | 短期目标（3 个月） | 中期目标（6 个月） |
|------|------|-------------------|-------------------|
| 每次迭代 DRC 减少 | ~1-5 | ~5-20 | ~10-30 |
| test1-3 达到 DRC=0 迭代数 | 10-20 | 5-10 | 3-7 |
| test1-3 总运行时间 | 30-60 min | 20-40 min | 15-30 min |
| Wirelength 回退 | < 5% | < 3% | < 2% |
| Via 回退 | < 10% | < 5% | < 3% |
| Action 成功率 | ~70% | ~85% | ~90% |
| 人工采纳率 | — | — | > 70% |

### 6.3 Ablation Study

1. 无细粒度 action（基线）。
2. 仅细粒度 action，无归因反馈。
3. 细粒度 action + 归因反馈。
4. 细粒度 action + 归因反馈 + odb 局部评估。
5. 细粒度 action + 归因反馈 + 记忆库检索。
6. 细粒度 action + 归因反馈 + 记忆库检索 + HITL。

### 6.4 回归测试

- 新 agent 仍能使用旧 coarse action space 运行。
- 对 malformed VLM JSON 能 fallback 到 `noop` 或 `terminate`。
- OpenROAD 超时时能优雅降级。

---

## 7. 风险与应对措施

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| EDA 版本兼容性 | Tcl/命令差异 | EDAProvider 抽象 + 原子算子版本适配 |
| VLM 幻觉 | 生成错误策略 | VLM 只输出策略级意图，精确坐标由编辑器处理；所有动作经 DRC 验证 |
| DEF 文本编辑出错 | 格式复杂 | 加单元测试；优先用 ODBEditor 处理复杂编辑 |
| 状态同步延迟 | detailed_route 耗时长 | 命令分段 + checkpoint；小编辑用局部评估 |
| 记忆库冷启动 | 初期无案例 | Phase 1-2 先靠结构化诊断；Phase 6 再引入检索 |
| 数据隐私 | 版图敏感 | 本地部署 VLM；敏感数据不出域 |
| License 占用 | 商业 EDA 常驻 | Batch 为主，Interactive 为辅；非干预时段释放 |
| 商业 EDA 迁移成本 | OpenROAD → Innovus | EDAProvider 抽象；DEF/LEF 保持通用交换格式 |

---

## 8. 资源需求

### 8.1 人力资源

| 阶段 | 主要角色 | 人月 |
|------|---------|------|
| Phase 1-2 | 算法工程师 × 1 + 后端工程师 × 1 | 1 个月 |
| Phase 3-4 | 算法工程师 × 2 + 后端工程师 × 1 | 1.5 个月 |
| Phase 5 | 算法工程师 × 2 + EDA 专家 × 1 | 1 个月 |
| Phase 6-7 | 算法工程师 × 1 + 平台工程师 × 1 + 前端工程师 × 1 | 2-3 个月 |

### 8.2 计算资源

- OpenROAD 运行服务器：建议 16 核以上，64GB 内存。
- VLM 推理：初期使用云端 API（Gemini 2.5 Flash / Claude 3.5 Sonnet / GPT-4o）；敏感场景切换本地部署（Qwen-VL 等）。
- 向量数据库：Milvus / Weaviate，部署在内部环境。

### 8.3 数据资源

- ISPD 2018/2019 benchmark 数据。
- 公司内部历史项目 DEF/LEF/GDS（用于记忆库构建，需脱敏处理）。

---

## 9. 结论

本方案的核心价值在于：**让 VLM 从“看热闹”变成“能动手、会反思、可学习”的智能布线助手。**

通过四级细粒度动作空间、结构化状态表示、可归因反馈闭环和 EDAProvider 抽象层，系统能够在短期内验证算法效果，在中期积累设计知识，在长期迁移到商业 EDA 生产环境。人类工程师在关键节点保留决策权，既发挥了 VLM 的规模化优势，又保留了人类的专业判断。

建议立即启动 Phase 1，优先实现 `DRCViolation`、`NetRoutingFeatures`、`DEFEditor.rip_up_segment` / `reassign_layer` 和 `EDAProvider` 骨架，在 ispd18_test1-3 上验证“VLM 能否从结构化 DRC 表诊断并修复具体违例”这一核心假设。
