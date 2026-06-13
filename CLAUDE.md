# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Status

The repository contains a full implementation of the agent described in `VLM_Routing_Agent_Complete_Technical_Spec.md`, plus the original specification document. All source modules, entry scripts, Tcl templates, configuration, and tests described in the spec are present under `src/`, `scripts/`, `tcl/`, `config/`, and `tests/`.

## Environment Setup

The spec targets Ubuntu 22.04 LTS, Python 3.10+, OpenROAD v2.0+, and a VLM API key.

The agent supports multiple providers and auto-detects the client from the model name:
- `gemini-*` models use the Google `google-genai` SDK.
- `claude-*` models use the Anthropic SDK.
- `gpt-*` models use the OpenAI SDK.
- `kimi-*` models (or any model name when `ANTHROPIC_BASE_URL` is set) use the Anthropic SDK against an Anthropic-compatible endpoint.

Install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Configure API keys in `.env`:

```bash
# Gemini (used when model name starts with "gemini-")
GEMINI_API_KEY=your_gemini_api_key_here

# Anthropic / Kimi (Anthropic-compatible endpoint)
ANTHROPIC_API_KEY=your_anthropic_or_kimi_key_here
ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
ANTHROPIC_MODEL=kimi-for-coding

# OpenAI (used when model name starts with "gpt-")
# OPENAI_API_KEY=...

OPENROAD_EXE=/path/to/openroad
OPENROAD_THREADS=8
MAX_VLM_RETRIES=3
VLM_RETRY_DELAY=2
```

## Common Commands

Run a single ISPD benchmark through the agent optimization loop:

```bash
python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018
```

Run a baseline flow only:

```bash
python scripts/run_baseline.py --benchmark ispd18_test1 --data-dir ./data/ispd2018
```

Run a quick integration test with limited iterations:

```bash
python scripts/run_agent.py --benchmark ispd18_test1 \
    --data-dir ./data/ispd2018 --work-dir ./outputs/test_run \
    --max-iter 3 --patience 2
```

Run unit tests:

```bash
python -m pytest tests/
```

Batch run across ISPD 2018 benchmarks:

```bash
bash scripts/batch_run.sh ./data/ispd2018 ./outputs/agent_runs 15
```

## High-Level Architecture

The system follows an Extract → Policy → Execute → Evaluate loop built around OpenROAD Tcl bridges.

### Core Modules

- `src/routing_toolkit.py` — `RoutingToolkit` wraps OpenROAD by generating Tcl scripts and calling `openroad -exit script.tcl` via subprocess. It provides state extraction (congestion maps, routing layers per metal layer, netlist statistics, DRC reports, wirelength/via metrics) and routing actions (baseline flow, rip-up nets, incremental detailed route, routing blockage insertion).
- `src/visual_renderer.py` — `VisualRenderer` produces a 2×3 composite PNG of the current routing state: global congestion heatmap, M3/M4 routing layers, DRC violation map, congestion+routing overlay, and an auto-detected hotspot zoom. It also renders iteration-to-iteration difference images and policy-action overlays.
- `src/vlm_policy.py` — `VLMPolicyGenerator` sends the rendered images plus structured metrics/netlist text to a multimodal LLM (Gemini 2.5 Flash preferred, Claude 3.5 Sonnet or GPT-4o as fallbacks). It requests a JSON policy via each provider's JSON/JSON-object mode and validates the required `routing_policy` schema.
- `src/agent_controller.py` — `RoutingAgent` orchestrates the loop: baseline routing, full state extraction, visual rendering, VLM query, action execution, metric evaluation, checkpoint saving, and early stopping based on DRC improvement or patience.
- `src/ispd_evaluator.py` — `ISPDEvaluator` computes simplified and ISPD 2018/2019-style scores from `RoutingMetrics`, and compares baseline vs. optimized results.

### Data Flow

```
ISPD benchmark (LEF/DEF/Guide)
  -> OpenROAD baseline (FastRoute -> TritonRoute)
  -> RoutingToolkit extracts congestion map, layer maps, netlist stats, DRC report
  -> VisualRenderer composes multi-panel PNG
  -> VLMPolicyGenerator (image + text -> JSON policy)
  -> RoutingAgent executes rip_up_reroute / incremental_route / set_blockage
  -> ISPDEvaluator scores result
  -> repeat until DRC==0, no improvement for `patience` iterations, or max iterations reached
```

### Key Design Decisions

- OpenROAD is controlled through Tcl script bridging rather than Python `odb` bindings, for version compatibility.
- State is represented as a multi-channel PNG plus structured JSON so the VLM receives both visual and numeric context.
- Actions modify the DEF file directly (e.g., stripping `+ ROUTED` sections for rip-up, injecting `BLOCKAGES` sections for avoid regions) and rely on OpenROAD's detailed router to re-route incrementally.
- Optimization priority is DRC elimination first, then wirelength, then via count.

### Configuration

Runtime behavior is controlled through CLI arguments in `scripts/run_agent.py` and `config/agent_config.yaml`. Important knobs include `max_iterations`, `patience`, `vlm-model`, `resolution`, and `scoring-mode` (`simplified` | `ispd2019` | `ispd2018`).

### Output Layout

After a run, results are written under the configured work directory:

```
outputs/agent_runs/<benchmark>/
  checkpoints/          # iter_*.def and iter_*.json
  toolkit/              # OpenROAD intermediate reports
  visual_states/        # state_iter_*.png, diff_iter_*.png
  evaluation/           # <benchmark>_report.json
```

## ISPD Data

The spec assumes ISPD 2018/2019 benchmark sets placed under `data/ispd2018/` and `data/ispd2019/`, each benchmark directory containing `<name>.lef`, `<name>.def`, and `<name>.guide`.
