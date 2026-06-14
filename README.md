# VLM Routing Agent

Agent-based detailed routing optimization for digital IC physical design using OpenROAD and multimodal LLMs.

## Overview

This project implements the redesigned routing agent described in `VLM_Routing_Agent_Redesign.md`. It combines:

- **OpenROAD** (FastRoute global routing + TritonRoute detailed routing)
- **Multimodal LLMs** (Gemini, Claude, GPT-4o, or Kimi via Anthropic-compatible API) for routing diagnosis and strategy generation
- **Fine-grained DEF editing** at the segment/via/net/region level
- **Structured state extraction** (DRC violations, per-net routing features, local violation crops)
- **Attributable feedback** so the VLM can learn which actions fix which violations
- **ISPD 2018/2019 scoring** for evaluation

The agent follows a **Diagnose → Strategize → Localize → Execute → Evaluate → Attribute** loop.

## Environment

- Ubuntu 22.04 LTS (recommended)
- Python 3.10+ (tested on 3.13)
- OpenROAD v2.0+
- Gemini / Anthropic / OpenAI / Kimi API key

## Quick Start

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure API keys
# Edit the existing .env file and set the variables for your chosen provider:
#   - Gemini: GEMINI_API_KEY
#   - Anthropic: ANTHROPIC_API_KEY
#   - OpenAI: OPENAI_API_KEY
#   - Kimi (Anthropic-compatible): ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL

# Download ISPD benchmarks into data/ispd2018/ and data/ispd2019/

# Run baseline
python scripts/run_baseline.py --benchmark ispd18_test1 --data-dir ./data/ispd2018

# Run agent optimization with fine-grained actions (default)
python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018

# Run agent with only legacy coarse actions
python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018 --no-fine-actions
```

## Project Structure

```
vlm-routing/
├── src/                       # Core Python modules
│   ├── routing_toolkit.py     # OpenROAD Tcl bridge and state extraction
│   ├── visual_renderer.py     # Composite PNG rendering + violation crops
│   ├── vlm_policy.py          # Multimodal LLM policy generator (new schema)
│   ├── agent_controller.py    # Optimization loop orchestration
│   ├── ispd_evaluator.py      # ISPD scoring and comparison
│   ├── utils.py               # Logging and config helpers
│   ├── def_parser.py          # Pure-Python DEF net/segment/via parser
│   ├── def_editor.py          # Fine-grained DEF text editor
│   ├── eda_provider.py        # Abstract EDA backend interface
│   ├── openroad_provider.py   # OpenROAD implementation of EDAProvider
│   ├── tcl_generator.py       # Tcl script generators
│   ├── policy_executor.py     # Action dispatcher
│   ├── attribution_engine.py  # Attributable delta computation
│   ├── fishbone_router.py     # Trunk-and-branch net-level router
│   └── odb_editor.py          # Optional ODB-based editor (stub when unavailable)
├── scripts/                   # Entry-point scripts
│   ├── run_agent.py
│   ├── run_baseline.py
│   └── batch_run.sh
├── tcl/                       # OpenROAD Tcl templates
├── config/agent_config.yaml
├── tests/
└── data/                      # ISPD benchmarks
```

## Fine-Grained Action Space

The redesigned agent supports segment/via/net/region-level actions:

- `rip_up_segment`, `reassign_layer`, `insert_jog`
- `move_via`, `change_via_type`
- `rip_up_net`, `reroute_net_with_constraints`, `fishbone_route_net`
- `set_routing_blockage`, `set_soft_guidance`, `relax_region`
- `terminate`, `noop`

Legacy coarse actions (`rip_up_reroute`, `incremental_route`, `layer_assign`, `set_blockage`) remain supported for backward compatibility.

## Testing

```bash
python -m pytest tests/
```

## License

Provided as-is for research and educational use.
