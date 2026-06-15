# 基于 Agent 与多模态大模型的交互式数字电路版图布线系统技术方案

## 1. 项目概述

### 1.1 背景
当前数字后端物理设计流程中，布线（Routing）环节高度依赖工具自动化与工程师经验的结合。Innovus 等 EDA 工具提供了强大的 Tcl API 与内置算法，但在面对复杂设计时，其"黑盒式"自动化往往难以满足特定设计意图（如时钟鱼骨结构、通道整洁度、关键路径时序优化等）。资深工程师的价值体现在对局部版图的直觉判断、策略选择与人工微调上，但这种经验难以沉淀、复用和规模化。

### 1.2 目标
构建一套**以 Agent 为中枢、多模态大模型为感知器、Innovus 为执行引擎**的交互式布线系统，实现以下核心能力：
- **算子化布线**：将人工常规操作封装为可编排的原子/组合算子（如区域限定布线、鱼骨布线、A*寻优重布、Shielding 布线等）。
- **案例驱动决策**：从公司历史优质版图中自动提取经验，构建设计记忆库，Agent 通过相似性检索而非硬编码规则进行策略推荐。
- **多模态感知**：融合版图视觉截图、矢量拓扑（GDS/DEF 序列化/图表示）与文本报告，实现对版图状态的深度理解。
- **人机协同干预**：在关键节点设置检查点（Checkpoint），支持工程师通过自然语言、圈注截图等方式介入，Agent 根据反馈动态调整策略。
- **闭环验证**：所有策略执行后通过 Innovus 原生验证（DRC/Timing/Connectivity）反馈结果，持续更新记忆库。

### 1.3 适用范围
- **目标工具**：Cadence Innovus（主要执行引擎）
- **设计对象**：标准单元、子模块（Sub-block）、Macro 周边的信号布线
- **工艺节点**：当前及未来支持的先进工艺节点（7nm 及以下）
- **集成方式**：外挂式 Agent 编排器，通过 Tcl/批处理/交互式 Shell 驱动 Innovus，不修改 Innovus 内核

---

## 2. 系统总体架构

系统采用**四层分离模型**，各层通过标准化接口通信：

```
+-------------------------------------------------------------+
|  Layer 4: 人机交互与干预层 (Human-in-the-Loop UI)            |
|  - 版图可视化、检查点审批、自然语言/圈注交互、策略确认        |
+-------------------------------------------------------------+
|  Layer 3: Agent 编排与决策层 (Agent Orchestrator)            |
|  - 任务规划、状态机管理、算子调度、多模态融合推理             |
|  - 决定何时暂停等待人工输入、何时自主执行                     |
+-------------------------------------------------------------+
|  Layer 2: 多模态感知与算子层 (Multimodal Perception & Tools) |
|  - 版图视觉编码 (VLM)、矢量拓扑编码 (GNN/序列化)、设计记忆库 |
|  - 原子算子 / 组合算子 / 策略算子的封装与版本适配             |
+-------------------------------------------------------------+
|  Layer 1: Innovus 执行引擎 (Innovus Executor)                |
|  - Batch / Interactive 模式运行、Tcl 脚本执行                 |
|  - 日志捕获、报告生成 (DRC/Timing/Congestion)、数据库管理     |
+-------------------------------------------------------------+
```

**设计原则**：
- **策略与执行分离**：Agent 只生成"策略级"意图（层选择、模式、约束），精确坐标与 DRC 验证由 Innovus 处理。
- **状态可回滚**：每个组合算子执行前自动保存 Checkpoint（.enc），失败时自动恢复。
- **模态互补**：截图提供视觉直觉，矢量图提供精确拓扑，文本报告提供量化指标，三者融合决策。

---

## 3. 核心模块设计

### 3.1 算子层（Tool/Primitive）设计

算子是 Agent 操作 Innovus 的最小单元，分三层封装以隔离版本差异与参数复杂性：

#### 3.1.1 原子算子（Atomic Primitives）
直接映射 Innovus Tcl API，做参数校验与异常处理：

| 算子 ID | 功能 | Innovus 对应命令 |
|---------|------|-----------------|
| route_global | 全局布线 | routeDesign -global |
| route_detail | 详细布线 | routeDesign -detail |
| route_eco | ECO 增量布线 | ecoRoute |
| route_special | 特殊网络布线 | specialRoute |
| set_route_mode | 设置布线模式 | setNanoRouteMode 族 |
| set_route_constraint | 设置层/方向/间距约束 | setNanoRouteMode -routeTopRoutingLayer 等 |
| create_route_guide | 创建布线引导区 | createRouteGuide |
| add_route_blockage | 添加布线障碍 | addRouteBlk |
| delete_route | 删除指定网络布线 | deleteRoute |
| trim_wire | 修线/剪线 | trimWire |
| spread_wire | Spread 均匀分布 | setNanoRouteMode -droutePostRouteSpreadWire + spreadWire |
| verify_drc | DRC 检查 | verify_drc |
| verify_connectivity | 连通性检查 | verifyConnectivity |
| save_snapshot | 保存状态 | saveDesign |
| load_snapshot | 恢复状态 | restoreDesign |

**封装要求**：每个原子算子需实现 execute()、rollback()、validate() 三个接口，确保可安全编排。

#### 3.1.2 组合算子（Compositional Operators）
将原子算子按特定语义组合，映射人工常规操作：

| 算子 ID | 语义 | 内部逻辑 |
|---------|------|---------|
| route_fishbone | 鱼骨图式布线 | createRouteGuide 划定通道 -> set_route_constraint 固定层/方向 -> route_special -> spread_wire |
| route_star | 星型/放射式布线 | 以驱动为中心创建扇出引导区 -> 限制最大长度 -> route_detail |
| route_a_star | A* 寻优重布 | delete_route -> set_route_constraint 低阻层/短路径 -> route_eco -> verify_drc |
| route_in_box | 区域限定布线 | save_snapshot -> add_route_blockage 隔离 -> createRouteGuide 限定 box -> route_detail -> 验证/回滚 |
| route_channel | 通道布线 | 限制 routeBottomRoutingLayer/routeTopRoutingLayer -> route_detail -> spread_wire |
| route_shielded | 屏蔽线布线 | 创建 NDR 规则 -> set_route_constraint -> 两侧 route_special 接地 shielding -> DRC 验证 |
| route_repair | DRC 修复 | 解析 DRC 报告 -> 定位违例 -> route_eco / trim_wire / delete_route 局部修复 -> 迭代验证 |

**关键机制**：组合算子执行前自动 save_snapshot，执行后若结果劣化（DRC 激增、时序恶化）则自动 restoreDesign，确保 Agent 可以安全探索。

#### 3.1.3 策略算子（Strategic Operators）
面向设计意图的高层抽象，Agent Planner 主要在这一层决策：

| 算子 ID | 场景 | 说明 |
|---------|------|------|
| optimize_congestion | 拥塞优化 | 分析 congestion map -> 决策扩宽通道/换层/调整 placement |
| optimize_timing | 时序优化 | 针对 setup/hold 违例 -> 决策换层降阻/加 buffer/调整 wire sizing |
| optimize_si | 串扰优化 | 检测耦合电容 -> 决策加 shielding/增大 spacing/reorder net |
| cleanup_routing | 整洁度优化 | 后处理：spread、trim redundant via、jog 消除、层分布均衡 |

### 3.2 设计记忆库（Design Memory Vector DB）

这是系统的"经验中枢"，替代不可枚举的规则库。

#### 3.2.1 数据条目结构
```json
{
  "entry_id": "proj_a_dsp_2024q3_m3_congestion",
  "timestamp": "2024-09-15",
  "technology": "7nm",
  "design_type": "AI_Accelerator",
  "state": {
    "vision_embedding": [0.12, -0.05, ...],
    "graph_embedding": [0.33, 0.88, ...],
    "text_features": {
      "congestion_m3": 0.78,
      "cell_density": 0.65,
      "net_type": "clock",
      "fanout": 128,
      "adjacent_macros": ["RAM_A", "DSP_B"]
    }
  },
  "action": {
    "operator": "route_fishbone",
    "parameters": {
      "layers": ["M3", "M4"],
      "ndr_rule": "CLK_WIDE",
      "spread": true
    },
    "tcl_script": "..."
  },
  "outcome": {
    "drc_before": 45,
    "drc_after": 3,
    "timing_slack_before": -0.08,
    "timing_slack_after": -0.02,
    "execution_time": 180,
    "success_score": 0.92
  },
  "multimodal_assets": {
    "screenshot_path": "/db/proj_a/clip_m3.png",
    "def_clip_path": "/db/proj_a/region.def",
    "subgraph_gml": "/db/proj_a/net_graph.gml"
  }
}
```

#### 3.2.2 经验提取 Pipeline
```
历史项目 ENC/DEF/GDS
    |
    +-- 滑动窗口截取局部版图（50um x 50um）
    |       +-- KLayout / Innovus 渲染层着色截图（PNG）
    |       +-- VLM 编码 -> vision_embedding
    |
    +-- OpenDB / KLayout 解析局部拓扑
    |       +-- 提取子图（pins, vias, wire segments）
    |       +-- GNN / Graph Transformer 编码 -> graph_embedding
    |
    +-- 关联 QoR 报告
    |       +-- DRC 报告、Timing 报告、Congestion Map
    |       +-- 文本特征工程 -> text_embedding
    |
    +-- 多模态融合 -> 存入向量库（Milvus / Weaviate）
```

#### 3.2.3 检索策略
Agent 遇到新场景时，执行跨模态检索：
1. **视觉检索**：当前版图截图 -> 找视觉上最相似的历史片段
2. **图检索**：当前子图结构 -> 找拓扑相似的历史案例
3. **文本检索**：当前指标（congestion, DRC, slack）-> 找数值相近的案例
4. **融合排序**：多路召回后按加权相似度排序，取 Top-3 作为策略参考

### 3.3 Agent 决策引擎

#### 3.3.1 状态机（State Machine）
布线流程被抽象为状态机，关键节点设置 Checkpoint：

```
[Start] -> [Pre-route Check] -> [Global Route] -> [Checkpoint A: 人工审批?]
    |
    v
[Detail Route] -> [Checkpoint B: DRC/Timing 检查] -> [人工干预 / 自动修复]
    |
    v
[Post-route Opt] -> [Checkpoint C: 整洁度检查] -> [人工微调 / Spread]
    |
    v
[Final Verify] -> [End]
```

在每个 Checkpoint，Agent 评估是否需要人工介入：
- **自动通过**：指标优于阈值（DRC < 5, Slack > -0.05）
- **建议干预**：指标异常，Agent 生成诊断报告并暂停等待人工确认
- **强制干预**：检测到严重违例（Short, Antenna 超标），自动回滚并通知人工

#### 3.3.2 决策流程（Case-based Reasoning）
```
1. Observation: 从 Innovus 导出当前 DEF + 报告
    |
2. Encoding:
    +-- 渲染局部截图 -> VLM 提取 vision_embedding
    +-- 解析局部子图 -> GNN 提取 graph_embedding
    +-- 提取指标特征 -> text_embedding
    |
3. Retrieval: 在设计记忆库中执行跨模态相似检索，召回 Top-k 案例
    |
4. Reasoning: LLM/Agent 分析检索结果，进行类比推理
    |   "当前状态与 Case A（相似度 0.92）高度相似，
    |    均为 M3 层高密度时钟网、拥塞 0.8、非关键时序。
    |    Case A 采用 route_fishbone + M4 换层 + spread，
    |    DRC 从 30 降至 2。建议优先尝试此策略。"
    |
5. Planning: 生成算子序列（参数已根据当前上下文调整）
    |
6. Execution: 调用 Layer 2 组合算子 -> 生成 Tcl -> 提交 Innovus
    |
7. Verification: 读取 DRC/Timing 报告
    |
8. Feedback: 将结果（成功/失败）写回记忆库，更新 success_score
```

#### 3.3.3 在线学习机制
- **成功案例**：自动入库，作为未来检索的正样本
- **失败案例**：标记为负样本，Agent 在类似场景下降低该策略优先级
- **人工修正**：工程师在 Checkpoint 修改策略后，将"人工干预后的策略+结果"作为高置信样本入库

### 3.4 多模态感知层

这是系统的"眼睛"，负责将 Innovus 的物理世界转化为 Agent 可理解的语义。

#### 3.4.1 视觉感知（Vision）
- **渲染引擎**：KLayout Python API (klayout.db) 或 Innovus GUI 自动化截图
- **层着色编码**：M2=蓝, M3=绿, M4=黄, Via=红，确保 VLM 能区分层信息
- **滑动窗口**：按 50um x 50um 截取局部区域，避免单图过大
- **视觉编码器**：
  - 基础：CLIP / Jina-CLIP（通用视觉嵌入）
  - 进阶：Qwen-VL / LLaVA（支持视觉定位与圈注理解）
  - 领域适配：用历史项目截图+策略标签进行 LoRA 微调

#### 3.4.2 矢量拓扑感知（Vector/Graph）
避免截图的信息损失，直接处理 GDS/DEF 的矢量本质：

**A. 序列化表示（iPCL-R 范式）**
将布线路径编码为方向 token 序列：
```
矢量路径: (0,0,M1) -> (10,0,M1) -> (10,5,M2) -> (20,5,M2)
序列化: [DRIVER] R R R R U U R R R [LOAD]
```
- 适合 Transformer 处理，可用于生成式布线（续写序列）
- 局限：全局信息需分块处理

**B. 图表示（NetTAG / EDA-Schema-V2 范式）**
构建异构图：
- **节点**：pins, vias, steiner points, cells
- **边**：wire segments（属性：层、宽度、坐标、RC 参数）
- **节点文本属性**：门级逻辑表达式、物理特征（延迟、功耗）
- **编码**：Graph Transformer（TAGFormer）提取 graph_embedding
- **跨阶段对齐**：将布局嵌入与 RTL 嵌入对齐，支持跨阶段知识迁移

**C. 结构化文本（JSON）**
对局部小区域（单 net 或微区域），直接输出精确坐标与属性的结构化描述，供 LLM 理解。

#### 3.4.3 文本/指标感知（Text）
- DRC 报告、Timing 报告（WNS/TNS）、Congestion Map、Power 报告
- 通过文本嵌入模型（如 BGE-M3）编码，与视觉/图嵌入对齐到统一空间

#### 3.4.4 多模态融合策略
```
Agent 决策时同时消费：
  - 视觉模态："这片区域看起来像鱼骨/拥塞/杂乱"
  - 图模态："Net A 有 3 个负载，最长路径 120um，经过 2 个 Via"
  - 文本模态："M3 拥塞 0.8，Setup slack -0.08ns，DRC 23 个"

融合方式：
  - 早期融合：将三种嵌入拼接后输入 MLP，输出统一决策向量
  - 晚期融合：各模态分别检索，结果加权投票
  - 推荐：晚期融合 + LLM 综合推理（让 LLM 看检索结果做最终判断）
```

### 3.5 人机交互与干预层

#### 3.5.1 检查点（Checkpoint）机制
在状态机关键节点自动暂停，推送结构化摘要：
- **版图快照**：当前区域的 PNG 截图（带层着色）
- **指标卡片**：DRC 数、Timing Slack、Congestion 热力图缩略图
- **策略建议**：Agent 推荐的下一步算子及理由
- **操作选项**：
  - [批准继续]：Agent 按建议执行
  - [回退到 X]：恢复到某历史 Checkpoint
  - [自定义策略]：工程师输入自然语言或 Tcl 脚本
  - [圈注干预]：在截图上圈出问题区域，Agent 针对性修复

#### 3.5.2 交互形式
| 交互方式 | 技术实现 | 场景 |
|---------|---------|------|
| 自然语言 | LLM 理解意图 -> 翻译为算子序列 | "M3 太乱了，换到 M4 重新布" |
| 圈注截图 | VLM 视觉定位（Qwen-VL/GPT-4o） | 工程师圈出违例区域，Agent 局部 ECO |
| Tcl 直接输入 | 透传至 Innovus | 资深工程师直接写 Tcl 微调 |
| 参数滑杆 | Web UI 调整约束参数 | 实时调整层偏好、NDR 宽度等 |

#### 3.5.3 上下文保留
Agent 在 Checkpoint 处生成**状态摘要文档**，包含：
- 当前已执行算子序列
- 各阶段指标变化曲线
- 检索到的相似历史案例链接
- 人工干预的历史记录

确保工程师即使中断后返回，也能快速恢复上下文。

---

## 4. 数据流与接口规范

### 4.1 Innovus <-> Agent 接口
```python
class InnovusExecutor:
    def execute_tcl(self, tcl_script: str, mode: str = "batch") -> ExecutionResult:
        # 提交 Tcl 脚本，返回日志与报告路径

    def get_snapshot(self, region: Box) -> Snapshot:
        # 导出指定区域的 DEF + 截图 + 报告

    def restore_checkpoint(self, checkpoint_id: str):
        # 恢复到指定 Checkpoint

    def interrupt(self) -> bool:
        # 中断当前长命令（如 routeDesign），支持优雅停止
```

### 4.2 多模态数据 Pipeline
```
Innovus (ENC/DEF/GDS)
    |
    +-- KLayout API -> 渲染 PNG 截图 -> VLM Encoder -> vision_embedding
    |
    +-- OpenDB API -> 提取子图 -> Graph Transformer -> graph_embedding
    |
    +-- DEF Parser -> 提取几何/属性 -> Feature Engineering -> text_embedding
    |
    +-- 融合 -> Vector DB (Milvus/Weaviate)
```

### 4.3 记忆库接口
```python
class DesignMemory:
    def insert(self, entry: MultimodalEntry) -> str:
        # 插入新经验条目

    def retrieve(self, 
                 vision_query: Optional[Image],
                 graph_query: Optional[Graph],
                 text_query: Optional[Dict],
                 top_k: int = 3) -> List[Entry]:
        # 跨模态检索，返回最相似的历史案例

    def feedback(self, entry_id: str, outcome: Outcome):
        # 更新案例结果（成功/失败）
```

---

## 5. 实施路线图

### Phase 1：算子固化与基础闭环（第 1-2 个月）
**目标**：验证"Agent 驱动 Innovus"的技术可行性
- 将团队现有 Tcl 脚本拆解为 15-20 个原子算子，封装为 Python 类
- 实现 5-8 个高频组合算子（route_in_box, route_fishbone, route_repair 等），含自动回滚机制
- 构建最小 Agent 编排器（LangGraph 或自研状态机），实现：
  - 生成 Tcl -> 提交 Innovus -> 解析日志 -> 决策下一步
  - 在 Global Route / Detail Route / Post-route 后设置 Checkpoint
- **交付物**：可运行的 MVP，能自动完成一次简单设计的布线并暂停等待确认

### Phase 2：设计记忆库与向量检索（第 3-4 个月）
**目标**：验证"案例驱动决策"的有效性
- 收集 20-30 个历史项目的局部版图数据（好/坏案例）
- 构建设计记忆库：
  - 用 CLIP / 轻量 VLM 提取视觉嵌入
  - 用 OpenDB 提取子图，手工特征工程提取 text embedding
  - 存入 Milvus / Weaviate
- 实现跨模态检索：给定当前版图截图/指标，召回 Top-3 历史案例
- Agent 集成检索结果到决策流程，生成策略建议
- **关键指标**：Agent 推荐策略的人工采纳率 > 70%

### Phase 3：多模态感知与交互升级（第 5-7 个月）
**目标**：实现"看图说话"的人机协同
- 引入支持视觉定位的 VLM（Qwen-VL / GPT-4o）
- 实现圈注交互：工程师在截图上圈出区域 -> Agent 解析意图 -> 局部 ECO
- 引入图表示（NetTAG 范式）：用 GNN 编码局部子图，与视觉嵌入对齐
- 实现自然语言 -> 算子序列的翻译（如"把这片区域 spread 一下" -> spread_wire）
- **交付物**：带 Web UI 的交互式布线工作台

### Phase 4：在线学习与策略自优化（第 8-12 个月）
**目标**：系统随使用自我进化
- 引入反馈闭环：所有执行结果自动写回记忆库，更新 success_score
- 对高频场景（如时钟布线、总线布线）训练轻量级策略模型（Bandit / 贝叶斯优化）
- 探索 Inverse RL：从历史最优版图中学习隐式奖励函数
- 逐步减少 Checkpoint 频率，提高自动化率
- **交付物**：生产级系统，能处理 80% 常规布线场景，人工仅处理例外

---

## 6. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Innovus 版本兼容性 | Tcl 命令在不同版本间有差异 | 原子算子层做版本适配，Agent 不直接生成原始 Tcl |
| VLM 幻觉 | 生成不存在的层或错误策略 | 所有输出必须经过 Innovus DRC/Timing 验证；VLM 只生成策略级意图，不生成精确坐标 |
| 状态同步延迟 | routeDesign 可能运行数小时 | 采用命令分段（Global -> Detail -> Opt），每步后保存 Checkpoint |
| 数据隐私 | 版图截图包含设计机密 | 使用本地部署 VLM（Qwen-VL-72B 本地版）；敏感数据不出域 |
| License 占用 | Interactive 模式长期驻留 | Batch 为主，Interactive 为辅；非干预时段释放 License |
| 向量库冷启动 | 初期记忆库为空，Agent 无经验可参考 | Phase 1-2 人工录入种子案例；初期降低 Agent 自主率，提高人工确认频率 |
| 长上下文瓶颈 | 全局版图信息无法一次性输入 | 滑动窗口局部处理；全局策略由传统算法（Innovus）处理，Agent 只处理局部优化 |

---

## 7. 参考资料与开源项目汇总

以下为本方案设计过程中调研到的关键参考资料与开源项目，按主题分类：

### 7.1 EDA Agent 架构与自动化框架
- **EDA-Agent (AutoEDA)**：基于 MCP Server + Executor 范式的 EDA 自动化框架，提出 Generic Primitives + Domain Tools 的分层思想，支持 Agent 对 EDA 工具的原子级操作与状态管理。
  - 参考：相关论文及项目讨论（搜索关键词：EDA Agent MCP Server）

- **Innovus Tcl API 文档**：Cadence Innovus 官方 Tcl 命令参考，涵盖 routeDesign, setNanoRouteMode, ecoRoute, specialRoute, verify_drc, verifyConnectivity 等全部布线相关命令，以及 Batch/Interactive 模式切换与中断机制。
  - 参考：Cadence 官方文档及社区教程（搜索关键词：Innovus Tcl API routeDesign）

### 7.2 版图数据解析与处理
- **OpenDB (OpenROAD)**：OpenROAD 项目的开源数据库，提供 C++/Tcl/Python API 用于读取、修改和分析版图数据（DEF/LEF），支持子图提取、ClipGraphExtract 等功能，可作为 Agent 与 Innovus 之间的数据桥梁。
  - GitHub: https://github.com/The-OpenROAD-Project/OpenDB
  - 参考：OpenDB Python API 文档及版图图提取相关实现

- **KLayout**：开源版图查看与编辑工具，提供强大的 Python/Ruby 脚本接口（klayout.db），支持 GDS/DEF 解析、几何操作、层着色渲染、截图导出，是多模态感知层中版图渲染与矢量解析的核心工具。
  - 官网: https://www.klayout.de/
  - GitHub: https://github.com/KLayout/klayout
  - 参考：KLayout Python API 文档及 DEF/GDS 解析教程

- **def_parser**：Python 开源 DEF 文件解析库，可用于快速提取版图中的几何信息、单元位置、网络连接等结构化数据。
  - 参考：PyPI 及 GitHub 上的 def_parser 项目

### 7.3 版图序列化与图表示学习
- **iPCL-R (Interconnect Pattern Characterization Library - Routing)**：将芯片布线模式视为可学习序列，通过领域特定的 tokenizer（方向编码、树结构编码）将矢量几何转化为 LLM 可处理的 token，支持布线模式的生成与分类。
  - 参考：相关学术论文（搜索关键词：iPCL-R routing tokenization sequence）

- **EDA-Schema-V2**：构建包含 7,800 个设计实例的多模态数据集，将版图同时表示为空间图像（binary maps）、异构图和结构化文本/指标（Parquet），用于训练跨模态 ML 模型，是设计记忆库数据 schema 的重要参考。
  - 参考：相关论文及项目（搜索关键词：EDA-Schema-V2 multimodal graph image layout）

- **NetTAG (Netlist Text-Attributed Graph)**：将网表表示为文本属性图（TAG），融合门级语义与图结构，通过 LLM-based 文本编码器（ExprLLM）和 Graph Transformer（TAGFormer）实现跨阶段（RTL -> 布局）嵌入对齐，支持物理设计任务。
  - 参考：相关论文（搜索关键词：NetTAG text-attributed graph netlist）

- **CircuitFusion**：通过跨模态对比学习对齐 HDL 代码、电路图和功能摘要的嵌入，实现跨阶段知识迁移，为电路基础模型（CFM）提供技术路径参考。
  - 参考：相关论文（搜索关键词：CircuitFusion multimodal circuit foundation model）

### 7.4 多模态大模型与视觉理解
- **CLIP (OpenAI)**：通用视觉-语言对比学习模型，可用于版图截图的视觉嵌入提取，支持跨模态检索。
  - GitHub: https://github.com/openai/CLIP

- **Qwen-VL (阿里巴巴)**：开源多模态大语言模型，支持视觉定位、图像理解、圈注解析，适合作为交互式版图诊断的视觉推理引擎，支持本地部署。
  - GitHub: https://github.com/QwenLM/Qwen-VL

- **LLaVA (Large Language and Vision Assistant)**：开源视觉指令微调框架，可用于构建版图视觉问答系统。
  - GitHub: https://github.com/haotian-liu/LLaVA

- **Jina-CLIP / Jina Embeddings**：支持多模态（文本+图像）统一嵌入的开源模型，适合构建跨模态设计记忆库。
  - GitHub: https://github.com/jina-ai/jina-embeddings

### 7.5 向量数据库与 Agent 框架
- **Milvus**：开源分布式向量数据库，支持十亿级向量的高性能检索，适合存储设计记忆库的多模态嵌入。
  - GitHub: https://github.com/milvus-io/milvus

- **Weaviate**：开源向量搜索引擎，支持多模态数据存储与检索，提供 GraphQL 接口。
  - GitHub: https://github.com/weaviate/weaviate

- **LangGraph (LangChain)**：用于构建 Agent 状态机与工作流的框架，支持循环、条件分支、人机协同（Human-in-the-loop），适合实现本方案的 Agent 编排层。
  - 文档: https://langchain-ai.github.io/langgraph/

### 7.6 相关学术方向
- **Inverse Reinforcement Learning (IRL) for Physical Design**：通过从专家演示（优质版图）中反推隐式奖励函数，学习人类工程师的布线偏好与美学，避免手工设计奖励函数。
  - 参考：凸优化逆强化学习在设计空间探索中的应用相关论文

- **Imitation Learning / Behavior Cloning for Routing**：通过模仿历史优质布线数据，训练策略网络生成布线决策，结合 Innovus 执行引擎实现端到端优化。
  - 参考：EDA 领域模仿学习相关研究

- **Layout Pattern Mining**：从版图中自动挖掘高频几何模式（如鱼骨、星型、通道），用于指导 Agent 的策略选择。
  - 参考：EDA 版图模式挖掘相关论文

---

## 8. 结语

本方案的核心思想是**"让 Agent 像资深工程师一样看版图、做决策、用工具"**。通过算子化封装 Innovus 能力，通过设计记忆库沉淀组织经验，通过多模态感知理解物理世界，通过人机协同保留人类最终决策权。系统不追求完全替代工程师，而是将工程师从重复性的参数调优和脚本拼接中解放出来，专注于架构级决策与例外处理。

随着设计记忆库的不断积累，Agent 的自主率将逐步提升，最终形成**公司专属的智能布线知识资产**。
