# VLM-Routing-Agent 完整可执行技术方案

## 基于 OpenROAD + 多模态大模型 的 Agent-Based 数字电路自动布线系统

**Version**: v2.0 Complete Edition  
**Goal**: 提供一份可直接执行的完整技术方案，使AI Agent能够零歧义地实现全部配置和算法脚本  
**Environment**: Ubuntu 22.04 LTS, Python 3.10, OpenROAD v2.0+, Gemini API / Claude API  
**Target**: ISPD 2018/2019 Initial Detailed Routing Contest Benchmarks

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [环境配置与依赖安装](#2-环境配置与依赖安装)
3. [项目目录结构](#3-项目目录结构)
4. [OpenROAD Tcl 命令参考](#4-openroad-tcl-命令参考)
5. [核心模块实现](#5-核心模块实现)
   - 5.1 OpenROAD 工具包装器 (`routing_toolkit.py`)
   - 5.2 视觉状态渲染器 (`visual_renderer.py`)
   - 5.3 VLM 策略生成器 (`vlm_policy.py`)
   - 5.4 Agent 主控制器 (`agent_controller.py`)
   - 5.5 ISPD 评估器 (`ispd_evaluator.py`)
   - 5.6 工具函数 (`utils.py`)
6. [执行脚本](#6-执行脚本)
7. [Agent 执行流程](#7-agent-执行流程)
8. [ISPD 评估标准与评分实现](#8-ispd-评估标准与评分实现)
9. [验证与基准测试](#9-验证与基准测试)
10. [故障排除](#10-故障排除)

---

## 1. 系统架构总览

### 1.1 数据流架构

```
[Input] ISPD Benchmark (LEF/DEF/Guide)
    |
    v
[OpenROAD Baseline] FastRoute (Global) -> TritonRoute (Detailed)
    |
    v
[State Extraction] ----> 视觉状态 (PNG, 多通道图像)
                   |----> 文本状态 (JSON, 结构化数据)
                   |----> 网表统计 (JSON, 网络特征)
                   |----> DRC报告 (JSON, 违规详情)
    |
    v
[VLM Policy Engine] Gemini 2.5 Flash / Claude 3.5 Sonnet
    |  Input: 多图像 (congestion map, routing layers, DRC markers)
    |         + 结构化文本 (metrics, netlist stats)
    |  Output: 路由策略 JSON (动作序列)
    |
    v
[Agent Controller] 策略解析 -> 工具调用 -> 执行验证
    |
    |--> rip_up_reroute(target_nets, strategy)
    |--> incremental_route(region, params)
    |--> layer_assign(net_group, layers)
    |--> set_blockage(region, layers)
    |
    v
[Evaluation] DRC + Wirelength + Via Count + ISPD Score
    |
    |--> DRC == 0 ? 终止并保存结果
    |--> DRC > 0  ? 视觉差分 + 指标差分 -> 下一迭代
```

### 1.2 模块交互图

```
+------------------+      +-------------------+      +------------------+
|  RoutingToolkit  |<---->|  AgentController  |<---->| VLMPolicyGenerator|
|  (OpenROAD包装)   |      |  (主控制循环)      |      |  (多模态策略)      |
+------------------+      +-------------------+      +------------------+
         ^                         |                           |
         |                         v                           v
+------------------+      +-------------------+      +------------------+
| VisualRenderer   |<---->|   ISPDEvaluator   |      |   Gemini/Claude  |
| (状态可视化)      |      |   (评分与评估)     |      |    API Client    |
+------------------+      +-------------------+      +------------------+
```

### 1.3 核心设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| OpenROAD接口方式 | **TCL脚本桥接** | 比Python odb绑定更稳定，所有版本兼容 |
| 状态表示 | **多通道PNG图像 + 结构化JSON** | VLM对图像理解能力强，JSON提供精确数值 |
| VLM模型 | **Gemini 2.5 Flash** | 原生多模态，1M上下文，成本效益高 |
| 布线控制 | **DEF文件操作 + 增量Tcl命令** | 无需修改OpenROAD源码，完全可逆 |
| 评分标准 | **ISPD 2019官方评分公式** | 业界公认，可量化对比 |

---

## 2. 环境配置与依赖安装

### 2.1 系统依赖

```bash
# 更新系统包
sudo apt-get update && sudo apt-get upgrade -y

# 安装编译工具和依赖库
sudo apt-get install -y \
    build-essential \
    cmake \
    git \
    wget \
    curl \
    python3.10 \
    python3.10-dev \
    python3.10-venv \
    python3-pip \
    libboost-all-dev \
    libspdlog-dev \
    libeigen3-dev \
    liblemon-dev \
    swig \
    tcl-dev \
    tk-dev \
    bison \
    flex \
    libreadline-dev \
    zlib1g-dev \
    libomp-dev \
    g++-10 \
    gcc-10 \
    qtbase5-dev \
    qtchooser \
    qt5-qmake \
    qttools5-dev-tools \
    libqt5charts5-dev \
    libqt5svg5-dev

# 设置GCC-10为默认编译器（OpenROAD推荐）
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-10 100
sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-10 100
```

### 2.2 安装 OpenROAD（推荐方式：官方预编译二进制）

```bash
# 方式1：通过 Conda 安装（推荐，最简单）
# 安装 Miniconda（如未安装）
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p $HOME/miniconda3
source $HOME/miniconda3/bin/conda init bash
source ~/.bashrc

# 创建 OpenROAD 专用环境
conda create -n openroad_env python=3.10 -y
conda activate openroad_env

# 从 conda-forge 安装 OpenROAD
conda install -c conda-forge openroad -y

# 验证安装
openroad -version
# 期望输出: OpenROAD v2.0-xxxx [features included...]

# 方式2：从源码编译（如需最新功能或调试）
# git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD.git
# cd OpenROAD
# ./etc/Build.sh
# 编译产物在 build/src/openroad

# 设置环境变量（添加到 ~/.bashrc）
echo 'export OPENROAD_EXE=$(which openroad)' >> ~/.bashrc
echo 'export OPENROAD_HOME=$CONDA_PREFIX' >> ~/.bashrc
source ~/.bashrc
```

**关键说明**：
- OpenROAD 的 `detailed_route` 命令基于 **TritonRoute** 实现
- `global_route` 命令基于 **FastRoute** 实现（支持 `-use_cugr` 使用CUGR）
- 所有布线命令均通过 Tcl 脚本调用，无需 Python 绑定
- 本方案采用 **Tcl 脚本桥接** 方式，通过 `subprocess` 调用 `openroad -exit script.tcl`

### 2.3 Python 虚拟环境

```bash
# 创建项目目录
mkdir -p ~/vlm-routing-agent && cd ~/vlm-routing-agent

# 创建 Python 虚拟环境
python3.10 -m venv venv
source venv/bin/activate

# 升级 pip
pip install --upgrade pip

# 安装核心依赖
cat > requirements.txt << 'EOF'
# VLM API Clients
google-generativeai>=0.7.0
anthropic>=0.28.0
openai>=1.35.0

# 数据处理
numpy>=1.24.0
pandas>=2.0.0
pydantic>=2.0.0

# 可视化
matplotlib>=3.7.0
pillow>=10.0.0

# 工具
python-dotenv>=1.0.0
tqdm>=4.65.0
pyyaml>=6.0
requests>=2.31.0

# 可选：GNN扩展（未来使用）
# torch>=2.0.0
# torch-geometric>=2.3.0
EOF

pip install -r requirements.txt
```

### 2.4 API 密钥配置

```bash
# 创建环境变量文件
cat > .env << 'EOF'
# Google Gemini API (推荐，主选)
GEMINI_API_KEY=your_gemini_api_key_here

# Anthropic Claude API (备选1)
# ANTHROPIC_API_KEY=your_claude_key_here

# OpenAI API (备选2)
# OPENAI_API_KEY=your_openai_key_here

# 并发控制
MAX_VLM_RETRIES=3
VLM_RETRY_DELAY=2

# OpenROAD配置
OPENROAD_EXE=/path/to/openroad
OPENROAD_THREADS=8
EOF

# 添加 .env 到 .gitignore
echo ".env" >> .gitignore
echo "venv/" >> .gitignore
echo "outputs/" >> .gitignore
echo "__pycache__/" >> .gitignore
```

**获取 API Key**：
- Gemini: https://aistudio.google.com/app/apikey
- Claude: https://console.anthropic.com/settings/keys
- OpenAI: https://platform.openai.com/api-keys

### 2.5 ISPD 数据集下载与准备

```bash
# 创建数据目录
mkdir -p data/ispd2018 data/ispd2019

# 下载 ISPD 2018 Initial Detailed Routing Contest 数据集
# 官方链接: https://www.ispd.cc/contests/18/
cd data/ispd2018
wget https://www.ispd.cc/contests/18/ISPD18_InitialDetailingRoutingContest.tar.gz
tar -xzf ISPD18_InitialDetailingRoutingContest.tar.gz

# 下载 ISPD 2019 Detailed Routing Contest 数据集
# 官方链接: https://www.ispd.cc/contests/19/
cd ../ispd2019
wget https://www.ispd.cc/contests/19/ISPD19_DetailedRoutingContest.tar.gz
tar -xzf ISPD19_DetailedRoutingContest.tar.gz

cd ~/vlm-routing-agent

# 数据集目录结构（解压后）：
# data/ispd2018/
#   ├── ispd18_test1/
#   │   ├── ispd18_test1.lef       # 技术库文件
#   │   ├── ispd18_test1.def       # 设计布局文件
#   │   └── ispd18_test1.guide     # 全局布线引导文件
#   ├── ispd18_test2/
#   │   └── ...
#   └── ispd18_test3/
#
# data/ispd2019/
#   ├── ispd19_test1/
#   │   ├── ispd19_test1.lef
#   │   ├── ispd19_test1.def
#   │   └── ispd19_test1.guide
#   ├── ispd19_test2/
#   │   └── ...
#   └── ispd19_test10/
```

---

## 3. 项目目录结构

```
vlm-routing-agent/
├── .env                          # API 密钥（gitignore）
├── .gitignore
├── requirements.txt              # Python依赖
├── README.md                     # 项目说明
│
├── config/
│   └── agent_config.yaml         # Agent参数配置
│
├── src/
│   ├── __init__.py
│   ├── routing_toolkit.py        # OpenROAD工具包装器（核心）
│   ├── visual_renderer.py        # 视觉状态渲染器
│   ├── vlm_policy.py             # VLM策略生成器
│   ├── agent_controller.py       # Agent主控制器
│   ├── ispd_evaluator.py         # ISPD评估接口
│   └── utils.py                  # 通用工具函数
│
├── scripts/
│   ├── run_baseline.py           # 运行基线流程
│   ├── run_agent.py              # 运行Agent优化
│   └── evaluate_ispd.py          # ISPD评分脚本
│
├── tcl/
│   ├── base_route.tcl            # 基线布线Tcl模板
│   ├── extract_metrics.tcl       # 指标提取Tcl模板
│   ├── check_drc.tcl             # DRC检查Tcl模板
│   └── extract_congestion.tcl    # 拥塞图提取Tcl模板
│
├── data/                         # ISPD数据集
│   ├── ispd2018/
│   └── ispd2019/
│
└── outputs/
    ├── baseline/                 # 基线结果
    ├── agent_runs/               # Agent迭代结果
    ├── visual_states/            # 渲染的视觉状态图像
    └── evaluation/               # 评估报告
```

---

## 4. OpenROAD Tcl 命令参考

本方案核心通过 **Tcl 脚本** 与 OpenROAD 交互。以下是所有使用的命令的完整规范。

### 4.1 文件读写命令

```tcl
# 读取LEF文件（技术信息 + 标准单元库）
read_lef [-tech] [-library] <filename>

# 读取DEF文件（设计布局）
read_def <filename>

# 写入DEF文件（保存结果）
write_def [-version 5.8|5.7|5.6|5.5|5.4|5.3] <filename>

# 读取/写入ODB数据库（二进制格式，更快）
read_db <filename>
write_db <filename>

# 读取全局布线引导文件
define_guide <filename>    ;# 或 GUI中的 read_guides
```

### 4.2 全局布线命令 (FastRoute)

```tcl
# 执行全局布线
global_route
    [-guide_file <out_file>]                # 输出引导文件路径
    [-congestion_iterations <iterations>]   # 拥塞消除迭代次数（默认50）
    [-congestion_report_file <file_name>]   # 拥塞报告文件（关键！）
    [-congestion_report_iter_step <steps>]  # 报告间隔步数
    [-grid_origin {x y}]                    # 网格原点
    [-critical_nets_percentage <percent>]   # 关键网络百分比
    [-skip_large_fanout_nets <fanout>]     # 跳过大扇出网络
    [-allow_congestion]                     # 允许残留拥塞
    [-verbose]                              # 详细输出

# 拥塞报告文件格式（每行）：
# GCell坐标、层名、Demand、Supply、Overflow
# 示例：10 15 metal3 5 3 2
```

### 4.3 详细布线命令 (TritonRoute)

```tcl
# 执行详细布线
detailed_route
    [-output_drc <filename>]                # 输出DRC报告（关键！）
    [-output_maze <filename>]               # 输出迷宫日志
    [-output_guide_coverage <filename>]     # 引导覆盖率报告
    [-drc_report_iter_step <step>]          # DRC报告迭代步数
    [-db_process_node <name>]               # 工艺节点
    [-droute_end_iter <iter>]               # 最大迭代次数（默认-1=自动）
    [-bottom_routing_layer <layer>]         # 最小布线层
    [-top_routing_layer <layer>]            # 最大布线层
    [-verbose <level>]                      # 详细级别
    [-clean_patches]                        # 清理补丁
    [-no_pin_access]                        # 禁用引脚访问
    [-save_guide_updates]                   # 保存引导更新

# DRC报告格式：每行包含违规类型、坐标、层名、说明
# 示例：Short 100500 205000 metal2 "Net1 and Net2 short"
```

### 4.4 报告命令

```tcl
# 报告线长
report_wire_length
    [-net <net_list>]                       # 指定网络列表（*表示全部）
    [-file <file>]                          # 输出到文件
    [-global_route]                         # 报告全局布线线长
    [-detailed_route]                       # 报告详细布线线长
    [-verbose]                              # 详细输出（每层线长）
    [-summary]                              # 汇总报告

# 输出示例（summary模式）：
# Total wire length = 1234567 um.
# Total wire length on LAYER metal1 = 12345 um.
# Total wire length on LAYER metal2 = 234567 um.
# Total number of vias = 89012.
# Up-via summary (total 89012):
#     metal1 to metal2: 45000
#     metal2 to metal3: 44012
```

### 4.5 布线层设置命令

```tcl
# 设置布线层范围
set_routing_layers
    -signal <min_layer>-<max_layer>         # 信号网络层范围
    -clock <min_layer>-<max_layer>          # 时钟网络层范围

# 示例
set_routing_layers -signal metal2-metal5 -clock metal3-metal6
```

### 4.6 完整 Tcl 脚本模板

#### 基线布线脚本 (`tcl/base_route.tcl`)

```tcl
# 基线完整布线流程：全局布线 + 详细布线
# 用法: openroad -exit base_route.tcl

# 读取输入
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

# 可选：读取全局布线引导（如存在）
if {[info exists ::env(GUIDE_FILE)] && [file exists $::env(GUIDE_FILE)]} {
    read_guides $::env(GUIDE_FILE)
}

# 设置布线层（根据工艺调整）
set_routing_layers -signal metal2-metal5

# 执行全局布线
global_route \
    -guide_file $::env(OUTPUT_DIR)/route.guide \
    -congestion_report_file $::env(OUTPUT_DIR)/congestion.rpt \
    -congestion_iterations 50 \
    -verbose

# 执行详细布线
detailed_route \
    -output_drc $::env(OUTPUT_DIR)/drc.rpt \
    -output_maze $::env(OUTPUT_DIR)/maze.log \
    -verbose 1

# 报告线长
report_wire_length -net * -detailed_route -summary \
    -file $::env(OUTPUT_DIR)/wirelength.rpt

# 写入结果DEF
write_def $::env(OUTPUT_DEF)

# 写入ODB（可选，用于快速恢复）
write_db $::env(OUTPUT_DIR)/routed.odb
```

#### DRC 检查脚本 (`tcl/check_drc.tcl`)

```tcl
# DRC检查脚本
# 用法: openroad -exit check_drc.tcl

read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

# 仅运行DRC检查（不解冲布线）
detailed_route \
    -output_drc $::env(OUTPUT_DRC) \
    -verbose 0
```

#### 拥塞图提取脚本 (`tcl/extract_congestion.tcl`)

```tcl
# 拥塞图提取脚本
# 用法: openroad -exit extract_congestion.tcl

read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

# 运行全局布线获取拥塞报告
global_route \
    -congestion_report_file $::env(CONGESTION_RPT) \
    -congestion_iterations 1 \
    -allow_congestion
```

---

## 5. 核心模块实现

### 5.1 OpenROAD 工具包装器 (`src/routing_toolkit.py`)

本模块是所有 OpenROAD 操作的统一入口。采用 **Tcl 脚本桥接** 架构，通过 `subprocess.run()` 调用 `openroad -exit script.tcl`。

**关键设计原则**：
1. 所有 Tcl 脚本通过 Python 字符串模板动态生成
2. 使用环境变量传递文件路径，避免硬编码
3. 每次 OpenROAD 调用独立进程，保证状态隔离
4. 所有输出写入临时文件，由 Python 解析

```python
"""
src/routing_toolkit.py

OpenROAD 工具包装器
提供统一接口：状态提取、布线执行、DRC检查、指标评估
核心设计：通过 Tcl 脚本桥接与 OpenROAD 交互

依赖：subprocess, os, json, re, numpy, pathlib, typing, dataclasses
"""

import os
import re
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Union
from dataclasses import dataclass, field, asdict
import numpy as np


# ==================== 数据结构定义 ====================

@dataclass
class RoutingMetrics:
    """布线指标数据结构"""
    drc_total: int = 0                  # DRC违规总数
    drc_spacing: int = 0                # 间距违规
    drc_min_width: int = 0              # 最小宽度违规
    drc_short: int = 0                  # 短路违规
    drc_end_of_line: int = 0            # 线端违规
    drc_via_spacing: int = 0            # Via间距违规
    drc_other: int = 0                  # 其他违规
    wirelength: float = 0.0             # 总线长 (um)
    via_count: int = 0                  # Via总数
    wirelength_per_layer: Dict[str, float] = field(default_factory=dict)  # 每层线长
    iteration: int = 0                  # 当前迭代次数
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @property
    def drc_breakdown(self) -> Dict[str, int]:
        """DRC违规分类统计"""
        return {
            'spacing': self.drc_spacing,
            'min_width': self.drc_min_width,
            'short': self.drc_short,
            'end_of_line': self.drc_end_of_line,
            'via_spacing': self.drc_via_spacing,
            'other': self.drc_other
        }


@dataclass
class RoutingState:
    """当前布线状态（用于VLM输入）"""
    def_file: str = ''                          # 当前DEF文件路径
    congestion_map: Optional[np.ndarray] = None # 拥塞热力图 (H, W)
    routing_layers: Dict[str, np.ndarray] = field(default_factory=dict)  # 各层布线状态
    metrics: Optional[RoutingMetrics] = None    # 当前指标
    netlist_stats: Optional[Dict] = None        # 网表统计
    drc_report: Optional[Dict] = None           # DRC报告详情
    drc_markers: List[Tuple[float, float]] = field(default_factory=list)  # DRC坐标标记
    iteration: int = 0


# ==================== 主类定义 ====================

class RoutingToolkit:
    """
    OpenROAD 路由工具包装器
    
    通过 Tcl 脚本桥接方式调用 OpenROAD 命令：
    1. 动态生成 Tcl 脚本
    2. 通过 subprocess 调用 openroad -exit script.tcl
    3. 解析输出文件获取结果
    
    Attributes:
        openroad_exe: OpenROAD 可执行文件路径
        work_dir: 工作目录（所有临时文件存放）
        lef_file: LEF 技术文件路径
    """
    
    def __init__(self, openroad_exe: str, work_dir: str, lef_file: str):
        self.openroad = openroad_exe
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.lef_file = Path(lef_file)
        self.current_def = None
        self.iteration = 0
        self._env = os.environ.copy()
        self._env['OPENROAD_EXE'] = openroad_exe
        
        # 验证 OpenROAD 可用
        self._validate_openroad()
    
    def _validate_openroad(self):
        """验证 OpenROAD 安装"""
        try:
            result = subprocess.run(
                [self.openroad, '-version'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"OpenROAD 验证失败: {result.stderr}")
            print(f"[OK] OpenROAD: {result.stdout.strip()}")
        except FileNotFoundError:
            raise RuntimeError(f"OpenROAD 未找到: {self.openroad}")
    
    def _run_tcl_script(self, tcl_content: str, env_vars: Optional[Dict] = None,
                       timeout: int = 600) -> Tuple[int, str, str]:
        """
        执行 Tcl 脚本并返回结果
        
        Args:
            tcl_content: Tcl 脚本内容
            env_vars: 额外环境变量
            timeout: 超时时间（秒）
            
        Returns:
            (returncode, stdout, stderr)
        """
        # 写入临时 Tcl 文件
        tcl_path = self.work_dir / f'_temp_{self.iteration}.tcl'
        tcl_path.write_text(tcl_content, encoding='utf-8')
        
        # 准备环境变量
        env = self._env.copy()
        if env_vars:
            env.update(env_vars)
        
        try:
            result = subprocess.run(
                [self.openroad, '-exit', '-no_init', str(tcl_path)],
                capture_output=True, text=True, env=env, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        finally:
            # 清理临时文件
            if tcl_path.exists():
                tcl_path.unlink()
    
    # ==================== 状态提取 APIs ====================
    
    def extract_congestion_map(self, def_file: str, 
                               resolution: int = 256) -> np.ndarray:
        """
        提取全局拥塞图
        
        方法：运行 global_route -congestion_report_file 并解析报告
        报告格式：每行 'gcell_x gcell_y layer demand supply overflow'
        
        Args:
            def_file: DEF 文件路径
            resolution: 输出网格分辨率
            
        Returns:
            (resolution, resolution) numpy array, 值域 [0, +inf)
            值越大表示拥塞越严重
        """
        congestion_rpt = self.work_dir / f'congestion_{self.iteration}.rpt'
        
        tcl_script = f"""
read_lef {self.lef_file}
read_def {def_file}
global_route -congestion_report_file {congestion_rpt} -congestion_iterations 1 -allow_congestion
"""
        retcode, stdout, stderr = self._run_tcl_script(tcl_script)
        
        if retcode != 0:
            print(f"[WARNING] 拥塞提取失败: {stderr[:200]}")
            return np.zeros((resolution, resolution))
        
        return self._parse_congestion_report(congestion_rpt, resolution)
    
    def _parse_congestion_report(self, rpt_file: Path, resolution: int) -> np.ndarray:
        """
        解析 FastRoute 拥塞报告为网格热力图
        
        报告格式示例（每行）：
            gcell_x gcell_y layer_name demand supply overflow
            10 15 metal3 5 3 2
            
        Args:
            rpt_file: 拥塞报告文件路径
            resolution: 目标分辨率
            
        Returns:
            numpy.ndarray: (resolution, resolution) 拥塞热力图
        """
        grid = np.zeros((resolution, resolution), dtype=np.float32)
        
        if not rpt_file.exists():
            return grid
        
        # 收集所有 GCell 坐标以计算映射比例
        all_coords = []
        overflow_data = []
        
        with open(rpt_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        x, y = int(parts[0]), int(parts[1])
                        overflow = float(parts[5])
                        all_coords.append((x, y))
                        overflow_data.append((x, y, overflow))
                    except (ValueError, IndexError):
                        continue
        
        if not all_coords:
            return grid
        
        # 计算 GCell 网格的实际边界
        max_gx = max(c[0] for c in all_coords) + 1
        max_gy = max(c[1] for c in all_coords) + 1
        
        # 映射到目标分辨率
        scale_x = resolution / max(max_gx, 1)
        scale_y = resolution / max(max_gy, 1)
        
        for gx, gy, overflow in overflow_data:
            px = min(int(gx * scale_x), resolution - 1)
            py = min(int(gy * scale_y), resolution - 1)
            grid[py, px] = max(grid[py, px], overflow)
        
        return grid
    
    def extract_routing_layers(self, def_file: str, 
                               layers: Optional[List[str]] = None,
                               resolution: int = 512) -> Dict[str, np.ndarray]:
        """
        提取指定层的路由状态
        
        通过解析 DEF 文件 NETS 段中的 ROUTED/NEW 语句提取走线坐标，
        渲染为二进制图像
        
        Args:
            def_file: DEF 文件路径
            layers: 要提取的层名列表（如 ['metal2', 'metal3']）
            resolution: 输出图像分辨率
            
        Returns:
            Dict[str, np.ndarray]: 层名 -> 二值图像 (H, W), 1=有走线, 0=无走线
        """
        if layers is None:
            layers = ['metal2', 'metal3', 'metal4', 'metal5']
        
        result = {}
        
        # 首先解析 DEF 获取设计边界
        die_area = self._parse_die_area(def_file)
        if die_area is None:
            return {layer: np.zeros((resolution, resolution)) for layer in layers}
        
        die_x1, die_y1, die_x2, die_y2 = die_area
        die_width = max(die_x2 - die_x1, 1)
        die_height = max(die_y2 - die_y1, 1)
        
        scale_x = resolution / die_width
        scale_y = resolution / die_height
        
        # 解析每层的走线
        for layer in layers:
            routing_map = np.zeros((resolution, resolution), dtype=np.uint8)
            segments = self._parse_def_routing_for_layer(def_file, layer)
            
            for (x1, y1, x2, y2) in segments:
                # 坐标映射到图像空间
                ix1 = int((x1 - die_x1) * scale_x)
                iy1 = int((y1 - die_y1) * scale_y)
                ix2 = int((x2 - die_x1) * scale_x)
                iy2 = int((y2 - die_y1) * scale_y)
                
                ix1 = max(0, min(ix1, resolution - 1))
                iy1 = max(0, min(iy1, resolution - 1))
                ix2 = max(0, min(ix2, resolution - 1))
                iy2 = max(0, min(iy2, resolution - 1))
                
                # 绘制线段（Bresenham算法）
                self._draw_line(routing_map, ix1, iy1, ix2, iy2)
            
            result[layer] = routing_map
        
        return result
    
    def _parse_die_area(self, def_file: str) -> Optional[Tuple[int, int, int, int]]:
        """解析 DEF 文件的 DIEAREA 段"""
        die_pattern = re.compile(
            r'DIEAREA\\s*\\(\\s*([\\d.]+)\\s+([\\d.]+)\\s*\\)\\s*\\(\\s*([\\d.]+)\\s+([\\d.]+)\\s*\\)',
            re.IGNORECASE
        )
        with open(def_file, 'r') as f:
            content = f.read()
        match = die_pattern.search(content)
        if match:
            return (int(float(match.group(1))), int(float(match.group(2))),
                   int(float(match.group(3))), int(float(match.group(4))))
        return None
    
    def _parse_def_routing_for_layer(self, def_file: str, 
                                     target_layer: str) -> List[Tuple[int, int, int, int]]:
        """
        解析 DEF 文件中指定层的走线线段
        
        DEF NETS 段格式：
            - net_name
            + ROUTED layer_name ( x1 y1 ) ( x2 y2 ) ...
            NEW layer_name ( x1 y1 ) ( x2 y2 ) ...
            ;
        
        Returns:
            [(x1, y1, x2, y2), ...] 线段列表（数据库单位DBU）
        """
        segments = []
        
        with open(def_file, 'r') as f:
            content = f.read()
        
        # 提取 NETS 段
        nets_match = re.search(r'NETS\\s+\\d+\\s*;(.*?)END\\s+NETS', 
                               content, re.DOTALL | re.IGNORECASE)
        if not nets_match:
            return segments
        
        nets_text = nets_match.group(1)
        
        # 匹配目标层的 ROUTED/NEW 语句
        # 格式: NEW layer_name ( x y ) ( x y ) ...
        layer_pattern = re.compile(
            rf'(?:ROUTED|NEW)\\s+{re.escape(target_layer)}\\s+(.*?)(?=(?:NEW|\\-|;|END))',
            re.DOTALL | re.IGNORECASE
        )
        
        for match in layer_pattern.finditer(nets_text):
            segment_text = match.group(1)
            # 提取所有坐标点
            coords = re.findall(r'\\(\\s*([\\d.]+)\\s+([\\d.]+)\\s*\\)', segment_text)
            points = [(int(float(x)), int(float(y))) for x, y in coords]
            
            # 将连续点转换为线段
            for i in range(len(points) - 1):
                segments.append((points[i][0], points[i][1], 
                               points[i+1][0], points[i+1][1]))
        
        return segments
    
    @staticmethod
    def _draw_line(img: np.ndarray, x1: int, y1: int, x2: int, y2: int):
        """Bresenham 线段绘制算法"""
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        
        h, w = img.shape
        x, y = x1, y1
        
        while True:
            if 0 <= x < w and 0 <= y < h:
                img[y, x] = 1
            if x == x2 and y == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
    
    def extract_netlist_stats(self, def_file: str) -> Dict:
        """
        提取网表统计特征（结构化数据供VLM使用）
        
        Returns:
            {
                'total_nets': int,
                'total_pins': int,
                'high_fanout_nets': [{'name': str, 'fanout': int, 'hpwl': float}, ...],
                'long_distance_nets': [{'name': str, 'hpwl': float, 'fanout': int}, ...],
                'nets_in_congestion': [str, ...],
                'macro_count': int,
                'std_cell_count': int,
                'component_count': int,
                'io_pin_count': int
            }
        """
        stats = {
            'total_nets': 0,
            'total_pins': 0,
            'high_fanout_nets': [],
            'long_distance_nets': [],
            'nets_in_congestion': [],
            'macro_count': 0,
            'std_cell_count': 0,
            'component_count': 0,
            'io_pin_count': 0
        }
        
        with open(def_file, 'r') as f:
            content = f.read()
        
        # 解析 PINS 段
        pins_match = re.search(r'PINS\\s+(\\d+)', content, re.IGNORECASE)
        if pins_match:
            stats['io_pin_count'] = int(pins_match.group(1))
        
        # 解析 COMPONENTS 段
        comps_match = re.search(
            r'COMPONENTS\\s+(\\d+)\\s*;(.*?)END\\s+COMPONENTS',
            content, re.DOTALL | re.IGNORECASE
        )
        if comps_match:
            stats['component_count'] = int(comps_match.group(1))
            comps_text = comps_match.group(2)
            # 区分宏单元和标准单元（基于LEF MACRO定义更准确）
            # 简化：COMPONENTS中引用MACRO的为标准单元库中的cell
            stats['std_cell_count'] = stats['component_count']  # 简化为全部
        
        # 解析 NETS 段
        nets_match = re.search(
            r'NETS\\s+(\\d+)\\s*;(.*?)END\\s+NETS',
            content, re.DOTALL | re.IGNORECASE
        )
        if not nets_match:
            return stats
        
        stats['total_nets'] = int(nets_match.group(1))
        nets_text = nets_match.group(2)
        
        # 逐网络解析
        # 网络格式：- net_name ( pin1 ) ( pin2 ) + ROUTED ... ;
        net_pattern = re.compile(
            r'-\\s+(\\S+)\\s+\\(\\s*([^)]*)\\s*\\)(.*?)(?=\\-\\s+\\S+\\s+\\(|END)',
            re.DOTALL
        )
        
        for net_match in net_pattern.finditer(nets_text):
            net_name = net_match.group(1)
            pins_text = net_match.group(2)
            route_text = net_match.group(3)
            
            # 解析引脚坐标
            pin_coords = re.findall(r'\\(\\s*([\\d.]+)\\s+([\\d.]+)\\s*\\)', pins_text)
            coords = [(float(x), float(y)) for x, y in pin_coords 
                     if x.replace('.','').isdigit()]
            
            fanout = len(coords)
            stats['total_pins'] += fanout
            
            if coords:
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
                hpwl_um = hpwl / 1000.0  # 假设DBU为nm，转换为um
                
                if fanout > 50:
                    stats['high_fanout_nets'].append({
                        'name': net_name,
                        'fanout': fanout,
                        'hpwl': round(hpwl_um, 2)
                    })
                
                if hpwl_um > 500:
                    stats['long_distance_nets'].append({
                        'name': net_name,
                        'hpwl': round(hpwl_um, 2),
                        'fanout': fanout
                    })
        
        # 限制列表长度，避免VLM上下文过长
        stats['high_fanout_nets'] = sorted(
            stats['high_fanout_nets'], 
            key=lambda x: x['fanout'], reverse=True
        )[:10]
        stats['long_distance_nets'] = sorted(
            stats['long_distance_nets'],
            key=lambda x: x['hpwl'], reverse=True
        )[:10]
        
        return stats
    
    def extract_drc_report(self, def_file: str) -> Dict:
        """
        运行 DRC 检查并解析报告
        
        使用 detailed_route -output_drc 生成DRC报告
        报告格式：每行包含 违规类型 x坐标 y坐标 层名 描述
        
        Returns:
            {
                'total_violations': int,
                'spacing': int,
                'min_width': int,
                'short': int,
                'end_of_line': int,
                'via_spacing': int,
                'corner_spacing': int,
                'adjacent_cut_spacing': int,
                'min_area': int,
                'other': int,
                'violations': [
                    {'type': str, 'x': float, 'y': float, 'layer': str, 'description': str},
                    ...
                ]
            }
        """
        drc_rpt = self.work_dir / f'drc_{self.iteration}.rpt'
        
        tcl_script = f"""
read_lef {self.lef_file}
read_def {def_file}
detailed_route -output_drc {drc_rpt} -verbose 0
"""
        retcode, stdout, stderr = self._run_tcl_script(tcl_script, timeout=1200)
        
        drc_data = {
            'total_violations': 0,
            'spacing': 0,
            'min_width': 0,
            'short': 0,
            'end_of_line': 0,
            'via_spacing': 0,
            'corner_spacing': 0,
            'adjacent_cut_spacing': 0,
            'min_area': 0,
            'other': 0,
            'violations': []
        }
        
        if not drc_rpt.exists():
            return drc_data
        
        # 解析DRC报告
        violation_pattern = re.compile(
            r'(\\w+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+(\\S+)\\s*(.*)'
        )
        
        with open(drc_rpt, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # 尝试匹配标准格式
                match = violation_pattern.match(line)
                if match:
                    vtype = match.group(1)
                    x = float(match.group(2))
                    y = float(match.group(3))
                    layer = match.group(4)
                    desc = match.group(5)
                    
                    drc_data['violations'].append({
                        'type': vtype,
                        'x': x, 'y': y,
                        'layer': layer,
                        'description': desc
                    })
                    
                    # 分类统计
                    vtype_lower = vtype.lower()
                    if 'spacing' in vtype_lower and 'via' not in vtype_lower:
                        drc_data['spacing'] += 1
                    elif 'minwidth' in vtype_lower or 'min_width' in vtype_lower:
                        drc_data['min_width'] += 1
                    elif 'short' in vtype_lower:
                        drc_data['short'] += 1
                    elif 'endofline' in vtype_lower or 'eol' in vtype_lower:
                        drc_data['end_of_line'] += 1
                    elif 'via_spacing' in vtype_lower:
                        drc_data['via_spacing'] += 1
                    elif 'corner' in vtype_lower:
                        drc_data['corner_spacing'] += 1
                    elif 'adjacent_cut' in vtype_lower:
                        drc_data['adjacent_cut_spacing'] += 1
                    elif 'minarea' in vtype_lower or 'min_area' in vtype_lower:
                        drc_data['min_area'] += 1
                    else:
                        drc_data['other'] += 1
                else:
                    # 尝试其他格式（关键词匹配）
                    vtype_lower = line.lower()
                    if 'spacing' in vtype_lower:
                        drc_data['spacing'] += 1
                        drc_data['violations'].append({'type': 'spacing', 'raw': line})
                    elif 'short' in vtype_lower:
                        drc_data['short'] += 1
                        drc_data['violations'].append({'type': 'short', 'raw': line})
        
        drc_data['total_violations'] = len(drc_data['violations'])
        return drc_data
    
    def extract_metrics(self, def_file: str) -> RoutingMetrics:
        """
        提取完整布线指标
        
        组合调用：
        1. DRC检查 -> drc_total
        2. report_wire_length -detailed_route -summary -> wirelength, via_count
        
        Returns:
            RoutingMetrics 完整指标对象
        """
        # 提取DRC
        drc_data = self.extract_drc_report(def_file)
        
        # 提取线长和via数量
        wirelength_rpt = self.work_dir / f'wirelength_{self.iteration}.rpt'
        
        tcl_script = f"""
read_lef {self.lef_file}
read_def {def_file}
report_wire_length -net * -detailed_route -summary -file {wirelength_rpt}
"""
        retcode, stdout, stderr = self._run_tcl_script(tcl_script)
        
        wirelength = 0.0
        via_count = 0
        wirelength_per_layer = {}
        
        if wirelength_rpt.exists():
            with open(wirelength_rpt, 'r') as f:
                content = f.read()
            
            # 解析总线长
            # 格式: Total wire length = 1234567 um.
            total_wl_match = re.search(
                r'Total wire length =\\s*([\\d.]+)\\s*um',
                content, re.IGNORECASE
            )
            if total_wl_match:
                wirelength = float(total_wl_match.group(1))
            
            # 解析via总数
            # 格式: Total number of vias = 89012.
            via_match = re.search(
                r'Total number of vias =\\s*(\\d+)',
                content, re.IGNORECASE
            )
            if via_match:
                via_count = int(via_match.group(1))
            
            # 解析每层线长
            # 格式: Total wire length on LAYER metal2 = 234567 um.
            layer_wl_pattern = re.compile(
                r'Total wire length on LAYER\\s+(\\S+)\\s*=\\s*([\\d.]+)\\s*um',
                re.IGNORECASE
            )
            for match in layer_wl_pattern.finditer(content):
                layer_name = match.group(1)
                layer_wl = float(match.group(2))
                wirelength_per_layer[layer_name] = layer_wl
        
        # 如果report_wire_length失败，尝试从stdout解析
        if wirelength == 0.0 and retcode == 0:
            total_wl_match = re.search(
                r'Total wire length =\\s*([\\d.]+)\\s*um',
                stdout + stderr, re.IGNORECASE
            )
            if total_wl_match:
                wirelength = float(total_wl_match.group(1))
            
            via_match = re.search(
                r'Total number of vias =\\s*(\\d+)',
                stdout + stderr, re.IGNORECASE
            )
            if via_match:
                via_count = int(via_match.group(1))
        
        return RoutingMetrics(
            drc_total=drc_data['total_violations'],
            drc_spacing=drc_data['spacing'],
            drc_min_width=drc_data['min_width'],
            drc_short=drc_data['short'],
            drc_end_of_line=drc_data['end_of_line'],
            drc_via_spacing=drc_data['via_spacing'],
            drc_other=drc_data['other'],
            wirelength=wirelength,
            via_count=via_count,
            wirelength_per_layer=wirelength_per_layer,
            iteration=self.iteration
        )
    
    # ==================== 布线执行 APIs ====================
    
    def run_baseline_flow(self, def_file: str, 
                         guide_file: Optional[str] = None) -> str:
        """
        运行基线布线流程：FastRoute -> TritonRoute
        
        Args:
            def_file: 输入 DEF 文件路径
            guide_file: 全局布线引导文件（可选）
            
        Returns:
            布线后的 DEF 文件路径
        """
        output_def = self.work_dir / f'baseline_{Path(def_file).stem}.def'
        output_dir = self.work_dir / 'baseline_output'
        output_dir.mkdir(exist_ok=True)
        
        env_vars = {
            'LEF_FILE': str(self.lef_file),
            'DEF_FILE': str(def_file),
            'OUTPUT_DEF': str(output_def),
            'OUTPUT_DIR': str(output_dir)
        }
        if guide_file:
            env_vars['GUIDE_FILE'] = str(guide_file)
        
        tcl_script = """
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

if {[info exists ::env(GUIDE_FILE)] && [file exists $::env(GUIDE_FILE)]} {
    read_guides $::env(GUIDE_FILE)
}

set_routing_layers -signal metal2-metal5

global_route \\
    -guide_file $::env(OUTPUT_DIR)/route.guide \\
    -congestion_report_file $::env(OUTPUT_DIR)/congestion.rpt \\
    -congestion_iterations 50 \\
    -verbose

detailed_route \\
    -output_drc $::env(OUTPUT_DIR)/drc.rpt \\
    -output_maze $::env(OUTPUT_DIR)/maze.log \\
    -verbose 1

report_wire_length -net * -detailed_route -summary \\
    -file $::env(OUTPUT_DIR)/wirelength.rpt

write_def $::env(OUTPUT_DEF)
"""
        
        retcode, stdout, stderr = self._run_tcl_script(tcl_script, env_vars, timeout=3600)
        
        if retcode != 0:
            print(f"[ERROR] 基线布线失败: {stderr[:500]}")
            # 尝试返回原始文件
            return def_file
        
        print(f"[OK] 基线布线完成: {output_def}")
        self.current_def = str(output_def)
        return str(output_def)
    
    def rip_up_nets(self, def_file: str, net_list: List[str]) -> str:
        """
        撕线：移除指定网络的走线（保留网络定义）
        
        操作原理：在DEF文件NETS段中，删除目标网络的 + ROUTED ... 语句，
        保留网络声明和引脚连接。
        
        Args:
            def_file: 输入 DEF 文件
            net_list: 要撕线的网络名列表
            
        Returns:
            撕线后的 DEF 文件路径
        """
        output_def = self.work_dir / f'ripped_iter{self.iteration}.def'
        
        with open(def_file, 'r') as f:
            lines = f.readlines()
        
        # 将 net_list 转换为集合以便快速查找
        target_nets = set(net_list)
        
        new_lines = []
        in_target_net = False
        skip_route_section = False
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 检测网络开始：- net_name
            if stripped.startswith('-'):
                # 提取网络名
                net_match = re.match(r'-\\s+(\\S+)', stripped)
                if net_match:
                    net_name = net_match.group(1)
                    in_target_net = net_name in target_nets
                    skip_route_section = False
                    new_lines.append(line)
                    i += 1
                    continue
            
            # 如果在目标网络内，检测 + ROUTED/NEW 开始
            if in_target_net and (stripped.startswith('+ ROUTED') or 
                                  stripped.startswith('+ FIXED') or
                                  stripped.startswith('NEW')):
                skip_route_section = True
                i += 1
                continue
            
            # 如果在跳过段内，检测结束（下一网络的 '-' 或 ';'）
            if skip_route_section:
                if stripped.startswith('-') or stripped == ';':
                    skip_route_section = False
                    # 如果是 ';'，保留它（网络结束标记）
                    if stripped == ';':
                        new_lines.append(line)
                    else:
                        # 是下一网络的开始，回退处理
                        continue
                i += 1
                continue
            
            new_lines.append(line)
            i += 1
        
        with open(output_def, 'w') as f:
            f.writelines(new_lines)
        
        print(f"[OK] 撕线完成: {len(net_list)} nets -> {output_def}")
        return str(output_def)
    
    def run_incremental_route(self, def_file: str, 
                              output_name: Optional[str] = None,
                              timeout: int = 1800) -> str:
        """
        增量详细布线
        
        对已被撕线的网络重新布线。OpenROAD的detailed_route
        会自动检测未布线的网络并进行布线。
        
        Args:
            def_file: 输入 DEF（含未布线网络）
            output_name: 输出DEF文件名（可选）
            timeout: 超时时间
            
        Returns:
            布线后的 DEF 文件路径
        """
        if output_name is None:
            output_name = f'routed_iter{self.iteration}.def'
        
        output_def = self.work_dir / output_name
        output_dir = self.work_dir / f'route_output_{self.iteration}'
        output_dir.mkdir(exist_ok=True)
        
        env_vars = {
            'LEF_FILE': str(self.lef_file),
            'DEF_FILE': str(def_file),
            'OUTPUT_DEF': str(output_def),
            'OUTPUT_DIR': str(output_dir)
        }
        
        tcl_script = """
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

detailed_route \\
    -output_drc $::env(OUTPUT_DIR)/drc.rpt \\
    -verbose 1

report_wire_length -net * -detailed_route -summary \\
    -file $::env(OUTPUT_DIR)/wirelength.rpt

write_def $::env(OUTPUT_DEF)
"""
        
        retcode, stdout, stderr = self._run_tcl_script(tcl_script, env_vars, timeout=timeout)
        
        if retcode != 0:
            print(f"[WARNING] 增量布线失败: {stderr[:300]}")
            return def_file  # 返回原文件作为fallback
        
        self.iteration += 1
        print(f"[OK] 增量布线完成: {output_def}")
        return str(output_def)
    
    def rip_up_and_reroute(self, def_file: str, net_list: List[str],
                          strategy: Dict) -> Tuple[str, RoutingMetrics]:
        """
        组合操作：撕线 + 重布线
        
        Args:
            def_file: 当前 DEF 文件
            net_list: 目标网络列表
            strategy: 策略参数（可包含 layer_preference, avoid_regions 等）
            
        Returns:
            (新DEF路径, 新指标)
        """
        print(f"[ACTION] Rip-up & Reroute: {len(net_list)} nets")
        
        # 1. 撕线
        ripped_def = self.rip_up_nets(def_file, net_list)
        
        # 2. 增量重布线
        new_def = self.run_incremental_route(ripped_def)
        
        # 3. 评估
        metrics = self.extract_metrics(new_def)
        
        return new_def, metrics
    
    def set_routing_blockage(self, def_file: str, 
                            bbox: Tuple[int, int, int, int],
                            layers: List[str],
                            output_name: Optional[str] = None) -> str:
        """
        设置临时布线障碍区
        
        在DEF文件中添加BLOCKAGES段，阻止路由器在指定区域布线。
        
        Args:
            def_file: 输入 DEF
            bbox: (x1, y1, x2, y2) 障碍区坐标（数据库单位）
            layers: 受影响层列表
            output_name: 输出文件名
            
        Returns:
            修改后的 DEF 路径
        """
        if output_name is None:
            output_name = f'blocked_iter{self.iteration}.def'
        
        output_def = self.work_dir / output_name
        
        with open(def_file, 'r') as f:
            content = f.read()
        
        x1, y1, x2, y2 = bbox
        
        # 构建 BLOCKAGES 段
        blockage_entries = []
        for layer in layers:
            blockage_entries.append(
                f"    - LAYER {layer}"
                f"      RECT ( {x1} {y1} ) ( {x2} {y2} ) ;"
            )
        
        blockages_section = (
            f"BLOCKAGES {len(blockage_entries)} ;\n"
            + "\n".join(blockage_entries)
            + "\nEND BLOCKAGES\n"
        )
        
        # 在END DESIGN之前插入BLOCKAGES
        content = content.replace("END DESIGN", blockages_section + "END DESIGN")
        
        with open(output_def, 'w') as f:
            f.write(content)
        
        return str(output_def)


# ==================== 测试入口 ====================

if __name__ == '__main__':
    # 快速测试
    toolkit = RoutingToolkit(
        openroad_exe='openroad',
        work_dir='/tmp/routing_test',
        lef_file='data/ispd2018/ispd18_test1/ispd18_test1.lef'
    )
    print("RoutingToolkit initialized successfully")
```

### 5.2 视觉状态渲染器 (`src/visual_renderer.py`)

本模块将布线的多维状态渲染为 VLM 可理解的 PNG 图像。采用 **6通道复合布局**，将拥塞图、各层布线、DRC标记合成为一张图。

**设计原则**：
1. 单图多子图布局：避免多次API调用传输多张图
2. 颜色语义一致：红色=拥塞/问题，绿色=正常走线，蓝色=DRC
3. 关键信息标注：在图上叠加数值标注
4. 分辨率可调：根据VLM模型支持的输入尺寸调整

```python
"""
src/visual_renderer.py

视觉状态渲染器
将路由状态渲染为多通道PNG图像供VLM理解

设计：2x3子图布局，单图包含全部关键信息
      拥塞热力图 + 各层布线 + DRC标记 + 热点放大 + 统计叠加

依赖：numpy, matplotlib, PIL, typing, pathlib
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')  # 无头模式，不依赖X11
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import io


class VisualRenderer:
    """
    路由状态视觉渲染器
    
    将多维路由状态渲染为VLM可理解的复合图像：
    - 全局拥塞热力图
    - 各金属层走线分布
    - DRC违规标记
    - 热点区域放大
    - 走线+拥塞叠加图
    - 网表统计文本叠加
    
    Attributes:
        output_dir: 图像输出目录
        resolution: 基础分辨率（影响细节程度）
    """
    
    def __init__(self, output_dir: str, resolution: int = 512):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.resolution = resolution
        
        # 自定义颜色映射：拥塞图
        self.congestion_cmap = LinearSegmentedColormap.from_list(
            'congestion', ['darkblue', 'blue', 'green', 'yellow', 'orange', 'red', 'darkred']
        )
        
        # DRC标记颜色映射
        self.drc_color_map = {
            'spacing': 'yellow',
            'min_width': 'orange',
            'short': 'red',
            'end_of_line': 'magenta',
            'via_spacing': 'cyan',
            'other': 'white'
        }
    
    def render_state(self, 
                     congestion_map: np.ndarray,
                     routing_layers: Dict[str, np.ndarray],
                     drc_markers: List[Tuple[float, float, str]],
                     netlist_stats: Dict,
                     metrics: Optional[Dict] = None,
                     iteration: int = 0,
                     focus_region: Optional[Tuple[int, int, int, int]] = None) -> str:
        """
        渲染完整视觉状态
        
        布局（2行 x 3列）：
        [0,0] 全局拥塞图    [0,1] M3层走线     [0,2] M4层走线
        [1,0] DRC违规分布   [1,1] 叠加图       [1,2] 热点放大
        + 底部统计文本条
        
        Args:
            congestion_map: (H, W) 拥塞热力图
            routing_layers: {layer_name: (H, W) 二值图}
            drc_markers: [(x, y, vtype), ...] DRC标记坐标和类型
            netlist_stats: 网表统计字典
            metrics: 当前指标字典
            iteration: 迭代次数
            focus_region: (x1, y1, x2, y2) 手动指定热点区域
            
        Returns:
            图像文件路径
        """
        fig = plt.figure(figsize=(18, 12), dpi=150)
        fig.patch.set_facecolor('#1a1a2e')  # 深色背景
        
        # 子图1: 全局拥塞热力图
        ax1 = plt.subplot(2, 3, 1)
        self._render_congestion(ax1, congestion_map)
        ax1.set_title(f'Global Congestion (Iter {iteration})', 
                     fontsize=11, color='white', fontweight='bold')
        
        # 子图2: 主要布线层1 (M3/horizontal)
        ax2 = plt.subplot(2, 3, 2)
        layer_m3 = routing_layers.get('M3') or routing_layers.get('metal3')
        if layer_m3 is not None:
            self._render_routing_layer(ax2, layer_m3, 'M3 (Horizontal)')
        else:
            ax2.text(0.5, 0.5, 'M3 Not Available', ha='center', va='center',
                    transform=ax2.transAxes, color='white', fontsize=12)
            ax2.set_facecolor('black')
        ax2.set_title('M3 Routing Layer', fontsize=11, color='white', fontweight='bold')
        
        # 子图3: 主要布线层2 (M4/vertical)
        ax3 = plt.subplot(2, 3, 3)
        layer_m4 = routing_layers.get('M4') or routing_layers.get('metal4')
        if layer_m4 is not None:
            self._render_routing_layer(ax3, layer_m4, 'M4 (Vertical)')
        else:
            ax3.text(0.5, 0.5, 'M4 Not Available', ha='center', va='center',
                    transform=ax3.transAxes, color='white', fontsize=12)
            ax3.set_facecolor('black')
        ax3.set_title('M4 Routing Layer', fontsize=11, color='white', fontweight='bold')
        
        # 子图4: DRC违规分布
        ax4 = plt.subplot(2, 3, 4)
        self._render_drc_markers(ax4, drc_markers, congestion_map.shape)
        ax4.set_title(f'DRC Violations (Total: {len(drc_markers)})', 
                     fontsize=11, color='white', fontweight='bold')
        
        # 子图5: 拥塞 + 走线叠加图
        ax5 = plt.subplot(2, 3, 5)
        self._render_composite(ax5, congestion_map, routing_layers)
        ax5.set_title('Congestion + Routing Overlay', 
                     fontsize=11, color='white', fontweight='bold')
        
        # 子图6: 热点区域放大
        ax6 = plt.subplot(2, 3, 6)
        if focus_region:
            self._render_hotspot(ax6, congestion_map, focus_region)
        else:
            auto_hotspot = self._detect_hotspot(congestion_map)
            if auto_hotspot:
                self._render_hotspot(ax6, congestion_map, auto_hotspot)
                ax6.set_title(f'Auto Hotspot {auto_hotspot}', 
                            fontsize=11, color='white', fontweight='bold')
            else:
                ax6.text(0.5, 0.5, 'No Significant Hotspot', 
                        ha='center', va='center', transform=ax6.transAxes,
                        color='white', fontsize=12)
                ax6.set_facecolor('black')
                ax6.set_title('Hotspot Analysis', fontsize=11, color='white')
        
        # 底部统计文本条
        stats_text = self._format_stats(netlist_stats, metrics)
        fig.text(0.5, 0.02, stats_text, ha='center', fontsize=9,
                color='white',
                bbox=dict(boxstyle='round,pad=0.5', 
                         facecolor='#16213e', edgecolor='#e94560', alpha=0.9))
        
        # 总标题
        if metrics:
            title = (f'Routing State - Iteration {iteration} | '
                    f'DRC: {metrics.get("drc_total", "N/A")} | '
                    f'WL: {metrics.get("wirelength", 0)/1e6:.3f}mm | '
                    f'Vias: {metrics.get("via_count", "N/A")}')
        else:
            title = f'Routing State - Iteration {iteration}'
        fig.suptitle(title, fontsize=14, color='#e94560', fontweight='bold', y=0.98)
        
        plt.tight_layout(rect=[0, 0.06, 1, 0.95])
        
        output_path = self.output_dir / f'state_iter_{iteration:03d}.png'
        plt.savefig(output_path, dpi=150, bbox_inches='tight',
                   facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        
        return str(output_path)
    
    def render_diff(self, prev_state_path: str, curr_state_path: str,
                   iteration: int) -> str:
        """
        渲染状态差异图（当前 - 之前）
        
        帮助VLM聚焦于变化区域，使用差分图像放大变化
        
        Args:
            prev_state_path: 上一迭代状态图路径
            curr_state_path: 当前迭代状态图路径
            iteration: 迭代次数
            
        Returns:
            差异图像路径
        """
        prev_img = np.array(Image.open(prev_state_path).convert('RGB'))
        curr_img = np.array(Image.open(curr_state_path).convert('RGB'))
        
        # 确保尺寸一致
        min_h = min(prev_img.shape[0], curr_img.shape[0])
        min_w = min(prev_img.shape[1], curr_img.shape[1])
        prev_img = prev_img[:min_h, :min_w]
        curr_img = curr_img[:min_h, :min_w]
        
        # 计算差分（放大变化）
        diff = np.abs(curr_img.astype(float) - prev_img.astype(float))
        diff = np.clip(diff * 3, 0, 255).astype(np.uint8)  # 3倍放大变化
        
        fig, axes = plt.subplots(1, 3, figsize=(15, 5), dpi=150)
        fig.patch.set_facecolor('#1a1a2e')
        
        for ax, img, title in zip(axes, 
                                  [prev_img, curr_img, diff],
                                  ['Previous State', 'Current State', 
                                   'Difference (3x Amplified)']):
            ax.imshow(img)
            ax.set_title(title, fontsize=12, color='white', fontweight='bold')
            ax.axis('off')
            ax.set_facecolor('#1a1a2e')
        
        plt.tight_layout()
        output_path = self.output_dir / f'diff_iter_{iteration:03d}.png'
        plt.savefig(output_path, dpi=150, bbox_inches='tight',
                   facecolor=fig.get_facecolor())
        plt.close()
        
        return str(output_path)
    
    def render_policy_action(self, state_path: str, action: Dict,
                            iteration: int) -> str:
        """
        渲染策略动作可视化图
        
        在状态图上叠加显示Agent将要执行的动作区域和目标网络
        
        Args:
            state_path: 当前状态图路径
            action: 动作字典（含target_nets, avoid_regions等）
            iteration: 迭代次数
            
        Returns:
            动作可视化图路径
        """
        img = Image.open(state_path).convert('RGBA')
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        width, height = img.size
        
        # 绘制avoid_regions（半透明红色遮罩）
        avoid_regions = action.get('avoid_regions', [])
        for region in avoid_regions:
            bbox = region.get('bbox')
            if bbox and len(bbox) == 4:
                x1 = int(bbox[0] * width)
                y1 = int(bbox[1] * height)
                x2 = int(bbox[2] * width)
                y2 = int(bbox[3] * height)
                draw.rectangle([x1, y1, x2, y2], fill=(255, 0, 0, 80),
                             outline=(255, 0, 0, 200), width=2)
                draw.text((x1+5, y1+5), f"BLOCK: {region.get('reason', '')}",
                         fill=(255, 255, 255, 255))
        
        # 合并叠加层
        img = Image.alpha_composite(img, overlay)
        
        output_path = self.output_dir / f'action_iter_{iteration:03d}.png'
        img.save(output_path)
        
        return str(output_path)
    
    # ==================== 内部渲染方法 ====================
    
    def _render_congestion(self, ax, congestion_map: np.ndarray):
        """渲染拥塞热力图"""
        ax.set_facecolor('black')
        
        if congestion_map.max() > 0:
            im = ax.imshow(congestion_map, cmap=self.congestion_cmap,
                          interpolation='nearest', vmin=0, 
                          vmax=max(congestion_map.max(), 1.0))
            cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            cbar.ax.tick_params(colors='white')
            cbar.set_label('Overflow', color='white')
        else:
            ax.imshow(congestion_map, cmap='gray', interpolation='nearest')
        
        ax.set_xlabel('GCell X', color='white')
        ax.set_ylabel('GCell Y', color='white')
        ax.tick_params(colors='white')
        
        # 标注高拥塞区域（前5个）
        if congestion_map.max() > 0.5:
            flat_indices = np.argsort(congestion_map.ravel())[::-1][:5]
            coords = np.unravel_index(flat_indices, congestion_map.shape)
            for i, (y, x) in enumerate(zip(coords[0], coords[1])):
                val = congestion_map[y, x]
                ax.annotate(f'{val:.1f}', xy=(x, y),
                          fontsize=7, color='white', ha='center',
                          bbox=dict(boxstyle='round,pad=0.2', 
                                   facecolor='red', alpha=0.7))
    
    def _render_routing_layer(self, ax, routing_map: np.ndarray, 
                              layer_name: str):
        """渲染布线层"""
        ax.set_facecolor('black')
        ax.imshow(routing_map, cmap='Greens', interpolation='nearest',
                 vmin=0, vmax=1)
        ax.set_xlabel('X', color='white')
        ax.set_ylabel('Y', color='white')
        ax.tick_params(colors='white')
        
        # 计算走线密度
        if routing_map.size > 0:
            density = routing_map.sum() / routing_map.size * 100
            ax.text(0.02, 0.98, f'Density: {density:.1f}%',
                   transform=ax.transAxes, color='yellow', fontsize=9,
                   verticalalignment='top',
                   bbox=dict(boxstyle='round', facecolor='black', alpha=0.7))
    
    def _render_drc_markers(self, ax, drc_markers: List[Tuple],
                           map_shape: Tuple[int, int]):
        """渲染DRC违规标记"""
        ax.set_facecolor('black')
        h, w = map_shape
        ax.set_xlim(0, w)
        ax.set_ylim(0, h)
        
        if not drc_markers:
            ax.text(0.5, 0.5, 'No DRC Violations', ha='center', va='center',
                   transform=ax.transAxes, color='green', fontsize=14,
                   fontweight='bold')
            return
        
        # 按类型分组绘制
        type_groups = {}
        for marker in drc_markers:
            if len(marker) >= 3:
                x, y, vtype = marker[0], marker[1], marker[2]
            else:
                x, y = marker[0], marker[1]
                vtype = 'unknown'
            type_groups.setdefault(vtype, []).append((x, y))
        
        for vtype, coords in type_groups.items():
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            color = self.drc_color_map.get(vtype, 'white')
            ax.scatter(xs, ys, c=color, s=20, alpha=0.8, 
                      label=f'{vtype}: {len(coords)}', marker='x')
        
        ax.legend(loc='upper right', facecolor='black', edgecolor='white',
                 labelcolor='white', fontsize=8)
        ax.set_xlabel('X', color='white')
        ax.set_ylabel('Y', color='white')
        ax.tick_params(colors='white')
    
    def _render_composite(self, ax, congestion_map: np.ndarray,
                         routing_layers: Dict[str, np.ndarray]):
        """渲染叠加图：拥塞 + 走线"""
        ax.set_facecolor('black')
        h, w = congestion_map.shape
        
        composite = np.zeros((h, w, 3), dtype=np.float32)
        
        # R通道: 拥塞（归一化到0-1）
        congestion_norm = np.clip(congestion_map / 
                                  max(congestion_map.max(), 0.1), 0, 1)
        composite[:, :, 0] = congestion_norm
        
        # G通道: 走线并集（所有层）
        routing_union = np.zeros((h, w), dtype=np.float32)
        for layer_name, layer_map in routing_layers.items():
            if layer_map is not None and layer_map.shape == (h, w):
                routing_union = np.maximum(routing_union, layer_map)
        composite[:, :, 1] = routing_union
        
        # B通道: 低拥塞区域指示
        composite[:, :, 2] = np.clip(1 - congestion_norm, 0, 1) * 0.3
        
        ax.imshow(composite, interpolation='nearest')
        ax.set_xlabel('X', color='white')
        ax.set_ylabel('Y', color='white')
        ax.tick_params(colors='white')
        
        # 添加图例说明
        legend_text = 'R=Congestion | G=Routing | B=LowCongestion'
        ax.text(0.5, -0.12, legend_text, transform=ax.transAxes,
               ha='center', color='white', fontsize=8)
    
    def _render_hotspot(self, ax, congestion_map: np.ndarray,
                       region: Tuple[int, int, int, int]):
        """渲染局部热点区域放大"""
        x1, y1, x2, y2 = region
        h, w = congestion_map.shape
        
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        
        if x2 <= x1 or y2 <= y1:
            ax.text(0.5, 0.5, 'Invalid Region', ha='center', va='center',
                   transform=ax.transAxes, color='white')
            return
        
        hotspot = congestion_map[y1:y2, x1:x2]
        
        ax.set_facecolor('black')
        im = ax.imshow(hotspot, cmap=self.congestion_cmap,
                      interpolation='nearest', vmin=0,
                      vmax=max(hotspot.max(), 0.5))
        cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.tick_params(colors='white')
        
        ax.set_title(f'Region ({x1},{y1})-({x2},{y2})', color='white')
        ax.set_xlabel('X', color='white')
        ax.set_ylabel('Y', color='white')
        ax.tick_params(colors='white')
        
        # 标记最大值
        if hotspot.max() > 0:
            max_y, max_x = np.unravel_index(np.argmax(hotspot), hotspot.shape)
            ax.plot(max_x, max_y, 'w*', markersize=15)
            ax.annotate(f'Max: {hotspot.max():.1f}',
                      xy=(max_x, max_y), xytext=(max_x+5, max_y-5),
                      color='white', fontsize=9,
                      arrowprops=dict(arrowstyle='->', color='white'))
    
    def _detect_hotspot(self, congestion_map: np.ndarray,
                       window_size: int = 20) -> Optional[Tuple[int, int, int, int]]:
        """
        自动检测最大拥塞热点区域
        
        使用滑动窗口找到平均拥塞最大的区域
        
        Args:
            congestion_map: 拥塞热力图
            window_size: 检测窗口大小
            
        Returns:
            (x1, y1, x2, y2) 热点区域坐标，或 None
        """
        if congestion_map.max() < 0.3:
            return None
        
        h, w = congestion_map.shape
        
        # 找到最大值的坐标
        max_y, max_x = np.unravel_index(np.argmax(congestion_map), congestion_map.shape)
        
        # 以最大值为中心定义窗口
        half = window_size // 2
        x1 = max(0, max_x - half)
        y1 = max(0, max_y - half)
        x2 = min(w, max_x + half)
        y2 = min(h, max_y + half)
        
        return (x1, y1, x2, y2)
    
    def _format_stats(self, stats: Dict, metrics: Optional[Dict]) -> str:
        """格式化网表统计为文本"""
        lines = []
        
        # 网表基础信息
        lines.append(f"Nets: {stats.get('total_nets', 'N/A')} | "
                    f"Pins: {stats.get('total_pins', 'N/A')} | "
                    f"Components: {stats.get('component_count', 'N/A')}")
        
        # 关键网络
        hf_count = len(stats.get('high_fanout_nets', []))
        ld_count = len(stats.get('long_distance_nets', []))
        lines.append(f"HighFanout(>50): {hf_count} | "
                    f"LongNets(>500um): {ld_count} | "
                    f"IO_Pins: {stats.get('io_pin_count', 'N/A')}")
        
        # 指标（如果可用）
        if metrics:
            lines.append(f"DRC: {metrics.get('drc_total', 'N/A')} "
                        f"(S:{metrics.get('drc_spacing',0)} "
                        f"W:{metrics.get('drc_min_width',0)} "
                        f"Sh:{metrics.get('drc_short',0)}) | "
                        f"WL: {metrics.get('wirelength',0)/1e6:.4f}mm | "
                        f"Vias: {metrics.get('via_count', 'N/A')}")
        
        return ' | '.join(lines)


if __name__ == '__main__':
    renderer = VisualRenderer('/tmp/visual_test')
    print("VisualRenderer initialized successfully")
```

### 5.3 VLM 策略生成器 (`src/vlm_policy.py`)

本模块负责调用多模态大模型 API，将视觉状态 + 文本状态转换为结构化的路由策略 JSON。

**支持的VLM模型**：
- **Gemini 2.5 Flash**（推荐）：原生多模态，1M上下文窗口，成本$0.15/1M tokens
- **Claude 3.5 Sonnet**（备选）：通过base64编码支持图像输入
- **GPT-4o**（备选）：支持图像输入，成本较高

**核心功能**：
1. 多模态输入：图像(PNG) + 结构化文本
2. JSON模式输出：强制输出为可解析的策略JSON
3. 重试机制：处理API限流和格式错误
4. 上下文窗口管理：自动截断过长的网表信息

```python
"""
src/vlm_policy.py

VLM策略生成器
使用多模态大模型分析路由状态，输出优化策略JSON

支持模型：
- Gemini 2.5 Flash (推荐): google-generativeai
- Claude 3.5 Sonnet: anthropic
- GPT-4o: openai

依赖：os, json, base64, typing, pathlib, dotenv
     google-generativeai, anthropic, openai
"""

import os
import json
import base64
import time
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 尝试导入各API客户端
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class VLMPolicyGenerator:
    """
    VLM 路由策略生成器
    
    通过多模态大模型分析当前路由状态（图像+文本），
    输出结构化的优化策略JSON。
    
    Attributes:
        model: 使用的模型名称
        client_type: API客户端类型 ('gemini' | 'anthropic' | 'openai')
        max_retries: 最大重试次数
        retry_delay: 重试间隔（秒）
    """
    
    # ==================== Prompt 模板 ====================
    
    SYSTEM_PROMPT = """You are an expert digital IC physical design engineer specializing in detailed routing optimization. Your task is to analyze the provided routing state images and metrics, then output an optimization strategy.

## Design Rules Reference
- Metal layer directions (typical): M1/M3/M5 horizontal, M2/M4/M6 vertical
- Spacing rules depend on process node; trust the DRC engine
- Optimization priority: DRC=0 > minimize wirelength > minimize via count

## Available Actions
You can ONLY output these action types:
1. "rip_up_reroute": Rip up routing of specified nets and reroute them
2. "incremental_route": Run incremental detailed route on unconnected nets
3. "layer_assign": Assign preferred metal layers to net groups (executed via reroute)
4. "set_blockage": Add temporary routing blockage in congested regions
5. "terminate": Terminate optimization (when DRC=0 or cannot improve)

## Output Format
Output MUST be valid JSON with this exact schema:
{
  "routing_policy": {
    "iteration": <integer>,
    "strategy_type": <string: brief strategy name>,
    "analysis": <string: 2-3 sentence analysis of current state>,
    "priority_actions": [
      {
        "action": <string: one of the 5 types above>,
        "target_nets": [<string>: net names to act on],
        "reason": <string: why this action>,
        "layer_preference": [<string>]: preferred layers (e.g., ["M3", "M4"]),
        "direction_constraint": <string: "horizontal" | "vertical" | "none">,
        "avoid_regions": [
          {"bbox": [x1, y1, x2, y2], "reason": <string>}
        ]
      }
    ],
    "termination_check": <boolean>,
    "next_state_focus": <string: what to look for in next iteration>
  }
}

## Rules
- Target only nets that exist in the netlist statistics
- Layer preferences must be valid metal layers (M1-M6 typical)
- Avoid regions must be within chip boundaries [0, 0, 1, 1] normalized
- Provide clear reasoning for each action
- If DRC > 0, focus on fixing violations first
- If DRC == 0, suggest wirelength/via optimization or terminate
"""

    USER_PROMPT_TEMPLATE = """## Current State - Iteration {iteration}

### Visual Inputs (6-panel routing visualization):
1. [Top-Left] Global Congestion Heatmap: Red regions indicate congestion hotspots
2. [Top-Center] M3 Routing Layer: Green= routed tracks (horizontal layer)
3. [Top-Right] M4 Routing Layer: Green= routed tracks (vertical layer)
4. [Bottom-Left] DRC Violation Map: Colored X markers show violation locations by type
5. [Bottom-Center] Overlay: R=congestion, G=routing density, B=low congestion
6. [Bottom-Right] Hotspot Zoom: Close-up of highest congestion region

### Netlist Statistics:
- Total nets: {total_nets}
- Total pins: {total_pins}
- Components: {component_count} (StdCells: {std_cell_count})
- IO pins: {io_pin_count}
- High fanout nets (>50 pins): {high_fanout_count}
{high_fanout_list}
- Long distance nets (HPWL>500um): {long_dist_count}
{long_dist_list}

### Current Metrics:
- DRC Violations: {drc_total}
  - Spacing: {drc_spacing}
  - MinWidth: {drc_min_width}
  - Short: {drc_short}
  - EndOfLine: {drc_end_of_line}
  - ViaSpacing: {drc_via_spacing}
- Wirelength: {wirelength:.2f} um ({wirelength_mm:.6f} mm)
- Via Count: {via_count}

### Previous Action: {last_action}
### Previous Result: DRC {drc_delta:+d}, Wirelength {wl_delta:+.2f}%

Please analyze the routing state and output the optimization strategy in the required JSON format.
"""

    def __init__(self, 
                 model: str = 'gemini-2.5-flash',
                 client_type: Optional[str] = None,
                 max_retries: int = 3,
                 retry_delay: float = 2.0,
                 temperature: float = 0.1):
        """
        初始化VLM策略生成器
        
        Args:
            model: 模型名称
                Gemini: 'gemini-2.5-flash', 'gemini-2.5-pro'
                Claude: 'claude-3-5-sonnet-20241022'
                OpenAI: 'gpt-4o', 'gpt-4o-mini'
            client_type: 强制指定客户端类型 ('gemini'|'anthropic'|'openai')
            max_retries: API调用最大重试次数
            retry_delay: 重试间隔（秒）
            temperature: 生成温度（0-1，越低越确定性）
        """
        self.model_name = model
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.temperature = temperature
        self.client = None
        
        # 自动推断客户端类型
        if client_type is None:
            if 'gemini' in model.lower():
                client_type = 'gemini'
            elif 'claude' in model.lower():
                client_type = 'anthropic'
            elif 'gpt' in model.lower():
                client_type = 'openai'
            else:
                client_type = 'gemini'  # 默认
        
        self.client_type = client_type
        self._init_client()
    
    def _init_client(self):
        """初始化API客户端"""
        if self.client_type == 'gemini':
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                raise ValueError('GEMINI_API_KEY not found in environment')
            if not GEMINI_AVAILABLE:
                raise ImportError('google-generativeai not installed. '
                                'Run: pip install google-generativeai')
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel(self.model_name)
            
        elif self.client_type == 'anthropic':
            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError('ANTHROPIC_API_KEY not found in environment')
            if not ANTHROPIC_AVAILABLE:
                raise ImportError('anthropic not installed. '
                                'Run: pip install anthropic')
            self.client = anthropic.Anthropic(api_key=api_key)
            
        elif self.client_type == 'openai':
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise ValueError('OPENAI_API_KEY not found in environment')
            if not OPENAI_AVAILABLE:
                raise ImportError('openai not installed. '
                                'Run: pip install openai')
            self.client = openai.OpenAI(api_key=api_key)
        
        else:
            raise ValueError(f"Unknown client type: {self.client_type}")
        
        print(f"[OK] VLM client initialized: {self.client_type} / {self.model_name}")
    
    def generate_policy(self, 
                       image_paths: List[str],
                       netlist_stats: Dict,
                       metrics: Dict,
                       last_action: str = 'None',
                       last_metrics: Optional[Dict] = None,
                       iteration: int = 0) -> Dict:
        """
        生成路由优化策略
        
        Args:
            image_paths: 视觉状态图像路径列表（支持多图）
            netlist_stats: 网表统计字典
            metrics: 当前指标字典
            last_action: 上一回合的操作描述
            last_metrics: 上一回合的指标（用于计算增量）
            iteration: 当前迭代次数
            
        Returns:
            策略JSON字典，结构见 SYSTEM_PROMPT 中的schema定义
        """
        # 计算指标变化
        drc_delta = 0
        wl_delta = 0.0
        if last_metrics:
            drc_delta = metrics.get('drc_total', 0) - last_metrics.get('drc_total', 0)
            prev_wl = last_metrics.get('wirelength', 1)
            if prev_wl > 0:
                wl_delta = ((metrics.get('wirelength', 0) - prev_wl) / prev_wl) * 100
        
        # 格式化网络列表（限制长度）
        hf_nets = netlist_stats.get('high_fanout_nets', [])[:5]
        hf_list = '\\n'.join([f"  - {n['name']}: fanout={n['fanout']}, "
                              f"HPWL={n['hpwl']}um" for n in hf_nets])
        
        ld_nets = netlist_stats.get('long_distance_nets', [])[:5]
        ld_list = '\\n'.join([f"  - {n['name']}: HPWL={n['hpwl']}um, "
                              f"fanout={n['fanout']}" for n in ld_nets])
        
        # 构建用户提示
        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            iteration=iteration,
            total_nets=netlist_stats.get('total_nets', 0),
            total_pins=netlist_stats.get('total_pins', 0),
            component_count=netlist_stats.get('component_count', 0),
            std_cell_count=netlist_stats.get('std_cell_count', 0),
            io_pin_count=netlist_stats.get('io_pin_count', 0),
            high_fanout_count=len(netlist_stats.get('high_fanout_nets', [])),
            high_fanout_list=hf_list if hf_list else '  None',
            long_dist_count=len(netlist_stats.get('long_distance_nets', [])),
            long_dist_list=ld_list if ld_list else '  None',
            drc_total=metrics.get('drc_total', 0),
            drc_spacing=metrics.get('drc_spacing', 0),
            drc_min_width=metrics.get('drc_min_width', 0),
            drc_short=metrics.get('drc_short', 0),
            drc_end_of_line=metrics.get('drc_end_of_line', 0),
            drc_via_spacing=metrics.get('drc_via_spacing', 0),
            wirelength=metrics.get('wirelength', 0),
            wirelength_mm=metrics.get('wirelength', 0) / 1e6,
            via_count=metrics.get('via_count', 0),
            last_action=last_action,
            drc_delta=drc_delta,
            wl_delta=wl_delta
        )
        
        # 调用VLM API（带重试）
        for attempt in range(self.max_retries):
            try:
                if self.client_type == 'gemini':
                    response = self._call_gemini(image_paths, user_prompt)
                elif self.client_type == 'anthropic':
                    response = self._call_anthropic(image_paths, user_prompt)
                elif self.client_type == 'openai':
                    response = self._call_openai(image_paths, user_prompt)
                
                # 解析JSON
                policy = self._extract_json(response)
                
                # 验证schema
                self._validate_policy_schema(policy)
                
                return policy
                
            except Exception as e:
                print(f"[WARNING] VLM call attempt {attempt+1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    print("[ERROR] All VLM retries exhausted, returning fallback")
                    return self._fallback_policy(metrics, iteration)
        
        return self._fallback_policy(metrics, iteration)
    
    def _call_gemini(self, image_paths: List[str], user_prompt: str) -> str:
        """调用 Gemini API"""
        content = [self.SYSTEM_PROMPT, user_prompt]
        
        # 添加图像
        for img_path in image_paths:
            if Path(img_path).exists():
                with open(img_path, 'rb') as f:
                    img_data = f.read()
                content.append({
                    'mime_type': 'image/png',
                    'data': img_data
                })
        
        response = self.client.generate_content(
            content,
            generation_config=genai.types.GenerationConfig(
                temperature=self.temperature,
                response_mime_type='application/json'
            )
        )
        return response.text
    
    def _call_anthropic(self, image_paths: List[str], user_prompt: str) -> str:
        """调用 Claude API（图像需base64编码）"""
        messages = []
        
        # 构建多模态消息内容
        message_content = []
        
        # 添加图像（base64编码）
        for img_path in image_paths:
            if Path(img_path).exists():
                with open(img_path, 'rb') as f:
                    img_data = base64.b64encode(f.read()).decode('utf-8')
                message_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_data
                    }
                })
        
        # 添加文本
        message_content.append({
            "type": "text",
            "text": self.SYSTEM_PROMPT + "\\n\\n" + user_prompt
        })
        
        messages.append({
            "role": "user",
            "content": message_content
        })
        
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            temperature=self.temperature,
            messages=messages
        )
        
        return response.content[0].text
    
    def _call_openai(self, image_paths: List[str], user_prompt: str) -> str:
        """调用 OpenAI API（图像需base64编码或URL）"""
        messages = []
        
        # 系统提示
        messages.append({
            "role": "system",
            "content": self.SYSTEM_PROMPT
        })
        
        # 用户消息（多模态）
        content = [{"type": "text", "text": user_prompt}]
        
        for img_path in image_paths:
            if Path(img_path).exists():
                with open(img_path, 'rb') as f:
                    img_data = base64.b64encode(f.read()).decode('utf-8')
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_data}",
                        "detail": "high"
                    }
                })
        
        messages.append({
            "role": "user",
            "content": content
        })
        
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=self.temperature,
            max_tokens=4096,
            response_format={"type": "json_object"}
        )
        
        return response.choices[0].message.content
    
    def _extract_json(self, text: str) -> Dict:
        """
        从VLM输出中提取JSON
        
        尝试多种格式：直接JSON、markdown代码块、花括号匹配
        """
        # 尝试1: 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 尝试2: 提取 ```json ... ``` 代码块
        json_match = re.search(r'```(?:json)?\\s*(.*?)\\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 尝试3: 提取最外层 { ... }
        json_match = re.search(r'\\{.*\\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        raise ValueError(f"Cannot extract valid JSON from VLM output: {text[:500]}")
    
    def _validate_policy_schema(self, policy: Dict):
        """验证策略JSON结构"""
        if 'routing_policy' not in policy:
            raise ValueError("Missing 'routing_policy' key")
        
        rp = policy['routing_policy']
        required_keys = ['iteration', 'strategy_type', 'priority_actions', 
                        'termination_check']
        for key in required_keys:
            if key not in rp:
                raise ValueError(f"Missing key in routing_policy: {key}")
    
    def _fallback_policy(self, metrics: Dict, iteration: int) -> Dict:
        """
        生成默认回退策略
        
        当VLM调用失败时使用：
        - 如果DRC>0: 尝试撕线重布DRC最多的网络
        - 如果DRC==0: 终止
        """
        drc_total = metrics.get('drc_total', 999)
        
        if drc_total > 0:
            return {
                "routing_policy": {
                    "iteration": iteration,
                    "strategy_type": "fallback_fix_drc",
                    "analysis": "VLM API failed. Using fallback strategy to fix DRC violations.",
                    "priority_actions": [
                        {
                            "action": "rip_up_reroute",
                            "target_nets": [],
                            "reason": "Fallback: rip up all nets with DRC violations and reroute",
                            "layer_preference": [],
                            "direction_constraint": "none",
                            "avoid_regions": []
                        }
                    ],
                    "termination_check": False,
                    "next_state_focus": "Check if DRC count decreased"
                }
            }
        else:
            return {
                "routing_policy": {
                    "iteration": iteration,
                    "strategy_type": "fallback_terminate",
                    "analysis": "DRC is clean. Terminating optimization.",
                    "priority_actions": [],
                    "termination_check": True,
                    "next_state_focus": "Optimization complete"
                }
            }


if __name__ == '__main__':
    vlm = VLMPolicyGenerator()
    print("VLMPolicyGenerator initialized successfully")
```

### 5.4 Agent 主控制器 (`src/agent_controller.py`)

本模块实现Agent的完整控制循环：状态提取 -> VLM决策 -> 工具执行 -> 评估 -> 迭代。

**核心循环逻辑**：
1. 运行基线布线
2. 提取多模态状态
3. 查询VLM获取策略
4. 解析并执行动作
5. 评估结果
6. 保存最佳解
7. 检查终止条件

```python
"""
src/agent_controller.py

Agent 主控制器
编排整个优化循环：状态提取 -> VLM策略 -> 工具执行 -> 评估 -> 迭代

核心设计模式：ReAct (Reasoning + Acting)
- VLM负责Reasoning（分析状态、制定策略）
- Agent负责Acting（调用工具、执行操作）

依赖：json, shutil, typing, pathlib, dataclasses, time
     routing_toolkit, visual_renderer, vlm_policy
"""

import json
import shutil
import time
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import asdict

from routing_toolkit import RoutingToolkit, RoutingMetrics, RoutingState
from visual_renderer import VisualRenderer
from vlm_policy import VLMPolicyGenerator


class RoutingAgent:
    """
    路由优化 Agent
    
    核心循环：Extract -> VLM Policy -> Execute -> Evaluate -> Iterate
    
    Attributes:
        toolkit: OpenROAD工具包装器
        renderer: 视觉状态渲染器
        vlm: VLM策略生成器
        work_dir: 工作目录
        max_iterations: 最大迭代次数
        patience: 早停耐心值（连续多少次无改善则停止）
    """
    
    def __init__(self,
                 toolkit: RoutingToolkit,
                 renderer: VisualRenderer,
                 vlm: VLMPolicyGenerator,
                 work_dir: str,
                 max_iterations: int = 20,
                 patience: int = 5):
        self.toolkit = toolkit
        self.renderer = renderer
        self.vlm = vlm
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.max_iterations = max_iterations
        self.patience = patience
        
        # 历史缓冲区
        self.history: List[Tuple[str, RoutingMetrics, str]] = []  # (image_path, metrics, action)
        self.current_def: Optional[str] = None
        self.current_metrics: Optional[RoutingMetrics] = None
        self.best_def: Optional[str] = None
        self.best_metrics: Optional[RoutingMetrics] = None
        self.no_improve_count = 0
    
    def optimize(self, initial_def: str, 
                 guide_file: Optional[str] = None) -> Tuple[str, RoutingMetrics]:
        """
        主优化循环
        
        Args:
            initial_def: 初始DEF文件（已完成布局，未布线或已基线布线）
            guide_file: 全局布线引导文件（可选）
            
        Returns:
            (最佳DEF路径, 最佳指标)
        """
        print("=" * 70)
        print("VLM Routing Agent - Optimization Starting")
        print("=" * 70)
        
        # Step 1: 基线布线
        print("\\n[Phase 1] Running baseline routing...")
        start_time = time.time()
        
        self.current_def = self.toolkit.run_baseline_flow(initial_def, guide_file)
        self.current_metrics = self.toolkit.extract_metrics(self.current_def)
        
        baseline_time = time.time() - start_time
        print(f"[OK] Baseline complete in {baseline_time:.1f}s")
        print(f"    DRC: {self.current_metrics.drc_total} | "
              f"WL: {self.current_metrics.wirelength:.2f} um | "
              f"Vias: {self.current_metrics.via_count}")
        
        # 初始化最佳解
        self.best_def = self.current_def
        self.best_metrics = self.current_metrics
        
        # 保存基线检查点
        self._save_checkpoint(-1, self.current_def, self.current_metrics)
        
        # 检查是否已DRC-clean
        if self.current_metrics.drc_total == 0:
            print("\\n[COMPLETE] Design is already DRC-clean!")
            return self.current_def, self.current_metrics
        
        # Step 2+: 迭代优化
        for iteration in range(self.max_iterations):
            print(f"\\n{'='*70}")
            print(f"[Iteration {iteration + 1}/{self.max_iterations}]")
            print(f"Current Best: DRC={self.best_metrics.drc_total}, "
                  f"WL={self.best_metrics.wirelength:.2f}")
            print(f"{'='*70}")
            
            iter_start = time.time()
            
            # ---- Step 2a: 提取多模态状态 ----
            print("  [Step 1/5] Extracting state...")
            state = self._extract_state(self.current_def)
            
            # ---- Step 2b: 渲染视觉状态 ----
            print("  [Step 2/5] Rendering visual state...")
            image_paths = self._render_visual_state(state, iteration)
            
            # ---- Step 2c: VLM生成策略 ----
            print("  [Step 3/5] Querying VLM for policy...")
            last_action = self.history[-1][2] if self.history else 'baseline'
            last_metrics = self.history[-1][1] if self.history else None
            
            policy = self.vlm.generate_policy(
                image_paths=image_paths,
                netlist_stats=state.netlist_stats,
                metrics=asdict(self.current_metrics),
                last_action=last_action,
                last_metrics=asdict(last_metrics) if last_metrics else None,
                iteration=iteration
            )
            
            policy_data = policy.get('routing_policy', {})
            strategy_type = policy_data.get('strategy_type', 'unknown')
            analysis = policy_data.get('analysis', '')
            print(f"    VLM Analysis: {analysis}")
            print(f"    Strategy: {strategy_type}")
            
            # 检查终止条件
            if policy_data.get('termination_check', False):
                print("  [VLM] Termination suggested.")
                break
            
            # ---- Step 2d: 执行策略 ----
            print("  [Step 4/5] Executing policy...")
            new_def, new_metrics = self._execute_policy(
                self.current_def, policy, iteration
            )
            
            # ---- Step 2e: 评估与更新 ----
            print("  [Step 5/5] Evaluating result...")
            drc_change = new_metrics.drc_total - self.current_metrics.drc_total
            wl_change = new_metrics.wirelength - self.current_metrics.wirelength
            
            print(f"    Result: DRC={new_metrics.drc_total} ({drc_change:+d}), "
                  f"WL={new_metrics.wirelength:.2f} ({wl_change:+.2f})")
            
            # 更新最佳解（DRC优先，其次线长）
            is_better = self._is_better_metrics(new_metrics, self.best_metrics)
            if is_better:
                self.best_def = new_def
                self.best_metrics = new_metrics
                self.no_improve_count = 0
                print("    >>> New best solution found! <<<")
            else:
                self.no_improve_count += 1
            
            # 保存历史
            action_str = strategy_type
            self.history.append((image_paths[0], new_metrics, action_str))
            
            # 更新当前状态
            self.current_def = new_def
            self.current_metrics = new_metrics
            
            # 保存检查点
            self._save_checkpoint(iteration, new_def, new_metrics)
            
            # 检查终止条件
            if new_metrics.drc_total == 0:
                print("\\n[COMPLETE] DRC is clean! Optimization complete.")
                break
            
            if self.no_improve_count >= self.patience:
                print(f"\\n[STOP] No improvement for {self.patience} iterations. "
                      f"Early stopping.")
                break
            
            iter_time = time.time() - iter_start
            print(f"  Iteration time: {iter_time:.1f}s")
        
        # 输出最终报告
        print(f"\\n{'='*70}")
        print("Optimization Complete!")
        print(f"{'='*70}")
        print(f"Best result: DRC={self.best_metrics.drc_total}, "
              f"WL={self.best_metrics.wirelength:.2f}, "
              f"Via={self.best_metrics.via_count}")
        print(f"Total iterations: {iteration + 1}")
        print(f"Best DEF: {self.best_def}")
        
        return self.best_def, self.best_metrics
    
    def _extract_state(self, def_file: str) -> RoutingState:
        """提取完整状态（拥塞图、网表统计、DRC报告）"""
        # 拥塞图
        congestion = self.toolkit.extract_congestion_map(def_file)
        
        # 各层布线状态
        routing_layers = self.toolkit.extract_routing_layers(
            def_file, 
            layers=['M2', 'M3', 'M4', 'M5']
        )
        
        # 网表统计
        netlist_stats = self.toolkit.extract_netlist_stats(def_file)
        
        # DRC报告
        drc_report = self.toolkit.extract_drc_report(def_file)
        
        # DRC标记坐标
        drc_markers = []
        for v in drc_report.get('violations', []):
            if isinstance(v, dict) and 'x' in v and 'y' in v:
                vtype = v.get('type', 'unknown')
                x = v['x']
                y = v['y']
                drc_markers.append((x, y, vtype))
        
        return RoutingState(
            def_file=def_file,
            congestion_map=congestion,
            routing_layers=routing_layers,
            metrics=self.current_metrics,
            netlist_stats=netlist_stats,
            drc_report=drc_report,
            drc_markers=drc_markers,
            iteration=len(self.history)
        )
    
    def _render_visual_state(self, state: RoutingState, 
                            iteration: int) -> List[str]:
        """渲染视觉状态，返回图像路径列表"""
        metrics_dict = asdict(state.metrics) if state.metrics else None
        
        # 主状态图
        main_image = self.renderer.render_state(
            congestion_map=state.congestion_map,
            routing_layers=state.routing_layers,
            drc_markers=state.drc_markers,
            netlist_stats=state.netlist_stats,
            metrics=metrics_dict,
            iteration=iteration
        )
        
        images = [main_image]
        
        # 如果历史存在，渲染差异图
        if self.history:
            prev_image = self.history[-1][0]
            if Path(prev_image).exists() and Path(main_image).exists():
                diff_image = self.renderer.render_diff(
                    prev_image, main_image, iteration
                )
                images.append(diff_image)
        
        return images
    
    def _execute_policy(self, def_file: str, policy: Dict,
                       iteration: int) -> Tuple[str, RoutingMetrics]:
        """解析并执行VLM策略"""
        policy_data = policy.get('routing_policy', {})
        actions = policy_data.get('priority_actions', [])
        
        current_def = def_file
        
        if not actions:
            print("    No actions specified, returning current state")
            metrics = self.toolkit.extract_metrics(current_def)
            return current_def, metrics
        
        for action in actions:
            action_type = action.get('action', '')
            
            if action_type == 'rip_up_reroute':
                target_nets = action.get('target_nets', [])
                if target_nets:
                    print(f"    Action: Rip-up & reroute {len(target_nets)} nets")
                    current_def, metrics = self.toolkit.rip_up_and_reroute(
                        current_def, target_nets, action
                    )
                else:
                    # 如果没有指定网络，返回当前状态
                    print("    Action: rip_up_reroute with no target nets, skipping")
                    metrics = self.toolkit.extract_metrics(current_def)
                    return current_def, metrics
            
            elif action_type == 'incremental_route':
                print("    Action: Incremental route")
                current_def = self.toolkit.run_incremental_route(current_def)
            
            elif action_type == 'layer_assign':
                # 层分配通过撕线+指定层偏好重布实现
                target_nets = action.get('target_nets', [])
                if target_nets:
                    print(f"    Action: Layer assign for {len(target_nets)} nets")
                    layers = action.get('layer_preference', [])
                    current_def, metrics = self.toolkit.rip_up_and_reroute(
                        current_def, target_nets, action
                    )
            
            elif action_type == 'set_blockage':
                avoid_regions = action.get('avoid_regions', [])
                if avoid_regions:
                    print(f"    Action: Set {len(avoid_regions)} routing blockages")
                    for region in avoid_regions:
                        bbox = region.get('bbox')
                        if bbox and len(bbox) == 4:
                            layers = action.get('layer_preference', ['M2', 'M3', 'M4'])
                            current_def = self.toolkit.set_routing_blockage(
                                current_def, tuple(bbox), layers
                            )
            
            elif action_type == 'terminate':
                print("    Action: Terminate")
                break
            
            else:
                print(f"    Unknown action type: {action_type}, skipping")
        
        # 最终评估
        final_metrics = self.toolkit.extract_metrics(current_def)
        return current_def, final_metrics
    
    @staticmethod
    def _is_better_metrics(new: RoutingMetrics, 
                          best: RoutingMetrics) -> bool:
        """
        比较两组指标，判断新的是否更优
        
        优先级：DRC数量 > 线长 > Via数量
        """
        if new.drc_total < best.drc_total:
            return True
        if new.drc_total > best.drc_total:
            return False
        # DRC相同，比较线长
        if new.wirelength < best.wirelength:
            return True
        if new.wirelength > best.wirelength:
            return False
        # 线长相同，比较via
        return new.via_count < best.via_count
    
    def _save_checkpoint(self, iteration: int, def_file: str, 
                        metrics: RoutingMetrics):
        """保存检查点"""
        checkpoint_dir = self.work_dir / 'checkpoints'
        checkpoint_dir.mkdir(exist_ok=True)
        
        # 复制DEF
        shutil.copy(def_file, checkpoint_dir / f'iter_{iteration:03d}.def')
        
        # 保存指标
        with open(checkpoint_dir / f'iter_{iteration:03d}.json', 'w') as f:
            json.dump(asdict(metrics), f, indent=2)


if __name__ == '__main__':
    print("AgentController module loaded. Use with RoutingToolkit, VisualRenderer, VLMPolicyGenerator.")
```

### 5.5 ISPD 评估器 (`src/ispd_evaluator.py`)

本模块实现 ISPD 2019 官方评分公式，用于评估Agent优化效果并与基线对比。

**ISPD 2019 评分公式**：
```
raw_score = Σ(违规权重 * 违规数量) + Σ(指标权重 * 指标值)
scaled_score = raw_score * (1 + nondeterministic_penalty + runtime_factor)
```

```python
"""
src/ispd_evaluator.py

ISPD 评估器
按照ISPD 2018/2019竞赛官方评分公式计算路由质量分数

ISPD 2019评分公式：
  raw_score = Σ(metric_weight * metric_value) + Σ(violation_weight * violation_count)
  scaled_score = raw_score * (1 + nondeterministic_penalty + runtime_factor)

依赖：json, pathlib, typing, dataclasses
"""

import json
import time
from pathlib import Path
from typing import Dict, Tuple, Optional, List
from dataclasses import asdict

from routing_toolkit import RoutingMetrics


class ISPDEvaluator:
    """
    ISPD 2018/2019 竞赛评分器
    
    按照官方评分权重计算路由方案得分。
    分数越低越好。
    
    Attributes:
        output_dir: 评估报告输出目录
    """
    
    # ========== ISPD 2019 官方评分权重 ==========
    
    # DRC违规权重（每个违规的惩罚）
    VIOLATION_WEIGHTS = {
        'short_count': 500,           # 短路违规数量
        'short_area': 500,            # 短路面积 (单位: M2 pitch^2)
        'end_of_line': 500,           # 线端间距违规
        'wire_spacing': 500,          # 线间距违规
        'via_spacing': 500,           # Via间距违规
        'corner_spacing': 500,        # 角间距违规
        'adjacent_cut_spacing': 500,  # 相邻切割间距违规
        'min_area': 500,              # 最小面积违规
    }
    
    # 布线质量指标权重
    METRIC_WEIGHTS = {
        'single_cut_via': 4,      # 单切割Via数量
        'multi_cut_via': 2,       # 多切割Via数量
        'wirelength': 0.5,        # 总线长 (单位: um -> DBU)
        'out_of_guide_wl': 1,     # 超出引导线长
        'out_of_guide_via': 1,    # 超出引导Via数
        'off_track_wl': 0.5,      # 偏离轨道线长
        'off_track_via': 1,       # 偏离轨道Via数
        'wrong_way_wl': 1,        # 错误方向线长
    }
    
    # 简化评分（基于可获取的指标）
    # 由于完整ISPD指标需要专门的evaluator二进制，
    # 这里使用基于DRC+Wirelength+Via的近似评分
    SIMPLIFIED_WEIGHTS = {
        'drc_total': 1000,        # 每个DRC违规
        'wirelength': 0.01,       # 每um线长
        'via_count': 0.1,         # 每个Via
    }
    
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def compute_score(self, metrics: RoutingMetrics,
                     scoring_mode: str = 'simplified') -> float:
        """
        计算ISPD风格评分
        
        Args:
            metrics: 路由指标
            scoring_mode: 'simplified' | 'ispd2019' | 'ispd2018'
            
        Returns:
            float: 分数（越低越好）
        """
        if scoring_mode == 'simplified':
            return self._compute_simplified_score(metrics)
        elif scoring_mode == 'ispd2019':
            return self._compute_ispd2019_score(metrics)
        elif scoring_mode == 'ispd2018':
            return self._compute_ispd2018_score(metrics)
        else:
            raise ValueError(f"Unknown scoring mode: {scoring_mode}")
    
    def _compute_simplified_score(self, metrics: RoutingMetrics) -> float:
        """简化评分（基于DRC+线长+Via）"""
        score = (
            metrics.drc_total * self.SIMPLIFIED_WEIGHTS['drc_total'] +
            metrics.wirelength * self.SIMPLIFIED_WEIGHTS['wirelength'] +
            metrics.via_count * self.SIMPLIFIED_WEIGHTS['via_count']
        )
        return score
    
    def _compute_ispd2019_score(self, metrics: RoutingMetrics) -> float:
        """
        ISPD 2019 官方评分（完整版）
        
        注意：完整评分需要专门的evaluator工具解析详细DRC分类。
        这里使用简化版本，以DRC总数作为代理。
        """
        # 使用DRC分类（如果可用）
        drc_score = (
            metrics.drc_short * self.VIOLATION_WEIGHTS['short_count'] +
            metrics.drc_spacing * self.VIOLATION_WEIGHTS['wire_spacing'] +
            metrics.drc_min_width * 500 +  # min_width归类到other
            metrics.drc_end_of_line * self.VIOLATION_WEIGHTS['end_of_line'] +
            metrics.drc_via_spacing * self.VIOLATION_WEIGHTS['via_spacing']
        )
        
        # 布线质量
        quality_score = (
            metrics.wirelength * self.METRIC_WEIGHTS['wirelength'] +
            metrics.via_count * self.METRIC_WEIGHTS['single_cut_via']
        )
        
        return drc_score + quality_score
    
    def _compute_ispd2018_score(self, metrics: RoutingMetrics) -> float:
        """ISPD 2018 评分（更简化）"""
        # ISPD 2018: DRC权重更高，质量指标相对简单
        return (
            metrics.drc_total * 500 +    # ISPD 2018 DRC权重
            metrics.wirelength * 0.5 +
            metrics.via_count * 2
        )
    
    def evaluate_and_compare(self,
                            baseline_metrics: RoutingMetrics,
                            optimized_metrics: RoutingMetrics,
                            benchmark_name: str,
                            scoring_mode: str = 'simplified') -> Dict:
        """
        评估并对比基线与优化结果
        
        Args:
            baseline_metrics: 基线指标
            optimized_metrics: 优化后指标
            benchmark_name: 测试用例名
            scoring_mode: 评分模式
            
        Returns:
            评估报告字典
        """
        baseline_score = self.compute_score(baseline_metrics, scoring_mode)
        optimized_score = self.compute_score(optimized_metrics, scoring_mode)
        
        if baseline_score > 0:
            improvement = (baseline_score - optimized_score) / baseline_score * 100
        else:
            improvement = 0.0
        
        result = {
            'benchmark': benchmark_name,
            'scoring_mode': scoring_mode,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'baseline': {
                'drc_total': baseline_metrics.drc_total,
                'drc_breakdown': baseline_metrics.drc_breakdown,
                'wirelength_um': baseline_metrics.wirelength,
                'via_count': baseline_metrics.via_count,
                'score': baseline_score
            },
            'optimized': {
                'drc_total': optimized_metrics.drc_total,
                'drc_breakdown': optimized_metrics.drc_breakdown,
                'wirelength_um': optimized_metrics.wirelength,
                'via_count': optimized_metrics.via_count,
                'score': optimized_score
            },
            'improvement': {
                'score_pct': round(improvement, 2),
                'drc_delta': baseline_metrics.drc_total - optimized_metrics.drc_total,
                'drc_delta_pct': self._safe_pct(
                    baseline_metrics.drc_total - optimized_metrics.drc_total,
                    baseline_metrics.drc_total
                ),
                'wl_delta_um': baseline_metrics.wirelength - optimized_metrics.wirelength,
                'wl_delta_pct': self._safe_pct(
                    baseline_metrics.wirelength - optimized_metrics.wirelength,
                    baseline_metrics.wirelength
                ),
                'via_delta': baseline_metrics.via_count - optimized_metrics.via_count,
                'via_delta_pct': self._safe_pct(
                    baseline_metrics.via_count - optimized_metrics.via_count,
                    baseline_metrics.via_count
                )
            },
            'conclusion': self._generate_conclusion(
                baseline_metrics, optimized_metrics, improvement
            )
        }
        
        # 保存报告
        report_path = self.output_dir / f'{benchmark_name}_report.json'
        with open(report_path, 'w') as f:
            json.dump(result, f, indent=2)
        
        # 打印摘要
        self._print_summary(result)
        
        return result
    
    @staticmethod
    def _safe_pct(delta: float, base: float) -> float:
        """安全计算百分比变化"""
        if base > 0:
            return round(delta / base * 100, 2)
        return 0.0
    
    @staticmethod
    def _generate_conclusion(baseline: RoutingMetrics,
                           optimized: RoutingMetrics,
                           improvement: float) -> str:
        """生成评估结论"""
        if optimized.drc_total == 0 and baseline.drc_total > 0:
            return "SUCCESS: DRC completely eliminated!"
        elif optimized.drc_total < baseline.drc_total:
            return f"IMPROVED: DRC reduced by {baseline.drc_total - optimized.drc_total}"
        elif improvement > 0:
            return f"IMPROVED: Score improved by {improvement:.1f}%"
        elif improvement == 0:
            return "NO CHANGE: Results are equivalent"
        else:
            return f"REGRESSED: Score degraded by {-improvement:.1f}%"
    
    def _print_summary(self, result: Dict):
        """打印评估摘要"""
        b = result['baseline']
        o = result['optimized']
        imp = result['improvement']
        
        print(f"\\n{'='*60}")
        print(f"ISPD Evaluation Report: {result['benchmark']}")
        print(f"Mode: {result['scoring_mode']}")
        print(f"{'='*60}")
        print(f"Baseline:   DRC={b['drc_total']:4d} WL={b['wirelength_um']:12.2f} "
              f"Via={b['via_count']:6d} Score={b['score']:12.2f}")
        print(f"Optimized:  DRC={o['drc_total']:4d} WL={o['wirelength_um']:12.2f} "
              f"Via={o['via_count']:6d} Score={o['score']:12.2f}")
        print(f"{'='*60}")
        print(f"Score Improvement: {imp['score_pct']:+.2f}%")
        print(f"DRC Delta: {imp['drc_delta']:+d} ({imp['drc_delta_pct']:+.1f}%)")
        print(f"WL Delta:  {imp['wl_delta_um']:+.2f} um ({imp['wl_delta_pct']:+.1f}%)")
        print(f"Via Delta: {imp['via_delta']:+d} ({imp['via_delta_pct']:+.1f}%)")
        print(f"Conclusion: {result['conclusion']}")
        print(f"{'='*60}\\n")


if __name__ == '__main__':
    evaluator = ISPDEvaluator('/tmp/eval_test')
    print("ISPDEvaluator initialized successfully")
```

### 5.6 工具函数 (`src/utils.py`)

```python
"""
src/utils.py

通用工具函数
提供日志、配置加载、文件操作等辅助功能
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional


def setup_logging(log_dir: str = './logs', level: int = logging.INFO) -> logging.Logger:
    """配置日志系统"""
    log_dir = Path(log_dir)
    log_dir.mkdir(exist_ok=True)
    
    logger = logging.getLogger('vlm_routing')
    logger.setLevel(level)
    
    if not logger.handlers:
        # 文件处理器
        fh = logging.FileHandler(log_dir / 'agent.log')
        fh.setLevel(level)
        
        # 控制台处理器
        ch = logging.StreamHandler()
        ch.setLevel(level)
        
        # 格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        logger.addHandler(fh)
        logger.addHandler(ch)
    
    return logger


def load_config(config_path: str = 'config/agent_config.yaml') -> Dict[str, Any]:
    """加载Agent配置文件"""
    config_path = Path(config_path)
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    return {}


def save_json(data: Any, path: str):
    """保存JSON文件"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def load_json(path: str) -> Any:
    """加载JSON文件"""
    with open(path, 'r') as f:
        return json.load(f)


def ensure_dir(path: str):
    """确保目录存在"""
    Path(path).mkdir(parents=True, exist_ok=True)
```

---

## 6. 执行脚本

### 6.1 主执行脚本 (`scripts/run_agent.py`)

```python
#!/usr/bin/env python3
"""
scripts/run_agent.py

主执行脚本：在ISPD基准测试上运行VLM路由Agent

用法：
    python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018
    python scripts/run_agent.py --benchmark ispd19_test1 --data-dir ./data/ispd2019 --max-iter 20
"""

import sys
import argparse
from pathlib import Path

# 将src添加到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from routing_toolkit import RoutingToolkit
from visual_renderer import VisualRenderer
from vlm_policy import VLMPolicyGenerator
from agent_controller import RoutingAgent
from ispd_evaluator import ISPDEvaluator


def main():
    parser = argparse.ArgumentParser(
        description='VLM Routing Agent - ISPD Benchmark Optimization',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # ISPD 2018 test1
  python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018
  
  # ISPD 2019 test1 with custom iterations
  python scripts/run_agent.py --benchmark ispd19_test1 --data-dir ./data/ispd2019 --max-iter 20
  
  # Use Claude instead of Gemini
  python scripts/run_agent.py --benchmark ispd18_test1 --vlm-model claude-3-5-sonnet-20241022
        """
    )
    
    # 必需参数
    parser.add_argument('--benchmark', required=True,
                       help='ISPD基准名称 (e.g., ispd18_test1, ispd19_test1)')
    parser.add_argument('--data-dir', default='./data/ispd2018',
                       help='ISPD数据根目录')
    
    # 可选参数
    parser.add_argument('--work-dir', default='./outputs/agent_runs',
                       help='工作输出目录')
    parser.add_argument('--max-iter', type=int, default=15,
                       help='最大优化迭代次数')
    parser.add_argument('--patience', type=int, default=5,
                       help='早停耐心值')
    parser.add_argument('--openroad', default='openroad',
                       help='OpenROAD可执行文件路径')
    parser.add_argument('--vlm-model', default='gemini-2.5-flash',
                       help='VLM模型名称')
    parser.add_argument('--vlm-client', default=None,
                       help='VLM客户端类型 (gemini|anthropic|openai)')
    parser.add_argument('--resolution', type=int, default=512,
                       help='可视化分辨率')
    parser.add_argument('--scoring-mode', default='simplified',
                       choices=['simplified', 'ispd2019', 'ispd2018'],
                       help='ISPD评分模式')
    
    args = parser.parse_args()
    
    # 路径配置
    data_dir = Path(args.data_dir) / args.benchmark
    lef_file = data_dir / f'{args.benchmark}.lef'
    def_file = data_dir / f'{args.benchmark}.def'
    guide_file = data_dir / f'{args.benchmark}.guide'
    
    # 验证输入文件
    if not def_file.exists():
        print(f"[ERROR] DEF file not found: {def_file}")
        print(f"  Expected structure: {args.data_dir}/{args.benchmark}/{args.benchmark}.def")
        sys.exit(1)
    
    if not lef_file.exists():
        print(f"[WARNING] LEF file not found: {lef_file}")
    
    work_dir = Path(args.work_dir) / args.benchmark
    work_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"{'='*60}")
    print(f"Benchmark: {args.benchmark}")
    print(f"  LEF:  {lef_file}")
    print(f"  DEF:  {def_file}")
    print(f"  Guide: {guide_file if guide_file.exists() else 'N/A'}")
    print(f"  Work:  {work_dir}")
    print(f"  VLM:   {args.vlm_model}")
    print(f"{'='*60}\\n")
    
    # 初始化组件
    print("[Init] Initializing components...")
    
    toolkit = RoutingToolkit(
        openroad_exe=args.openroad,
        work_dir=str(work_dir / 'toolkit'),
        lef_file=str(lef_file)
    )
    
    renderer = VisualRenderer(
        output_dir=str(work_dir / 'visual_states'),
        resolution=args.resolution
    )
    
    vlm = VLMPolicyGenerator(
        model=args.vlm_model,
        client_type=args.vlm_client
    )
    
    agent = RoutingAgent(
        toolkit=toolkit,
        renderer=renderer,
        vlm=vlm,
        work_dir=str(work_dir),
        max_iterations=args.max_iter,
        patience=args.patience
    )
    
    # 运行优化
    print("\\n[Start] Running optimization...\\n")
    best_def, best_metrics = agent.optimize(
        initial_def=str(def_file),
        guide_file=str(guide_file) if guide_file.exists() else None
    )
    
    # 提取基线指标（来自检查点-1）
    baseline_metrics = None
    baseline_json = work_dir / 'checkpoints' / 'iter_-001.json'
    if baseline_json.exists():
        import json as _json
        with open(baseline_json) as f:
            baseline_data = _json.load(f)
        baseline_metrics = RoutingMetrics(**baseline_data)
    
    # 评估对比
    if baseline_metrics:
        print("\\n[Eval] Running ISPD evaluation...")
        evaluator = ISPDEvaluator(str(work_dir / 'evaluation'))
        evaluator.evaluate_and_compare(
            baseline_metrics=baseline_metrics,
            optimized_metrics=best_metrics,
            benchmark_name=args.benchmark,
            scoring_mode=args.scoring_mode
        )
    
    # 最终输出
    print(f"\\n{'='*60}")
    print(f"Optimization Complete!")
    print(f"{'='*60}")
    print(f"Best DEF: {best_def}")
    print(f"Final DRC: {best_metrics.drc_total}")
    print(f"Final WL:  {best_metrics.wirelength:.2f} um")
    print(f"Final Via: {best_metrics.via_count}")
    print(f"Output directory: {work_dir}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
```

### 6.2 基线运行脚本 (`scripts/run_baseline.py`)

```python
#!/usr/bin/env python3
"""
scripts/run_baseline.py

运行OpenROAD默认基线布线流程，生成对比基准

用法：
    python scripts/run_baseline.py --benchmark ispd18_test1
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from routing_toolkit import RoutingToolkit
from ispd_evaluator import ISPDEvaluator


def main():
    parser = argparse.ArgumentParser(description='Run OpenROAD baseline routing')
    parser.add_argument('--benchmark', required=True, help='ISPD benchmark name')
    parser.add_argument('--data-dir', default='./data/ispd2018')
    parser.add_argument('--output-dir', default='./outputs/baseline')
    parser.add_argument('--openroad', default='openroad')
    parser.add_argument('--scoring-mode', default='simplified')
    args = parser.parse_args()
    
    data_dir = Path(args.data_dir) / args.benchmark
    lef_file = data_dir / f'{args.benchmark}.lef'
    def_file = data_dir / f'{args.benchmark}.def'
    guide_file = data_dir / f'{args.benchmark}.guide'
    
    output_dir = Path(args.output_dir) / args.benchmark
    output_dir.mkdir(parents=True, exist_ok=True)
    
    toolkit = RoutingToolkit(
        openroad_exe=args.openroad,
        work_dir=str(output_dir),
        lef_file=str(lef_file)
    )
    
    print(f"Running baseline for {args.benchmark}...")
    routed_def = toolkit.run_baseline_flow(
        str(def_file),
        str(guide_file) if guide_file.exists() else None
    )
    
    metrics = toolkit.extract_metrics(routed_def)
    
    print(f"\\nBaseline Results:")
    print(f"  DRC: {metrics.drc_total}")
    print(f"  WL:  {metrics.wirelength:.2f} um")
    print(f"  Via: {metrics.via_count}")
    print(f"  DEF: {routed_def}")
    
    # 自评估
    evaluator = ISPDEvaluator(str(output_dir / 'evaluation'))
    score = evaluator.compute_score(metrics, args.scoring_mode)
    print(f"  Score: {score:.2f}")


if __name__ == '__main__':
    main()
```

### 6.3 批量执行脚本

```bash
#!/bin/bash
# scripts/batch_run.sh
# 批量运行Agent优化多个benchmark

DATA_DIR="${1:-./data/ispd2018}"
WORK_DIR="${2:-./outputs/agent_runs}"
MAX_ITER="${3:-15}"

# ISPD 2018 benchmarks
BENCHMARKS="ispd18_test1 ispd18_test2 ispd18_test3 ispd18_test4 ispd18_test5"

for bench in $BENCHMARKS; do
    echo "========================================="
    echo "Processing: $bench"
    echo "========================================="
    
    python scripts/run_agent.py \
        --benchmark "$bench" \
        --data-dir "$DATA_DIR" \
        --work-dir "$WORK_DIR" \
        --max-iter "$MAX_ITER" \
        --scoring-mode simplified
    
    echo "Completed: $bench"
    echo ""
done

echo "All benchmarks completed!"
echo "Results in: $WORK_DIR"
```

### 6.4 配置文件 (`config/agent_config.yaml`)

```yaml
# VLM Routing Agent 配置文件

# Agent 行为配置
agent:
  max_iterations: 20           # 最大迭代次数
  patience: 5                  # 早停耐心值
  checkpoint_interval: 1       # 检查点保存间隔

# VLM 配置
vlm:
  model: "gemini-2.5-flash"   # 主选模型
  fallback_model: "gemini-2.5-flash"  # 回退模型
  temperature: 0.1             # 生成温度
  max_retries: 3               # API重试次数
  retry_delay: 2.0             # 重试间隔（秒）
  timeout: 120                 # API调用超时（秒）

# OpenROAD 配置
openroad:
  exe: "openroad"              # 可执行文件路径
  threads: 8                   # 线程数
  timeout:                     # 各阶段超时（秒）
    baseline: 3600             # 基线布线
    incremental: 1800          # 增量布线
    drc_check: 1200            # DRC检查
    congestion: 300            # 拥塞提取

# 可视化配置
visualization:
  resolution: 512              # 图像分辨率
  dpi: 150                     # 图像DPI
  layers:                      # 要渲染的层
    - "M2"
    - "M3"
    - "M4"
    - "M5"

# 评估配置
evaluation:
  scoring_mode: "simplified"   # simplified | ispd2019 | ispd2018
  save_reports: true           # 保存JSON报告

# 日志配置
logging:
  level: "INFO"                # DEBUG | INFO | WARNING | ERROR
  log_dir: "./logs"            # 日志目录
```

---

## 7. Agent 执行流程

### 7.1 完整执行流程图

```
[Start]
  |
  v
[Load Config & Init] ----> 读取 agent_config.yaml
  |                        初始化 toolkit, renderer, vlm
  v
[Run Baseline Flow] ----> openroad base_route.tcl
  |                        输出: baseline.def
  v
[Extract Baseline Metrics] -> DRC count, Wirelength, Via count
  |                        保存 checkpoint iter_-1
  v
[Check DRC==0?] ---------> YES -> [Terminate & Save Result]
  | NO
  v
[Iteration Loop: i = 0 to max_iter-1]
  |
  +-- [Extract State]
  |     +-- extract_congestion_map() -> congestion heatmap
  |     +-- extract_routing_layers() -> layer binary maps
  |     +-- extract_netlist_stats() -> netlist JSON
  |     +-- extract_drc_report() -> DRC list with coordinates
  |
  +-- [Render Visual State]
  |     +-- render_state() -> state_iter_i.png (6-panel)
  |     +-- render_diff() -> diff_iter_i.png (optional)
  |
  +-- [VLM Policy Generation]
  |     +-- Build multimodal prompt (images + structured text)
  |     +-- Call Gemini/Claude API
  |     +-- Parse & validate JSON response
  |     +-- Fallback policy on API failure
  |
  +-- [Execute Actions]
  |     +-- rip_up_nets() -> modify DEF
  |     +-- run_incremental_route() -> openroad incremental
  |     +-- set_routing_blockage() -> modify DEF (if needed)
  |
  +-- [Evaluate]
  |     +-- extract_metrics() -> new DRC/WL/Via
  |     +-- Compare with best -> update if better
  |     +-- save_checkpoint()
  |
  +-- [Check Termination]
        +-- DRC==0? -> Terminate (SUCCESS)
        +-- No improvement for N iterations? -> Early stop
        +-- max_iter reached? -> Terminate
  |
  v
[Final Evaluation] -------> ISPDEvaluator.compare(baseline, best)
  |
  v
[Save Best Result] -------> best.def + report.json
```

### 7.2 单次迭代详细流程

```
Input: current_def, current_metrics, history
Output: new_def, new_metrics, updated history

1. State Extraction (3-5 minutes)
   - congestion_map = toolkit.extract_congestion_map(current_def)
   - routing_layers = toolkit.extract_routing_layers(current_def)
   - netlist_stats = toolkit.extract_netlist_stats(current_def)
   - drc_report = toolkit.extract_drc_report(current_def)
   => RoutingState object

2. Visual Rendering (10-20 seconds)
   - image_path = renderer.render_state(state, iteration)
   - diff_path = renderer.render_diff(prev_image, image_path) (if i>0)
   => [image_path, diff_path]

3. VLM Query (2-10 seconds, depending on API)
   - Build prompt with images + metrics + netlist stats
   - Call generate_content() with system + user prompts
   - Parse JSON from response
   - Validate schema
   => Policy JSON with action list

4. Action Execution (5-15 minutes)
   For each action in policy:
     - rip_up_reroute: 
         1. toolkit.rip_up_nets() (file edit, <1s)
         2. toolkit.run_incremental_route() (openroad, 5-15min)
     - incremental_route:
         1. toolkit.run_incremental_route() (openroad, 5-15min)
     - set_blockage:
         1. toolkit.set_routing_blockage() (file edit, <1s)
   => new_def path

5. Evaluation (2-5 minutes)
   - new_metrics = toolkit.extract_metrics(new_def)
   - Compare with best
   - Update if better
   => new_metrics, (possibly updated best)

Total per iteration: ~10-25 minutes (dominated by OpenROAD routing)
```

---

## 8. ISPD 评估标准与评分实现

### 8.1 ISPD 2019 官方评分公式

```
raw_score = Σ(violation_weight × violation_count) + Σ(metric_weight × metric_value)

其中：
- 违规项（每个违规计分）：
  * Short violation count:        500/个
  * Short area / M2 pitch^2:      500/单位
  * End-of-line spacing violation: 500/个
  * Wire spacing violation:       500/个
  * Via spacing violation:        500/个
  * Corner spacing violation:     500/个
  * Adjacent cut spacing:         500/个
  * Min-area violation:           500/个

- 布线质量项：
  * Single-cut via count:         4/个
  * Multi-cut via count:          2/个
  * Wire length:                  0.5/DBU
  * Out-of-guide wire length:     1/DBU
  * Out-of-guide via count:       1/个
  * Off-track wire length:        0.5/DBU
  * Off-track via count:          1/个
  * Wrong-way wire length:        1/DBU

scaled_score = raw_score × (1 + nondeterministic_penalty + runtime_factor)

runtime_factor = min(0.1, max(-0.1, 0.02 × log2(wall_time / median_wall_time)))
```

### 8.2 简化评分（本方案默认使用）

由于完整ISPD评分需要专门的evaluator二进制文件解析详细DRC分类，
本方案默认使用简化评分作为优化目标：

```
simplified_score = DRC_total × 1000 + Wirelength × 0.01 + Via_count × 0.1
```

**理由**：
- DRC违规是硬约束，权重最大
- 线长和Via是软优化目标
- 与完整ISPD评分趋势一致

---

## 9. 验证与基准测试

### 9.1 单元测试

```python
# tests/test_routing_toolkit.py
import unittest
from src.routing_toolkit import RoutingToolkit, RoutingMetrics

class TestRoutingToolkit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.toolkit = RoutingToolkit(
            openroad_exe='openroad',
            work_dir='/tmp/test_toolkit',
            lef_file='data/ispd2018/ispd18_test1/ispd18_test1.lef'
        )
    
    def test_extract_netlist_stats(self):
        stats = self.toolkit.extract_netlist_stats(
            'data/ispd2018/ispd18_test1/ispd18_test1.def'
        )
        self.assertGreater(stats['total_nets'], 0)
        self.assertIsInstance(stats['high_fanout_nets'], list)
    
    def test_congestion_map_shape(self):
        cmap = self.toolkit.extract_congestion_map(
            'data/ispd2018/ispd18_test1/ispd18_test1.def',
            resolution=256
        )
        self.assertEqual(cmap.shape, (256, 256))

# 运行：python -m pytest tests/
```

### 9.2 集成测试

```bash
# 测试完整流程（使用最小benchmark）
python scripts/run_agent.py \
    --benchmark ispd19_sample \
    --data-dir ./data/ispd2019 \
    --work-dir ./outputs/test_run \
    --max-iter 3 \
    --patience 2
```

### 9.3 成功标准

| 标准 | 阈值 | 验证方法 |
|------|------|----------|
| 端到端运行 | 在3个以上benchmark完整运行 | 检查outputs目录有结果 |
| DRC减少 | 相比基线减少 >10% | ISPDEvaluator对比报告 |
| 线长控制 | 相比基线变化 < +5% | 同上 |
| VLM策略格式 | JSON正确率 >80% | 日志检查parse成功率 |
| 单次迭代时间 | < 30分钟 | time命令测量 |

---

## 10. 故障排除

### 10.1 OpenROAD 相关问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `openroad: command not found` | 未安装或不在PATH | `conda activate openroad_env` 或检查安装 |
| `read_lef failed` | LEF文件路径错误 | 检查 `--data-dir` 参数和benchmark命名 |
| `detailed_route 段错误` | 内存不足或LEF/DEF不匹配 | 增加swap空间，检查文件兼容性 |
| DRC报告为空 | OpenROAD版本差异 | 检查stderr中的 `[WARNING DRT-xxx]` 消息 |
| 线长报告为0 | `report_wire_length` 命令不支持 | 确认OpenROAD版本 >= v2.0 |

### 10.2 VLM API 相关问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| API key无效 | 环境变量未设置 | 检查 `.env` 文件，运行 `source .env` |
| Rate limit exceeded | API调用过于频繁 | 增加 `--patience` 减少迭代；使用Gemini Flash（限制较宽松） |
| JSON解析失败 | VLM输出格式不标准 | 检查prompt中的格式要求；启用response_mime_type='application/json' |
| 图像上传失败 | 文件不存在或过大 | 检查PNG文件是否生成；压缩到 < 2MB |

### 10.3 性能优化

```bash
# 如果单次迭代太慢(>30min)：
# 1. 使用更快的VLM模型
python scripts/run_agent.py --vlm-model gemini-2.5-flash  # 比Pro快5x

# 2. 降低可视化分辨率
python scripts/run_agent.py --resolution 256  # 默认512

# 3. 减少迭代次数，增加耐心值
python scripts/run_agent.py --max-iter 10 --patience 3

# 4. OpenROAD多线程
export OPENROAD_THREADS=16

# 5. 使用更快的机器（布线是CPU密集型）
# 推荐: 16+ cores, 64GB+ RAM
```

### 10.4 调试模式

```python
# 在agent_controller.py中启用调试
import logging
logging.basicConfig(level=logging.DEBUG)

# 保存所有Tcl脚本用于调试
# 在 routing_toolkit.py 的 _run_tcl_script 中注释掉清理代码

# 手动运行Tcl脚本调试
openroad -exit -no_init work/toolkit/_temp_0.tcl
```

---

## 附录 A：文件清单

| # | 文件路径 | 说明 | 行数(约) |
|---|---------|------|---------|
| 1 | `src/routing_toolkit.py` | OpenROAD工具包装器 | 800+ |
| 2 | `src/visual_renderer.py` | 视觉状态渲染器 | 500+ |
| 3 | `src/vlm_policy.py` | VLM策略生成器 | 400+ |
| 4 | `src/agent_controller.py` | Agent主控制器 | 350+ |
| 5 | `src/ispd_evaluator.py` | ISPD评估器 | 250+ |
| 6 | `src/utils.py` | 工具函数 | 80+ |
| 7 | `scripts/run_agent.py` | 主执行脚本 | 150+ |
| 8 | `scripts/run_baseline.py` | 基线运行脚本 | 80+ |
| 9 | `scripts/batch_run.sh` | 批量执行脚本 | 30+ |
| 10 | `config/agent_config.yaml` | 配置文件 | 50+ |
| 11 | `requirements.txt` | Python依赖 | 20+ |
| 12 | `.env` | 环境变量 | 10+ |

---

## 附录 B：关键 OpenROAD 命令速查

```tcl
# === 文件操作 ===
read_lef [-tech] [-library] <file>
read_def <file>
write_def [-version 5.8] <file>
read_db <file>
write_db <file>

# === 全局布线 (FastRoute) ===
global_route -guide_file <out> -congestion_report_file <rpt> -verbose

# === 详细布线 (TritonRoute) ===
detailed_route -output_drc <rpt> -output_maze <log> -verbose <level>

# === 报告 ===
report_wire_length -net * -detailed_route -summary -file <out>

# === 层设置 ===
set_routing_layers -signal <min>-<max> -clock <min>-<max>
```

---

## 附录 C：VLM Prompt 工程说明

本方案中VLM的prompt设计遵循以下原则：

1. **系统提示（System Prompt）**：提供领域知识（设计规则、可用动作、输出格式）
2. **用户提示（User Prompt）**：提供当前状态（图像+文本指标）
3. **图像描述**：在每个子图位置添加 `[Description]` 标注，引导VLM注意力
4. **JSON Schema约束**：在system prompt中明确定义输出schema，要求严格的JSON格式
5. **错误处理**：当VLM输出不合法JSON时，使用正则提取 + fallback策略

---

*本文档由 AI Agent 基于 OpenROAD v2.0 官方文档、ISPD 2018/2019 竞赛规则和 Gemini API 文档生成。*
*最后更新：2025年1月*
