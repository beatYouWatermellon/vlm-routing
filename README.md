# VLM Routing Agent

Agent-based detailed routing optimization for digital IC physical design using OpenROAD and multimodal LLMs.

## Overview

This project implements the routing agent described in `VLM_Routing_Agent_Complete_Technical_Spec.md`. It combines:

- **OpenROAD** (FastRoute global routing + TritonRoute detailed routing)
- **Multimodal LLMs** (Gemini, Claude, GPT-4o, or Kimi via Anthropic-compatible API) for routing policy generation
- **DEF-based state extraction and manipulation** for rip-up/reroute and blockage actions
- **ISPD 2018/2019 scoring** for evaluation

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
cp .env.example .env
# Edit .env and set the variables for your chosen provider:
#   - Gemini: GEMINI_API_KEY
#   - Anthropic: ANTHROPIC_API_KEY
#   - OpenAI: OPENAI_API_KEY
#   - Kimi (Anthropic-compatible): ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL

# Download ISPD benchmarks into data/ispd2018/ and data/ispd2019/

# Run baseline
python scripts/run_baseline.py --benchmark ispd18_test1 --data-dir ./data/ispd2018

# Run agent optimization
python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018
```

## Project Structure

```
vlm-routing/
├── src/                    # Core Python modules
│   ├── routing_toolkit.py  # OpenROAD Tcl bridge and state extraction
│   ├── visual_renderer.py  # Composite PNG rendering for VLM input
│   ├── vlm_policy.py       # Multimodal LLM policy generator
│   ├── agent_controller.py # Optimization loop orchestration
│   ├── ispd_evaluator.py   # ISPD scoring and comparison
│   └── utils.py            # Logging and config helpers
├── scripts/                # Entry-point scripts
│   ├── run_agent.py
│   ├── run_baseline.py
│   └── batch_run.sh
├── tcl/                    # OpenROAD Tcl templates
├── config/agent_config.yaml
├── tests/
└── data/                   # ISPD benchmarks
```

## Testing

```bash
python -m pytest tests/
```

## License

Provided as-is for research and educational use.
