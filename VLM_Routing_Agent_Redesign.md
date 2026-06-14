# VLM Routing Agent 算法方案重设计

> 基于对当前 `VLM Routing Agent` 在 ISPD 2018/2019 benchmark 上运行结果的分析，本方案对原有 **Extract → Policy → Execute → Evaluate** 循环进行重构，核心目标是把 VLM 从"高层动作选择器"转变为"诊断器 + 策略选择器"，并引入 segment/via/region 级别的细粒度编辑算子，使 agent 真正具备修复和优化布线的能力。

---

## 1. 当前方案的问题重述

### 1.1 Action space 过于粗粒度

当前 VLM 能输出的动作只有 5 类：

| 动作 | VLM 实际决定的内容 | 实际执行者 | 问题 |
|---|---|---|---|
| `rip_up_reroute` | 选哪些 net | OpenROAD 重新 detailed route | VLM 只决定"撕谁"，不决定"怎么布"；整 net 撕掉重布破坏性大，且重布结果完全由 OpenROAD 决定。 |
| `incremental_route` | 无参数，直接调用 | OpenROAD | 本质上是"再试一次"。 |
| `layer_assign` | 一组 net + 偏好层 | OpenROAD | `layer_preference` 只是偏好，OpenROAD 不一定遵守。 |
| `set_blockage` | 一个 bbox 区域 | 在 DEF 里插入 BLOCKAGES | 对 OpenROAD 通常冗余，它会自动避开 DRC 区域。 |
| `terminate` | 是否终止 | — | 合理，但其余动作太粗。 |

结果是：**VLM 看了一张全局热力图，然后说"你把 net1、net5、net9 撕了重布"**。它并没有真正理解布线冲突的几何结构，也没有能力修改具体走线。

### 1.2 VLM 被低估

当前 VLM 的输入只有：
- 一张 6-panel PNG（全局 congestion、M3/M4 layer、DRC marker、overlay、hotspot）
- 全局 metrics（DRC、wirelength、via count）
- 少量 netlist 统计

它看不到：
- 每个 violation 具体坐标、bbox、涉及 net
- 每个 net 的 segment 分布、via 位置、层分布
- 上一次动作具体影响了哪些 net、哪些 region

因此 VLM 的策略往往是猜测，例如"撕掉 DRC 最多的 5 个 net"，而不是"把 net_123 在 (105000,205000) 的 M3 segment 改到 M4 以避开 spacing 违规"。

### 1.3 反馈信号稀疏且不可归因

每次迭代 VLM 只收到三个全局数字的 delta：
- `DRC total`
- `wirelength`
- `via_count`

如果一次 rip-up 20 个 net 后 DRC 增加了，VLM 无法知道：
- 是哪几个 net 导致的
- 是 spacing、short 还是 via 问题
- 改动发生在芯片哪个区域

### 1.4 OpenROAD 本身已经非常强

TritonRoute 是工业级详细路由器。对于 ISPD 2018/2019 标准 benchmark，baseline 往往已经 DRC-clean 或接近 clean（如 `ispd18_test1` 的 baseline 就是 DRC=0）。当 baseline 已经很好时，粗粒度 agent 根本没有优化空间，只能直接终止。

### 1.5 视觉输入信息损失严重

512×512 的 6-panel PNG 压缩了大量细节：
- DRC violation 的具体坐标、类型、涉及 net 丢失
- 走线的精确几何、层信息、via 位置丢失
- VLM 很难从缩略图判断"这里应该往左移 100nm"

### 1.6 DEF 文本编辑方式受限

当前通过文本操作 DEF（撕 `+ ROUTED`、插入 `BLOCKAGES`）再交给 OpenROAD 重跑。这种方式：
- 容易出错（DEF 格式复杂）
- 无法表达细粒度编辑（移动一个 via、调整一段走线）
- 每次都要完整 detailed route，非常慢

---

## 2. 重设计后的整体架构

新架构遵循 **Diagnose → Strategize → Localize → Execute → Evaluate → Attribute** 循环。

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ROUTING AGENT LOOP                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                 │
│  │   Baseline   │────▶│ State Extract│────▶│  DRC Parser  │                 │
│  │   Routing    │     │  (maps, DEF) │     │ (structured) │                 │
│  └──────────────┘     └──────────────┘     └──────────────┘                 │
│         │                      │                      │                       │
│         ▼                      ▼                      ▼                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    RICH STATE REPRESENTATION                           │   │
│  │  • Global 6-panel PNG (congestion, layers, DRC, overlay, hotspot)   │   │
│  │  • Per-violation table (type, bbox, layer, nets involved, severity) │   │
│  │  • Per-net routing features (HPWL, fanout, DRC count, layer usage)  │   │
│  │  • Local region crops (zoomed PNGs around violation clusters)       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│         │                                                                   │
│         ▼                                                                   │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐               │
│  │  VLM Diagnose│────▶│ VLM Strategize│────▶│ VLM Policy  │               │
│  │  (root cause)│     │ (select repair│     │  (JSON with  │               │
│  │              │     │   strategy)  │     │  fine actions)│               │
│  └──────────────┘     └──────────────┘     └──────────────┘               │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         POLICY EXECUTOR                              │   │
│  │  • Action dispatcher (maps JSON actions to Tcl / DEF / odb edits)   │   │
│  │  • Local edit engine (segment rip-up, via move, layer reassignment) │   │
│  │  • Global reroute fallback (for large-scale changes)                  │   │
│  │  • ODB-based incremental evaluator (fast DRC on edited region)      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     ATTRIBUTABLE FEEDBACK ENGINE                       │   │
│  │  • Pre/post diff: which nets changed, which regions changed            │   │
│  │  • DRC delta table: fixed violations, new violations, moved violations │   │
│  │  • Metric delta: WL/via change attributed to each edited net         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│         │                                                                   │
│         └──────────────────────┬────────────────────────────────────────────┘
│                                │ (loop back to State Extract)
│                                ▼
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    EARLY STOPPING / BEST SOLUTION                      │   │
│  │  • Patience counter on DRC reduction                                   │   │
│  │  • Best-solution checkpointing (DEF + metrics + history)               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 关键设计转变

| 方面 | 当前 | 重设计 |
|--------|---------|----------|
| **VLM 角色** | Executor（选择动作） | Diagnostician + Strategist（识别根因、选择修复策略） |
| **Action 粒度** | Net-level | Segment/via/region-level |
| **状态表示** | Global PNG + global metrics | Global PNG + per-violation table + per-net features + local crops |
| **评估** | Full `detailed_route` every iteration | Local fast DRC when possible; global DRC only after large changes |
| **反馈** | Global deltas | Attributable deltas per net / per region / per DRC type |

---

## 3. 细粒度 Action Space

新 action space 保留 `terminate`、`noop`，并将原有粗粒度动作拆分为 10 个细粒度动作。

### 3.1 动作分类

| 类别 | 动作 | 说明 |
|----------|---------|-------------|
| **Segment-level** | `rip_up_segment`, `reassign_layer`, `insert_jog` | 编辑单个 wire segment |
| **Via-level** | `move_via`, `change_via_type` | 编辑单个 via |
| **Net-level** | `rip_up_net`, `reroute_net_with_constraints`, `fishbone_route_net` | 粗/中粒度 net 编辑；`fishbone_route_net` 为自研拓扑初始化算子 |
| **Region-level** | `set_routing_blockage`, `set_soft_guidance`, `relax_region` | 修改局部布线资源 |
| **Control** | `terminate`, `noop` | 循环控制 |

### 3.2 具体动作定义

#### `rip_up_segment`
删除 net 上指定 layer 的指定 segment。

```json
{
  "action": "rip_up_segment",
  "parameters": {
    "net_name": "net_123",
    "segment_index": 2,
    "layer": "M3"
  },
  "reason": "Short with net_456 at (105000, 205000)",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`DEFEditor.rip_up_segment()` 解析 DEF NETS 段，定位指定 layer 的第 `segment_index` 个 ROUTED/NEW 条目并删除。必要时在断点处添加桥接 segment。

---

#### `reassign_layer`
把一段走线从一层改到另一层。

```json
{
  "action": "reassign_layer",
  "parameters": {
    "net_name": "net_123",
    "segment_index": 2,
    "from_layer": "M3",
    "to_layer": "M4"
  },
  "reason": "M3 spacing violation at (105000, 205000)",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`DEFEditor.reassign_layer()` 修改 DEF 中 segment 的 layer 名。若方向冲突，自动拆分为正交 segment 并插入 via。

---

#### `insert_jog`
在指定点插入 L 形绕线。

```json
{
  "action": "insert_jog",
  "parameters": {
    "net_name": "net_123",
    "segment_index": 2,
    "layer": "M3",
    "jog_point": [105500, 205500],
    "jog_direction": "horizontal"
  },
  "reason": "Avoid blockage at (105000, 205000)",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`DEFEditor.insert_jog()` 在原始 segment 上找到离 `jog_point` 最近的点，拆分成两段并插入 L 形路径。

---

#### `move_via`
把一个 via 平移到新坐标，修复 via-spacing 违规。

```json
{
  "action": "move_via",
  "parameters": {
    "net_name": "net_123",
    "via_index": 1,
    "old_position": [105000, 205000],
    "new_position": [105200, 205000],
    "via_type": "VIA23_1C"
  },
  "reason": "Via spacing violation with via at (105000, 205000)",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`DEFEditor.move_via()` 在 DEF 中定位 via 条目，更新坐标，并调整相邻 segment 连接到新 via 位置。

---

#### `change_via_type`
替换 via 类型（如单切 → 多切）。

```json
{
  "action": "change_via_type",
  "parameters": {
    "net_name": "net_123",
    "via_index": 1,
    "position": [105000, 205000],
    "new_type": "VIA23_2C"
  },
  "reason": "Min area violation on M2 via landing",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`DEFEditor.change_via_type()` 在 DEF 中替换 via 名称。实现前校验 LEF 是否定义该 via。

---

#### `rip_up_net`
保留的粗粒度动作，用于整 net 存在大量违规的场景。

```json
{
  "action": "rip_up_net",
  "parameters": {
    "net_name": "net_123"
  },
  "reason": "17 violations across 5 segments; full reroute recommended",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`RoutingToolkit.rip_up_nets()` + `detailed_route()`。

---

#### `reroute_net_with_constraints`
带层偏好和避让区域的单 net 重布。

```json
{
  "action": "reroute_net_with_constraints",
  "parameters": {
    "net_name": "net_123",
    "preferred_layers": ["M3", "M4"],
    "avoid_regions": [
      {"bbox": [100000, 200000, 110000, 210000], "reason": "Congestion hotspot"}
    ]
  },
  "reason": "Long-distance net currently on M2/M5; prefer M3/M4",
  "expected_impact": "wl_reduce"
}
```

**执行方式**：`TclGenerator` 生成 Tcl：撕 net → 注入临时 blockage → `set_routing_layers` → `detailed_route` → 移除临时 blockage。

---

#### `fishbone_route_net`

自研 net-level 布线算子。对目标 net 的所有 pin 用“一条/两条主干（trunk）+ 多条分支（branch）+ via 连接点”的鱼骨拓扑重新布线，作为 OpenROAD detailed route 的高质量初始结构，再由 OpenROAD 做 DRC 精修。

```json
{
  "action": "fishbone_route_net",
  "parameters": {
    "net_name": "net_123",
    "preferred_layers": ["Metal2", "Metal3", "Metal4"],
    "trunk_direction": "auto",
    "max_branches_per_trunk": 50,
    "obstacle_margin_nm": 200
  },
  "reason": "High fanout net; fishbone topology may reduce via count and ease DRC convergence",
  "expected_impact": "via_reduce"
}
```

**设计原则**：

- **层选择启发式**：从 `preferred_layers` 的最低层开始尝试，优先使用 LEF 中标记为 `preferred_direction` 的层；坐标全部按 `PITCH` / `OFFSET` 对齐。
- **必须做 obstacle 避让**：解析其他 net 的 segment / via 构建 obstacle map，trunk 选址和 branch 走线均考虑避让。
- **可用于所有 net**：VLM 可建议任意 net；也可由启发式自动选择高 fanout / 长 HPWL net。
- **OpenROAD 仍是最终验证者**：生成鱼骨路径后写回 DEF，调用 detailed route / DRC；若 DRC 恶化，优先保留无冲突的鱼骨路径，仅对冲突区域做局部修复或 OpenROAD 局部重布；仅当局部修复失败时才整 net fallback。

**执行方式**：

```
读取 DEF/LEF
  │
  ▼
提取 net pin 坐标与 access 点
  │
  ▼
启发式选择 trunk 方向、层、位置
  ├── 层：从 preferred_layers 最低层开始，按 LEF preferred_direction 筛选
  ├── 方向：默认与 pin 分布长边正交；bbox 接近方形时取层 preferred_direction
  └── 位置：在 bbox 内按 track 扫描，选 obstacle density 最低的 track
  │
  ▼
投影 pin 到 trunk，合并过近点
  │
  ▼
生成分支（branch）
  ├── 从 pin access 点到 trunk 连接点走 Manhattan 路径
  ├── 遇到 obstacle 时插入局部 jog 避让
  └── 避让搜索限制在 net bbox + margin 内
  │
  ▼
在 branch-trunk 交汇处插入 via（使用 LEF 默认 via）
  │
  ▼
DEFEditor 删除旧布线并写回新鱼骨路径
  │
  ▼
OpenROAD detailed_route -output_drc 验证
  │
  ▼
若 DRC 恶化，保留清洁鱼骨路径，对冲突区域局部修复；失败或无路径时再整 net fallback OpenROAD。
```

**Obstacle 避让**：

- 收集非目标 net 的 segment / via，按层扩展 `obstacle_margin_nm`。
- Trunk 选址 cost：`cost = w1 * obstacle_density + w2 * branch_total_length + w3 * via_count`。
- Branch 避让：若 Manhattan 直线路径与 obstacle 冲突，在冲突点附近做局部 A* / 扫描搜索，生成 L 形或 Z 形 jog；搜索范围限制在目标 net bbox 外扩 margin。

**风险与 Fallback**：

| 风险 | Fallback |
|---|---|
| 某 pin 无合法 access 点 | 跳过该 pin 并记录；整 net fallback OpenROAD |
| Branch 避让失败 | 该 branch 或整 net fallback OpenROAD |
| 生成后 DRC 增加 | 保留无冲突的鱼骨路径，仅对冲突 segment/branch 做局部修复（jog/改层/OpenROAD 局部重布）；局部修复失败再整 net fallback |
| 层选择导致 off-grid | 所有坐标按 LEF PITCH / OFFSET 对齐 |

**执行方式**：`PolicyExecutor` 分发给 `LocalRouter.fishbone_route()` → `DEFEditor.replace_net_routing()` → `OpenROADProvider.detailed_route()`；若出现新 DRC，先由 `LocalRouter`/`DEFEditor` 做局部修复，失败时整 net fallback → `AttributionEngine` 记录 delta。

---

#### `set_routing_blockage`
改进为支持 per-layer 和 soft/hard blockage。

```json
{
  "action": "set_routing_blockage",
  "parameters": {
    "bbox": [100000, 200000, 110000, 210000],
    "layers": ["M3"],
    "hardness": "soft"
  },
  "reason": "Congestion hotspot on M3; discourage but don't block",
  "expected_impact": "congestion_relief"
}
```

**执行方式**：
- `hard`：DEF `BLOCKAGES` 注入。
- `soft`：若 OpenROAD 支持 `set_routing_adjustment` 则用其提高该区域 routing cost；否则 fallback 到 hard。

---

#### `set_soft_guidance`
通过 `.guide` 文件引导 net 走特定路径。

```json
{
  "action": "set_soft_guidance",
  "parameters": {
    "net_name": "net_123",
    "guide_points": [
      [100000, 200000],
      [105000, 205000],
      [110000, 210000]
    ],
    "layer": "M3"
  },
  "reason": "Guide net away from congestion hotspot",
  "expected_impact": "congestion_relief"
}
```

**执行方式**：合并临时 guide 到现有 guide 文件，调用 `read_guides` + `detailed_route`。

---

#### `relax_region`
移除之前设置的 blockage/guidance，给 router 更多自由度。

```json
{
  "action": "relax_region",
  "parameters": {
    "bbox": [100000, 200000, 110000, 210000],
    "layers": ["M3"]
  },
  "reason": "Previous blockage too aggressive; remove to allow rerouting",
  "expected_impact": "drc_fix"
}
```

**执行方式**：`DEFEditor` 移除重叠的 `BLOCKAGES` 条目，并从 guide 文件中删除对应点。

---

#### `terminate` / `noop`

```json
{
  "action": "terminate",
  "parameters": {},
  "reason": "DRC is clean; no further improvement possible"
}
```

```json
{
  "action": "noop",
  "parameters": {},
  "reason": "State is stable; wait for next iteration"
}
```

---

## 4. 状态表示增强

### 4.1 结构化 DRC 表

新增 `DRCViolation` dataclass，替代当前简单的 `(x, y, type)` 列表。

```python
@dataclass
class DRCViolation:
    violation_id: str          # 唯一 ID，如 "v_0_0"
    vtype: str                 # spacing / short / min_width / end_of_line / via_spacing
    layer: str                 # "M2", "M3", ...
    bbox: Tuple[int, int, int, int]   # (x1, y1, x2, y2) DBU
    center: Tuple[int, int]    # (cx, cy) DBU
    nets_involved: List[str]   # 涉及 net 名
    segment_indices: List[Tuple[str, int]]  # [(net_name, seg_index), ...]
    severity: float            # 综合严重度
    description: str           # DRC 报告原始描述
```

**严重度计算**：
```python
def compute_severity(v: DRCViolation, congestion_map: np.ndarray) -> float:
    base_weights = {
        "short": 10.0, "spacing": 2.0, "min_width": 3.0,
        "end_of_line": 2.5, "via_spacing": 1.5, "other": 1.0
    }
    base = base_weights.get(v.vtype.lower(), 1.0)
    # 在 violation center 处查询 congestion map
    cong_val = lookup_congestion(congestion_map, v.center)
    return base * (1.0 + cong_val)
```

**解析实现**：扩展 `extract_drc_report()`：
1. 解析 `detailed_route -output_drc` 报告，获取类型、层、bbox。
2. 与 DEF NETS 段交叉，找出 bbox 内有哪些 net 的 segment/via。
3. 分配唯一 `violation_id`。

### 4.2 Per-net 路由特征

新增 `NetRoutingFeatures`：

```python
@dataclass
class NetRoutingFeatures:
    net_name: str
    fanout: int
    hpwl_um: float
    routed_segments: int
    via_count: int
    layer_usage: Dict[str, int]      # {"M2": 5, "M3": 12, ...}
    drc_count: int
    drc_violation_ids: List[str]
    congestion_score: float          # net bbox 内平均 congestion
    is_critical: bool                # fanout > 50 或 HPWL > 500um
```

新增 `RoutingToolkit.extract_net_routing_features(def_file, drc_violations) -> Dict[str, NetRoutingFeatures]`：
1. 解析 DEF NETS 段统计 segment/via/层分布。
2. 从 pin 坐标计算 HPWL。
3. 与 `drc_violations` 交叉统计 per-net DRC。
4. 用 congestion map 计算 net bbox 内平均 congestion。

### 4.3 局部裁剪图像

在 `VisualRenderer` 中新增 `render_violation_crops(state, max_crops=6)`：
1. 按空间邻近度对 violation 做网格聚类。
2. 按 cluster 总严重度排序。
3. 对 Top N cluster 生成 512×512 放大图，包含 congestion、layer、DRC marker、涉及 net 标注。
4. 保存为 `violation_crop_{cluster_id}_iter_{iteration}.png`。

---

## 5. VLM Prompt / Policy Schema 变化

### 5.1 System Prompt 重写

要求 VLM：
1. 先诊断每个 violation cluster 的根因。
2. 再选择修复策略。
3. 优先使用 segment/via 级编辑处理孤立违规。
4. 仅在 net 存在 >5 个违规或跨 >3 个 segment 时才用 net-level rip-up。
5. 仅在多 net 拥堵热点才使用 region-level blockage。

### 5.2 User Prompt 新增内容

```markdown
### DRC Violation Table (Top 20 by severity)
| ID | Type | Layer | Center (x,y) | Nets Involved | Severity | Description |
|----|------|-------|--------------|---------------|----------|-------------|
| v_0_0 | spacing | M3 | (105000,205000) | net_123, net_456 | 4.5 | M3 spacing < 0.14um |
| v_0_1 | short | M2 | (110000,210000) | net_789, net_101 | 12.0 | Overlap at ... |

### Per-Net Routing Features (Top 10 by DRC count)
| Net | Fanout | HPWL (um) | Segments | Vias | DRCs | Layers | Congestion |
|-----|--------|-----------|----------|------|------|--------|------------|
| net_123 | 45 | 520.5 | 12 | 8 | 3 | M3:8,M4:4 | 2.1 |

### Previous Action Result
Last action: reassign_layer net_123 seg 2 M3→M4
Affected nets: [net_123]
Fixed violations: [v_0_0]
New violations: []
Moved violations: []
WL delta: +3.2 um
Via delta: +1
```

### 5.3 输出 JSON Schema

```json
{
  "routing_policy": {
    "iteration": 0,
    "strategy_type": "segment_level_repair",
    "analysis": "3 spacing violations on M3 caused by net_123 and net_456 running parallel.",
    "diagnosis": [
      {
        "cluster_id": "cluster_0",
        "root_cause": "M3 spacing: net_123 segment 2 and net_456 segment 1 are 0.12um apart (min 0.14um)",
        "recommended_action": "reassign_layer on net_123 segment 2 from M3 to M4",
        "confidence": 0.85
      }
    ],
    "priority_actions": [
      {
        "action": "reassign_layer",
        "parameters": {
          "net_name": "net_123",
          "segment_index": 2,
          "from_layer": "M3",
          "to_layer": "M4"
        },
        "reason": "Fix M3 spacing violation v_0_0",
        "expected_impact": "drc_fix"
      }
    ],
    "termination_check": false,
    "next_state_focus": "Check if M3 spacing violations are resolved and if M4 congestion increased"
  }
}
```

---

## 6. Executor 设计

### 6.1 两层架构

| 层 | 类 | 职责 |
|-------|-------|----------------|
| Action Dispatcher | `PolicyExecutor` | 解析 JSON policy、校验动作、分发到低层 handler 或 EDAProvider |
| Local Edit Engine | `DEFEditor`, `LocalRouter`, `ODBEditor`, `TclGenerator` | 执行 DEF 文本编辑、自研局部布线算子、odb 编辑或 Tcl 脚本生成 |
| EDA Backend | `EDAProvider` / `OpenROADProvider` | 全局/详细布线、DRC/metrics 提取，支持未来切换商业 EDA |

### 6.2 PolicyExecutor

新建 `src/policy_executor.py`：
- `execute_policy(def_file, policy, current_violations, current_net_features)` 依次执行 policy 中的 action。
- 每个 action 包在 try/except 中，失败时记录错误并继续。
- segment/via/region 级编辑交给 `DEFEditor`/`LocalRouter`/`ODBEditor`；全局布线、DRC 验证和 metrics 提取统一通过 `EDAProvider` 完成。
- 根据编辑 net 数量决定做 local 还是 global 评估。
- 返回 `(new_def, new_metrics, execution_report)`。

### 6.3 DEFEditor

新建 `src/def_editor.py`：
- `_parse_nets_section()` / `_write_def()`：把 DEF 拆成 header / nets_dict / footer。
- `rip_up_segment()`：删除指定 segment。
- `reassign_layer()`：修改 segment layer。
- `insert_jog()`：插入 L 形绕线。
- `move_via()`：平移 via。
- `change_via_type()`：替换 via 类型。
- `relax_region()`：移除重叠 blockage。

### 6.4 TclGenerator

新建 `src/tcl_generator.py`：
- `generate_reroute_with_constraints()`：为 `reroute_net_with_constraints` 生成 Tcl。
- `generate_local_drc_check()`：为局部 DRC 生成 Tcl（OpenROAD 不支持 net 过滤时 fallback 到全局 DRC）。

### 6.5 ODBEditor（可选，增量引入）

新建 `src/odb_editor.py`：
- 使用 OpenROAD `odb` Python API 直接操作 design database。
- 更快速、更稳健，尤其适用于复杂编辑（插入带 via 的 jog）。
- `PolicyExecutor` 优先尝试 `ODBEditor`，不可用时回退到 `DEFEditor`。

---

### 6.6 EDA 后端抽象层（EDAProvider）

为支持未来从 OpenROAD 切换到商业 EDA 工具，引入 `EDAProvider` 抽象接口。OpenROAD 只是该接口的第一个实现；PolicyExecutor 与 AgentController 均只依赖接口，不依赖具体工具命令。

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

**默认实现**：`OpenROADProvider` 封装现有 `RoutingToolkit` 的 Tcl 子进程调用、报告解析和全局/详细布线流程。

**未来扩展**：新增 `CadenceInnovusProvider`、`SynopsysICC2Provider` 等实现时，只需实现上述接口，无需修改 `RoutingAgent`、`PolicyExecutor`、`VisualRenderer` 等上层逻辑。

**配置化切换**：

```yaml
# config/agent_config.yaml
eda_provider: openroad   # cadence_innovus / synopsys_icc2 / ...

openroad:
  exe: /path/to/openroad
  threads: 8

cadence_innovus:
  exe: /path/to/innovus
  license_server: ...
```

**迁移成本说明**：

| 能力 | OpenROAD | 商业 EDA 差异 |
|---|---|---|
| 进程/API 调用 | `openroad -exit script.tcl` | CLI/启动参数/license 模式不同 |
| 全局/详细布线 | `global_route` / `detailed_route` | 对应 `routeDesign`、`nanoRoute`、`route_opt` 等 |
| DRC 报告 | `detailed_route -output_drc` | 格式不同，但 `EDAProvider` 负责解析为统一 `DRCViolation` |
| 增量路由 | 撕 DEF + `detailed_route` | `ecoRoute` 等增量命令，需单独实现 |
| Blockage/Guide | DEF BLOCKAGES / guide 文件 | 可能需要 `.tdf`、`.gbc`、`.route_guide` 等 |

**关键原则**：DEF/LEF 仍作为通用几何交换格式；几何编辑（`DEFEditor`/`ODBEditor`）和视觉渲染尽量保持 EDA 无关；变化的是命令调用和报告解析。

---

## 7. 评估与可归因反馈

### 7.1 AttributionEngine

新建 `src/attribution_engine.py`：
- `compute_delta(prev_metrics, new_metrics, prev_violations, new_violations, execution_report)`
- 输出：
  - global DRC/WL/Via delta
  - fixed / new / moved / persisted violation IDs
  - per-net DRC delta
  - per-action 执行结果

### 7.2 反馈到 VLM

把归因结果序列化到下一轮 prompt：

```markdown
### DRC Attribution
Fixed violations: 2 (v_0_0, v_0_1)
New violations: 1 (v_0_5)
Moved violations: 0
Persisted violations: 1 (v_0_2)

### Per-Net DRC Delta
| Net | Prev DRCs | New DRCs | Delta |
|-----|-----------|----------|-------|
| net_123 | 3 | 0 | -3 |
| net_789 | 0 | 1 | +1 (NEW) |
```

---

## 8. 实施路线图

### Phase 1：结构化状态提取（1-2 周）
- `src/routing_toolkit.py`：新增 `DRCViolation`、`NetRoutingFeatures` 及提取方法。
- `src/visual_renderer.py`：新增 `render_violation_crops`。
- `src/vlm_policy.py`：重写 prompt，加入 DRC 表和 net 特征（保持旧 schema 兼容）。
- `tests/test_routing_toolkit.py`：增加对应测试。

### Phase 2：DEF 编辑器与 EDA 抽象层（2-3 周）
- 新建 `src/def_editor.py`。
- 新建 `src/eda_provider.py` + `src/openroad_provider.py`：把现有 OpenROAD Tcl 调用、报告解析封装为 `EDAProvider` 接口。
- `src/routing_toolkit.py`：作为 `OpenROADProvider` 的薄封装或逐步迁移。
- 用合成 DEF 和真实 ISPD DEF 做单元测试。

### Phase 3：PolicyExecutor、LocalRouter 与归因（3-4 周）
- 新建 `src/policy_executor.py`、`src/tcl_generator.py`、`src/attribution_engine.py`、`src/local_router.py`。
- 实现 `fishbone_route_net` 算子（`src/fishbone_router.py`），接入 `PolicyExecutor`。
- 修改 `src/agent_controller.py`：集成 `PolicyExecutor`、`EDAProvider`、richer state、归因反馈。
- 更新 `src/vlm_policy.py` JSON schema 校验。

### Phase 4：局部评估与 odb（4-5 周）
- 新建 `src/odb_editor.py`。
- `PolicyExecutor._evaluate_local()`：优先使用 odb 做局部 DRC。

### Phase 5：集成验证（5-6 周）
- `scripts/run_agent.py`：增加 `--enable-fine-actions`、`--use-odb` 开关。
- `config/agent_config.yaml`：增加细粒度 action、局部评估、归因配置。
- 在 `ispd18_test1-5` 上做 ablation study 和回归测试。

---

## 9. 验证计划

### 9.1 测试基准

| Benchmark | 复杂度 | 用途 |
|---|---|---|
| ispd18_test1 | 低 | 快速验证端到端流程 |
| ispd18_test2-3 | 中 | 验证细粒度 action 效果 |
| ispd18_test4-5 | 高 | 验证规模化与稳定性 |

### 9.2 成功标准

| 指标 | 当前 Agent | 目标 |
|---|---|---|
| 每次迭代 DRC 减少 | ~1-5 | ~5-20 |
| test1-3 达到 DRC=0 迭代数 | 10-20 | 5-10 |
| test1-3 总运行时间 | 30-60 min | 20-40 min |
| Wirelength 回退 | < 5% | < 3% |
| Via 回退 | < 10% | < 5% |
| Action 成功率 | ~70% | ~85% |

### 9.3 Ablation Study

1. 无细粒度 action（当前基线）。
2. 仅细粒度 action，无归因反馈。
3. 细粒度 action + 归因反馈（完整新方案）。
4. 细粒度 action + 归因反馈 + `odb` 局部评估。

### 9.4 回归测试

- 新 agent 仍能使用旧 coarse action space 运行（向后兼容）。
- 对 malformed VLM JSON 能 fallback 到 `noop` 或 `terminate`。
- OpenROAD 超时时能优雅降级。

---

## 10. 关键设计决策总结

| 决策 | 理由 |
|----------|-----------|
| 引入 `EDAProvider` 抽象层 | 默认使用 OpenROAD，未来可无缝接入商业 EDA；上层代码不耦合具体工具命令。 |
| 通过 `EDAProvider` 做全局 route / DRC / metrics | 稳定、版本无关、报告解析由 provider 自己负责。 |
| 新增 DEF 文本编辑器做细粒度编辑 | 纯 Python、无外部依赖、任何 DEF 都能用。 |
| 增量引入 `odb` | 更稳健高效，但可选，避免强依赖。 |
| VLM 作为诊断器 | 结构化 DRC 表 + per-net 特征让 VLM 能推理根因。 |
| 可归因反馈 | 闭环：VLM 能学习哪些动作修复了哪些违规。 |
| 局部评估 fallback | 全局 `detailed_route` 始终作为安全 fallback。 |
| 自研算子 + OpenROAD 精修 | `fishbone_route_net` 等自研算子生成候选拓扑，OpenROAD 负责 DRC 收敛，分工清晰。 |

---

## 11. 关键文件清单

- `src/routing_toolkit.py` — 扩展 DRC/net 特征提取（逐步迁移到 OpenROADProvider）。
- `src/visual_renderer.py` — 局部 violation crop。
- `src/vlm_policy.py` — 新 prompt 与 schema（增加 `fishbone_route_net`）。
- `src/agent_controller.py` — 集成 PolicyExecutor、EDAProvider 与反馈。
- `src/eda_provider.py` — EDA 后端抽象接口。
- `src/openroad_provider.py` — OpenROAD 实现（封装现有 Tcl 流程）。
- `src/def_editor.py` — 新增 DEF 文本编辑器。
- `src/local_router.py` — 自研局部布线算子接口。
- `src/fishbone_router.py` — 鱼骨图 net-level 布线算子。
- `src/policy_executor.py` — 新增动作分发器。
- `src/attribution_engine.py` — 新增可归因 delta 计算。
- `src/tcl_generator.py` — 新增 Tcl 脚本生成器（可由 OpenROADProvider 使用）。
- `src/odb_editor.py` — 新增 odb 编辑器（可选）。
- `scripts/run_agent.py` — 新增 CLI 开关。
- `config/agent_config.yaml` — 新增配置项（含 `eda_provider`）。
- `tests/test_def_editor.py` — DEFEditor 单元测试。
- `tests/test_attribution_engine.py` — 归因引擎测试。
- `tests/test_fishbone_router.py` — FishboneRouter 单元测试。

---

## 12. 下一步建议

最优先实现 **Phase 1 + Phase 2 中的 `EDAProvider` 骨架与 `DEFEditor` 的 `rip_up_segment` / `reassign_layer`**。这样即使其他模块尚未完成，也能在下一轮迭代中验证：
1. VLM 是否能从结构化 DRC 表中诊断出具体 violation。
2. segment-level 编辑是否能真正修复 baseline 后剩余的 DRC。
3. 通过 `EDAProvider` 切换 OpenROAD 流程是否对上层透明。

待这两个动作稳定后，再逐步加入：
- `move_via`、`insert_jog`、`change_via_type`；
- `fishbone_route_net` 自研算子与 `LocalRouter`；
- `odb` 局部评估；
- 后续商业 EDA provider 实现（按需）。
