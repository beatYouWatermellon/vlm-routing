"""
src/vlm_policy.py

VLM policy generator. Uses a multimodal LLM to analyze routing state images and
structured text, then emits a JSON routing optimization policy.
"""

import os
import re
import json
import base64
import time
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    from google import genai
    from google.genai import types
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
    Generate routing optimization policies from visual and textual state.

    Supports Gemini, Anthropic, and OpenAI multimodal APIs. Returns a JSON
    policy that follows the schema defined in ``SYSTEM_PROMPT``.
    """

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

    def __init__(
        self,
        model: Optional[str] = None,
        client_type: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        temperature: float = 0.1,
    ):
        # Allow environment defaults for model selection
        if model is None:
            model = os.getenv("ANTHROPIC_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-2.5-flash"
        self.model_name = model
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.temperature = temperature
        self.client = None

        if client_type is None:
            lower_model = model.lower()
            if "gemini" in lower_model:
                client_type = "gemini"
            elif "claude" in lower_model or "kimi" in lower_model:
                client_type = "anthropic"
            elif "gpt" in lower_model:
                client_type = "openai"
            else:
                # Default to anthropic when a model name is provided but ambiguous;
                # this lets Anthropic-compatible providers such as Kimi work out of
                # the box when ANTHROPIC_BASE_URL is set.
                client_type = "anthropic"

        self.client_type = client_type
        self._init_client()

    def _init_client(self):
        if self.client_type == "gemini":
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not found in environment")
            if not GEMINI_AVAILABLE:
                raise ImportError(
                    "google-genai not installed. Run: pip install google-genai"
                )
            self.client = genai.Client(api_key=api_key)

        elif self.client_type == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in environment")
            if not ANTHROPIC_AVAILABLE:
                raise ImportError("anthropic not installed. Run: pip install anthropic")
            base_url = os.getenv("ANTHROPIC_BASE_URL")
            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = anthropic.Anthropic(**client_kwargs)

        elif self.client_type == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found in environment")
            if not OPENAI_AVAILABLE:
                raise ImportError("openai not installed. Run: pip install openai")
            self.client = openai.OpenAI(api_key=api_key)

        else:
            raise ValueError(f"Unknown client type: {self.client_type}")

        print(f"[OK] VLM client initialized: {self.client_type} / {self.model_name}")

    def generate_policy(
        self,
        image_paths: List[str],
        netlist_stats: Dict,
        metrics: Dict,
        last_action: str = "None",
        last_metrics: Optional[Dict] = None,
        iteration: int = 0,
    ) -> Dict:
        """Build the prompt, call the VLM, parse and validate JSON."""
        drc_delta = 0
        wl_delta = 0.0
        if last_metrics:
            drc_delta = metrics.get("drc_total", 0) - last_metrics.get("drc_total", 0)
            prev_wl = last_metrics.get("wirelength", 1)
            if prev_wl > 0:
                wl_delta = (
                    (metrics.get("wirelength", 0) - prev_wl) / prev_wl
                ) * 100

        hf_nets = netlist_stats.get("high_fanout_nets", [])[:5]
        hf_list = "\n".join(
            [
                f"  - {n['name']}: fanout={n['fanout']}, HPWL={n['hpwl']}um"
                for n in hf_nets
            ]
        )

        ld_nets = netlist_stats.get("long_distance_nets", [])[:5]
        ld_list = "\n".join(
            [
                f"  - {n['name']}: HPWL={n['hpwl']}um, fanout={n['fanout']}"
                for n in ld_nets
            ]
        )

        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            iteration=iteration,
            total_nets=netlist_stats.get("total_nets", 0),
            total_pins=netlist_stats.get("total_pins", 0),
            component_count=netlist_stats.get("component_count", 0),
            std_cell_count=netlist_stats.get("std_cell_count", 0),
            io_pin_count=netlist_stats.get("io_pin_count", 0),
            high_fanout_count=len(netlist_stats.get("high_fanout_nets", [])),
            high_fanout_list=hf_list if hf_list else "  None",
            long_dist_count=len(netlist_stats.get("long_distance_nets", [])),
            long_dist_list=ld_list if ld_list else "  None",
            drc_total=metrics.get("drc_total", 0),
            drc_spacing=metrics.get("drc_spacing", 0),
            drc_min_width=metrics.get("drc_min_width", 0),
            drc_short=metrics.get("drc_short", 0),
            drc_end_of_line=metrics.get("drc_end_of_line", 0),
            drc_via_spacing=metrics.get("drc_via_spacing", 0),
            wirelength=metrics.get("wirelength", 0),
            wirelength_mm=metrics.get("wirelength", 0) / 1e6,
            via_count=metrics.get("via_count", 0),
            last_action=last_action,
            drc_delta=drc_delta,
            wl_delta=wl_delta,
        )

        for attempt in range(self.max_retries):
            try:
                if self.client_type == "gemini":
                    response = self._call_gemini(image_paths, user_prompt)
                elif self.client_type == "anthropic":
                    response = self._call_anthropic(image_paths, user_prompt)
                elif self.client_type == "openai":
                    response = self._call_openai(image_paths, user_prompt)
                else:
                    raise ValueError(f"Unknown client type: {self.client_type}")

                policy = self._extract_json(response)
                self._validate_policy_schema(policy)
                return policy

            except Exception as e:
                print(f"[WARNING] VLM call attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    print("[ERROR] All VLM retries exhausted, returning fallback")
                    return self._fallback_policy(metrics, iteration)

        return self._fallback_policy(metrics, iteration)

    def _call_gemini(self, image_paths: List[str], user_prompt: str) -> str:
        from PIL import Image as PILImage

        content = [self.SYSTEM_PROMPT, user_prompt]

        for img_path in image_paths:
            if Path(img_path).exists():
                content.append(PILImage.open(img_path).convert("RGB"))

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=content,
            config=types.GenerateContentConfig(
                temperature=self.temperature,
                response_mime_type="application/json",
            ),
        )
        return response.text

    def _call_anthropic(self, image_paths: List[str], user_prompt: str) -> str:
        message_content = []

        for img_path in image_paths:
            if Path(img_path).exists():
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                message_content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_data,
                        },
                    }
                )

        message_content.append(
            {"type": "text", "text": self.SYSTEM_PROMPT + "\n\n" + user_prompt}
        )

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            temperature=self.temperature,
            messages=[{"role": "user", "content": message_content}],
        )

        return response.content[0].text

    def _call_openai(self, image_paths: List[str], user_prompt: str) -> str:
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
        ]

        content = [{"type": "text", "text": user_prompt}]
        for img_path in image_paths:
            if Path(img_path).exists():
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_data}",
                            "detail": "high",
                        },
                    }
                )

        messages.append({"role": "user", "content": content})

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=self.temperature,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        return response.choices[0].message.content

    def _extract_json(self, text: str) -> Dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Cannot extract valid JSON from VLM output: {text[:500]}")

    def _validate_policy_schema(self, policy: Dict):
        if "routing_policy" not in policy:
            raise ValueError("Missing 'routing_policy' key")

        rp = policy["routing_policy"]
        required_keys = [
            "iteration", "strategy_type", "priority_actions", "termination_check"
        ]
        for key in required_keys:
            if key not in rp:
                raise ValueError(f"Missing key in routing_policy: {key}")

    def _fallback_policy(self, metrics: Dict, iteration: int) -> Dict:
        drc_total = metrics.get("drc_total", 999)

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
                            "avoid_regions": [],
                        }
                    ],
                    "termination_check": False,
                    "next_state_focus": "Check if DRC count decreased",
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
                    "next_state_focus": "Optimization complete",
                }
            }


if __name__ == "__main__":
    vlm = VLMPolicyGenerator()
    print("VLMPolicyGenerator initialized successfully")
