"""
src/vlm_policy.py

VLM policy generator for the redesigned routing agent.  Supports both the new
fine-grained action space and the legacy coarse action space for backward
compatibility.
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

    Supports Gemini, Anthropic, and OpenAI multimodal APIs.  Returns a JSON
    policy that follows the redesigned schema.
    """

    SYSTEM_PROMPT = """You are an expert digital IC physical design engineer specializing in detailed routing optimization.

## Your Role
You are a DIAGNOSTICIAN and STRATEGIST, not just an action selector.  For each iteration:
1. Diagnose the root cause of each DRC violation cluster.
2. Select the smallest repair action that addresses the root cause.
3. Prefer segment/via-level edits for isolated violations.
4. Use net-level rip-up only when a net has >5 violations or spans >3 segments.
5. Use region-level blockages only for multi-net congestion hotspots.

## Design Rules Reference
- Metal layer directions (typical): M1/M3/M5 horizontal, M2/M4/M6 vertical
- Optimization priority: DRC=0 > minimize wirelength > minimize via count

## Available Fine-Grained Actions
Segment-level:
- "rip_up_segment": { "net_name", "segment_index", "layer" }
- "reassign_layer": { "net_name", "segment_index", "from_layer", "to_layer" }
- "insert_jog": { "net_name", "segment_index", "layer", "jog_point": [x,y], "jog_direction": "horizontal|vertical" }

Via-level:
- "move_via": { "net_name", "via_index", "old_position": [x,y], "new_position": [x,y], "via_type" }
- "change_via_type": { "net_name", "via_index", "position": [x,y], "new_type" }

Net-level:
- "rip_up_net": { "net_name" }
- "reroute_net_with_constraints": { "net_name", "preferred_layers": [...], "avoid_regions": [{"bbox": [x1,y1,x2,y2], "reason": "..."}] }
- "fishbone_route_net": { "net_name", "preferred_layers": [...], "trunk_direction": "auto|horizontal|vertical", "max_branches_per_trunk": 50, "obstacle_margin_nm": 200 }

Region-level:
- "set_routing_blockage": { "bbox": [x1,y1,x2,y2], "layers": [...], "hardness": "soft|hard" }
- "set_soft_guidance": { "net_name", "guide_points": [[x,y], ...], "layer" }
- "relax_region": { "bbox": [x1,y1,x2,y2], "layers": [...] }

Composite / Strategy-level:
- "route_in_box": { "bbox": [x1,y1,x2,y2], "target_nets": [...], "preferred_layers": [...] }
- "route_channel": { "bbox": [x1,y1,x2,y2], "target_nets": [...], "preferred_layers": [...] }
- "route_repair": { "violation_ids": [...] }
- "optimize_congestion": { "bbox": [x1,y1,x2,y2], "affected_nets": [...], "layers": [...] }
- "cleanup_routing": {}

Control:
- "terminate": {}
- "noop": {}

## Legacy Coarse Actions (still accepted)
- "rip_up_reroute": { "target_nets": [...] }
- "incremental_route": {}
- "layer_assign": { "target_nets": [...], "layer_preference": [...] }
- "set_blockage": { "avoid_regions": [{"bbox": [...], "reason": "..."}], "layer_preference": [...] }

## Output Format
Output MUST be valid JSON with this exact schema:
{
  "routing_policy": {
    "iteration": <integer>,
    "strategy_type": <string>,
    "analysis": <string: 2-3 sentence analysis>,
    "diagnosis": [
      {
        "cluster_id": <string>,
        "root_cause": <string>,
        "recommended_action": <string>,
        "confidence": <number 0-1>
      }
    ],
    "priority_actions": [
      {
        "action": <string>,
        "parameters": { <action-specific> },
        "reason": <string>,
        "expected_impact": <string: "drc_fix" | "wl_reduce" | "via_reduce" | "congestion_relief">
      }
    ],
    "termination_check": <boolean>,
    "next_state_focus": <string>
  }
}

## Rules
- Target only nets that exist in the netlist statistics.
- Layer preferences must be valid metal layers (M1-M6 or Metal1-Metal6).
- Avoid regions must be within chip boundaries in DBU (nanometers).
- Provide clear reasoning for each action.
- If DRC > 0, focus on fixing violations first.
- If DRC == 0, suggest wirelength/via optimization or terminate.
"""

    USER_PROMPT_TEMPLATE = """## Current State - Iteration {iteration}

### Visual Inputs
1. [Top-Left] Global Congestion Heatmap
2. [Top-Center] M3 Routing Layer
3. [Top-Right] M4 Routing Layer
4. [Bottom-Left] DRC Violation Map
5. [Bottom-Center] Congestion + Routing Overlay
6. [Bottom-Right] Hotspot Zoom
{violation_crops_note}

### DRC Violation Table (Top {max_violations} by severity)
{drc_table}

### Per-Net Routing Features (Top {max_nets} by DRC count / criticality)
{net_features_table}

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
- Wirelength: {wirelength:.2f} um
- Via Count: {via_count}

### Previous Action Result
{previous_action_report}

Please analyze the routing state, diagnose root causes, and output the optimization strategy in the required JSON format.
"""

    def __init__(
        self,
        model: Optional[str] = None,
        client_type: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        temperature: float = 0.1,
    ):
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
                client_type = "anthropic"

        self.client_type = client_type
        self._init_client()

        # Track actions that failed or were rolled back so the fallback policy
        # and future prompts can avoid them.
        self.blacklisted_actions: List[Dict] = []

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
        drc_violations: Optional[List] = None,
        net_features: Optional[Dict] = None,
        previous_attribution: Optional[Dict] = None,
        violation_crop_paths: Optional[List[str]] = None,
        update_blacklist_from_attribution: bool = True,
    ) -> Dict:
        """Build the prompt, call the VLM, parse and validate JSON."""
        if update_blacklist_from_attribution and previous_attribution:
            self.update_blacklist(previous_attribution.get("action_results", []))

        user_prompt = self._build_user_prompt(
            netlist_stats=netlist_stats,
            metrics=metrics,
            last_action=last_action,
            last_metrics=last_metrics,
            iteration=iteration,
            drc_violations=drc_violations,
            net_features=net_features,
            previous_attribution=previous_attribution,
            violation_crop_paths=violation_crop_paths,
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

    def update_blacklist(self, action_results: List[Dict]):
        """Add failed or rolled-back actions to the internal blacklist."""
        for entry in action_results or []:
            if not entry.get("success") or entry.get("rolled_back"):
                self.blacklisted_actions.append(
                    {
                        "action": entry.get("action"),
                        "parameters": entry.get("parameters", {}),
                        "reason": entry.get("error") or entry.get("local_check_detail"),
                    }
                )

    def _build_user_prompt(
        self,
        netlist_stats: Dict,
        metrics: Dict,
        last_action: str,
        last_metrics: Optional[Dict],
        iteration: int,
        drc_violations: Optional[List],
        net_features: Optional[Dict],
        previous_attribution: Optional[Dict],
        violation_crop_paths: Optional[List[str]],
    ) -> str:
        drc_table = self._format_drc_table(drc_violations, max_rows=20)
        net_features_table = self._format_net_features_table(
            net_features, max_rows=10
        )

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

        prev_report = self._format_previous_attribution(
            last_action,
            last_metrics,
            metrics,
            previous_attribution,
            self.blacklisted_actions,
        )

        crops_note = ""
        if violation_crop_paths:
            crops_note = (
                "\nAdditional local crops around top violation clusters are also "
                f"provided: {', '.join(violation_crop_paths)}"
            )

        return self.USER_PROMPT_TEMPLATE.format(
            iteration=iteration,
            max_violations=20,
            drc_table=drc_table,
            max_nets=10,
            net_features_table=net_features_table,
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
            via_count=metrics.get("via_count", 0),
            previous_action_report=prev_report,
            violation_crops_note=crops_note,
        )

    @staticmethod
    def _format_drc_table(drc_violations: Optional[List], max_rows: int) -> str:
        if not drc_violations:
            return "  No DRC violations."

        lines = [
            "| ID | Type | Layer | Center (x,y) | Nets Involved | Severity | Description |",
            "|----|------|-------|--------------|---------------|----------|-------------|",
        ]

        sorted_v = sorted(
            drc_violations,
            key=lambda v: getattr(v, "severity", 1.0),
            reverse=True,
        )[:max_rows]

        for v in sorted_v:
            vtype = getattr(v, "vtype", "unknown")
            layer = getattr(v, "layer", "")
            center = getattr(v, "center", (0, 0))
            nets = ", ".join(getattr(v, "nets_involved", [])) or "unknown"
            severity = getattr(v, "severity", 1.0)
            desc = getattr(v, "description", "")[:40]
            lines.append(
                f"| {v.violation_id} | {vtype} | {layer} | {center} | {nets} | {severity:.2f} | {desc} |"
            )

        return "\n".join(lines)

    @staticmethod
    def _format_net_features_table(
        net_features: Optional[Dict], max_rows: int
    ) -> str:
        if not net_features:
            return "  No net features available."

        lines = [
            "| Net | Fanout | HPWL (um) | Segments | Vias | DRCs | Layers | Congestion | Critical |",
            "|-----|--------|-----------|----------|------|------|--------|------------|----------|",
        ]

        sorted_nets = sorted(
            net_features.values(),
            key=lambda n: (n.drc_count, n.is_critical, n.fanout),
            reverse=True,
        )[:max_rows]

        for nf in sorted_nets:
            layers = ", ".join(
                f"{k}:{v}" for k, v in list(nf.layer_usage.items())[:3]
            )
            lines.append(
                f"| {nf.net_name} | {nf.fanout} | {nf.hpwl_um:.2f} | "
                f"{nf.routed_segments} | {nf.via_count} | {nf.drc_count} | "
                f"{layers} | {nf.congestion_score:.2f} | {nf.is_critical} |"
            )

        return "\n".join(lines)

    @staticmethod
    def _format_previous_attribution(
        last_action: str,
        last_metrics: Optional[Dict],
        metrics: Dict,
        attribution: Optional[Dict],
        blacklisted_actions: Optional[List[Dict]] = None,
    ) -> str:
        if attribution is None:
            if last_metrics:
                drc_delta = metrics.get("drc_total", 0) - last_metrics.get(
                    "drc_total", 0
                )
                wl_delta = metrics.get("wirelength", 0) - last_metrics.get(
                    "wirelength", 0
                )
                via_delta = metrics.get("via_count", 0) - last_metrics.get(
                    "via_count", 0
                )
                return (
                    f"Last action: {last_action}\n"
                    f"DRC delta: {drc_delta:+d}, WL delta: {wl_delta:+.2f}, Via delta: {via_delta:+d}"
                )
            return f"Last action: {last_action}\nNo previous metrics available."

        lines = [f"Last action: {last_action}"]
        lines.append(
            f"Fixed violations: {len(attribution.get('fixed_violations', []))} "
            f"({', '.join(attribution.get('fixed_violations', []))})"
        )
        lines.append(
            f"New violations: {len(attribution.get('new_violations', []))} "
            f"({', '.join(attribution.get('new_violations', []))})"
        )
        lines.append(
            f"Moved violations: {len(attribution.get('moved_violations', []))}"
        )
        lines.append(
            f"Persisted violations: {len(attribution.get('persisted_violations', []))}"
        )
        lines.append(
            f"Global DRC delta: {attribution.get('drc_delta', 0):+d}, "
            f"WL delta: {attribution.get('wirelength_delta', 0):+.2f}, "
            f"Via delta: {attribution.get('via_delta', 0):+d}"
        )

        per_net = attribution.get("per_net_drc_delta", {})
        if per_net:
            lines.append("Per-net DRC delta:")
            for net, delta in sorted(
                per_net.items(), key=lambda x: abs(x[1]), reverse=True
            )[:5]:
                lines.append(f"  {net}: {delta:+d}")

        action_results = attribution.get("action_results", [])
        if action_results:
            success_rate = attribution.get("action_success_rate", 0.0)
            lines.append(f"Action success rate: {success_rate:.0%}")
            failed_summary = attribution.get("failed_action_summary", {})
            if failed_summary:
                lines.append("Failed/rolled-back action types:")
                for action_type, count in sorted(
                    failed_summary.items(), key=lambda x: x[1], reverse=True
                ):
                    lines.append(f"  - {action_type}: {count}")

        if blacklisted_actions:
            lines.append("Avoid repeating these recent failed actions:")
            for entry in blacklisted_actions[-5:]:
                action = entry.get("action", "unknown")
                params = entry.get("parameters", {})
                reason = entry.get("reason", "")
                lines.append(f"  - {action}({params}): {reason}")

        return "\n".join(lines)

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
            # Avoid recently blacklisted fallback actions.
            blacklisted_types = {
                entry.get("action") for entry in self.blacklisted_actions
            }
            if "incremental_route" not in blacklisted_types:
                fallback_action = {
                    "action": "incremental_route",
                    "parameters": {},
                    "reason": "Fallback: re-run detailed route without edits",
                    "expected_impact": "drc_fix",
                }
            elif "rip_up_reroute" not in blacklisted_types:
                fallback_action = {
                    "action": "rip_up_reroute",
                    "parameters": {},
                    "reason": "Fallback: rip-up and reroute without a specific net",
                    "expected_impact": "drc_fix",
                }
            else:
                fallback_action = {
                    "action": "noop",
                    "parameters": {},
                    "reason": "Fallback: all safe actions recently failed",
                    "expected_impact": "drc_fix",
                }

            return {
                "routing_policy": {
                    "iteration": iteration,
                    "strategy_type": "fallback_fix_drc",
                    "analysis": "VLM API failed. Using fallback strategy to fix DRC violations.",
                    "diagnosis": [],
                    "priority_actions": [fallback_action],
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
                    "diagnosis": [],
                    "priority_actions": [],
                    "termination_check": True,
                    "next_state_focus": "Optimization complete",
                }
            }


if __name__ == "__main__":
    vlm = VLMPolicyGenerator()
    print("VLMPolicyGenerator initialized successfully")
