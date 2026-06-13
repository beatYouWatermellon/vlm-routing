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
| **Net-level** | `rip_up_net`, `reroute_net_with_constraints` | 粗粒度 net 编辑（保留为 fallback） |
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
| Action Dispatcher | `PolicyExecutor` | 解析 JSON policy、校验动作、分发到低层 handler |
| Low-Level Handlers | `DEFEditor`, `TclGenerator`, `ODBEditor` | 执行 DEF 文本编辑或 Tcl 脚本 |

### 6.2 PolicyExecutor

新建 `src/policy_executor.py`：
- `execute_policy(def_file, policy, current_violations, current_net_features)` 依次执行 policy 中的 action。
- 每个 action 包在 try/except 中，失败时记录错误并继续。
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

### Phase 2：DEF 编辑器（2-3 周）
- 新建 `src/def_editor.py`。
- `src/routing_toolkit.py`：增加对 `DEFEditor` 的薄封装。
- 用合成 DEF 和真实 ISPD DEF 做单元测试。

### Phase 3：PolicyExecutor 与归因（3-4 周）
- 新建 `src/policy_executor.py`、`src/tcl_generator.py`、`src/attribution_engine.py`。
- 修改 `src/agent_controller.py`：集成 `PolicyExecutor`、richer state、归因反馈。
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
| 保留 Tcl 子进程做全局 route | 稳定、版本无关、无 odb 依赖。 |
| 新增 DEF 文本编辑器做细粒度编辑 | 纯 Python、无外部依赖、任何 DEF 都能用。 |
| 增量引入 `odb` | 更稳健高效，但可选，避免强依赖。 |
| VLM 作为诊断器 | 结构化 DRC 表 + per-net 特征让 VLM 能推理根因。 |
| 可归因反馈 | 闭环：VLM 能学习哪些动作修复了哪些违规。 |
| 局部评估 fallback | 全局 `detailed_route` 始终作为安全 fallback。 |

---

## 11. 关键文件清单

- `src/routing_toolkit.py` — 扩展 DRC/net 特征提取。
- `src/visual_renderer.py` — 局部 violation crop。
- `src/vlm_policy.py` — 新 prompt 与 schema。
- `src/agent_controller.py` — 集成 PolicyExecutor 与反馈。
- `src/def_editor.py` — 新增 DEF 文本编辑器。
- `src/policy_executor.py` — 新增动作分发器。
- `src/attribution_engine.py` — 新增可归因 delta 计算。
- `src/tcl_generator.py` — 新增 Tcl 脚本生成器。
- `src/odb_editor.py` — 新增 odb 编辑器（可选）。
- `scripts/run_agent.py` — 新增 CLI 开关。
- `config/agent_config.yaml` — 新增配置项。
- `tests/test_def_editor.py` — DEFEditor 单元测试。
- `tests/test_attribution_engine.py` — 归因引擎测试。

---

## 12. 下一步建议

最优先实现 **Phase 1 + Phase 2 中的 `rip_up_segment` 和 `reassign_layer`**。这样即使其他模块尚未完成，也能在下一轮迭代中验证：
1. VLM 是否能从结构化 DRC 表中诊断出具体 violation。
2. segment-level 编辑是否能真正修复 baseline 后剩余的 DRC。
3. 可归因反馈是否帮助 VLM 在后续迭代中做出更好决策。

待这两个动作稳定后，再逐步加入 `move_via`、`insert_jog`、`change_via_type` 和 odb 局部评估。
