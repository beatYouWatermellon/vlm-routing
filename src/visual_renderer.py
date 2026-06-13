"""
src/visual_renderer.py

Visual state renderer. Converts multi-dimensional routing state into composite
PNG images suitable for multimodal LLM consumption.
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import io


class VisualRenderer:
    """
    Render routing state as composite PNG images for VLM input.

    Produces a 2x3 panel layout (global congestion, M3, M4, DRC markers,
    congestion+routing overlay, hotspot zoom) plus a statistics text bar.
    """

    def __init__(self, output_dir: str, resolution: int = 512):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.resolution = resolution

        self.congestion_cmap = LinearSegmentedColormap.from_list(
            "congestion",
            ["darkblue", "blue", "green", "yellow", "orange", "red", "darkred"],
        )

        self.drc_color_map = {
            "spacing": "yellow",
            "min_width": "orange",
            "short": "red",
            "end_of_line": "magenta",
            "via_spacing": "cyan",
            "other": "white",
        }

    def render_state(
        self,
        congestion_map: np.ndarray,
        routing_layers: Dict[str, np.ndarray],
        drc_markers: List[Tuple[float, float, str]],
        netlist_stats: Dict,
        metrics: Optional[Dict] = None,
        iteration: int = 0,
        focus_region: Optional[Tuple[int, int, int, int]] = None,
    ) -> str:
        """Render the full routing state and return the PNG file path."""
        fig = plt.figure(figsize=(18, 12), dpi=150)
        fig.patch.set_facecolor("#1a1a2e")

        ax1 = plt.subplot(2, 3, 1)
        self._render_congestion(ax1, congestion_map)
        ax1.set_title(
            f"Global Congestion (Iter {iteration})",
            fontsize=11, color="white", fontweight="bold"
        )

        ax2 = plt.subplot(2, 3, 2)
        layer_m3 = (
            routing_layers.get("M3")
            if routing_layers.get("M3") is not None
            else routing_layers.get("metal3")
        )
        if layer_m3 is not None:
            self._render_routing_layer(ax2, layer_m3, "M3 (Horizontal)")
        else:
            ax2.text(
                0.5, 0.5, "M3 Not Available", ha="center", va="center",
                transform=ax2.transAxes, color="white", fontsize=12
            )
            ax2.set_facecolor("black")
        ax2.set_title("M3 Routing Layer", fontsize=11, color="white", fontweight="bold")

        ax3 = plt.subplot(2, 3, 3)
        layer_m4 = (
            routing_layers.get("M4")
            if routing_layers.get("M4") is not None
            else routing_layers.get("metal4")
        )
        if layer_m4 is not None:
            self._render_routing_layer(ax3, layer_m4, "M4 (Vertical)")
        else:
            ax3.text(
                0.5, 0.5, "M4 Not Available", ha="center", va="center",
                transform=ax3.transAxes, color="white", fontsize=12
            )
            ax3.set_facecolor("black")
        ax3.set_title("M4 Routing Layer", fontsize=11, color="white", fontweight="bold")

        ax4 = plt.subplot(2, 3, 4)
        self._render_drc_markers(ax4, drc_markers, congestion_map.shape)
        ax4.set_title(
            f"DRC Violations (Total: {len(drc_markers)})",
            fontsize=11, color="white", fontweight="bold"
        )

        ax5 = plt.subplot(2, 3, 5)
        self._render_composite(ax5, congestion_map, routing_layers)
        ax5.set_title(
            "Congestion + Routing Overlay",
            fontsize=11, color="white", fontweight="bold"
        )

        ax6 = plt.subplot(2, 3, 6)
        if focus_region:
            self._render_hotspot(ax6, congestion_map, focus_region)
        else:
            auto_hotspot = self._detect_hotspot(congestion_map)
            if auto_hotspot:
                self._render_hotspot(ax6, congestion_map, auto_hotspot)
                ax6.set_title(
                    f"Auto Hotspot {auto_hotspot}",
                    fontsize=11, color="white", fontweight="bold"
                )
            else:
                ax6.text(
                    0.5, 0.5, "No Significant Hotspot",
                    ha="center", va="center", transform=ax6.transAxes,
                    color="white", fontsize=12
                )
                ax6.set_facecolor("black")
                ax6.set_title("Hotspot Analysis", fontsize=11, color="white")

        stats_text = self._format_stats(netlist_stats, metrics)
        fig.text(
            0.5, 0.02, stats_text, ha="center", fontsize=9, color="white",
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor="#16213e", edgecolor="#e94560", alpha=0.9
            )
        )

        if metrics:
            title = (
                f"Routing State - Iteration {iteration} | "
                f"DRC: {metrics.get('drc_total', 'N/A')} | "
                f"WL: {metrics.get('wirelength', 0) / 1e6:.3f}mm | "
                f"Vias: {metrics.get('via_count', 'N/A')}"
            )
        else:
            title = f"Routing State - Iteration {iteration}"
        fig.suptitle(title, fontsize=14, color="#e94560", fontweight="bold", y=0.98)

        plt.tight_layout(rect=[0, 0.06, 1, 0.95])

        output_path = self.output_dir / f"state_iter_{iteration:03d}.png"
        plt.savefig(
            output_path, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor(), edgecolor="none"
        )
        plt.close(fig)

        return str(output_path)

    def render_diff(
        self, prev_state_path: str, curr_state_path: str, iteration: int
    ) -> str:
        """Render an amplified difference image between two state images."""
        prev_img = np.array(Image.open(prev_state_path).convert("RGB"))
        curr_img = np.array(Image.open(curr_state_path).convert("RGB"))

        min_h = min(prev_img.shape[0], curr_img.shape[0])
        min_w = min(prev_img.shape[1], curr_img.shape[1])
        prev_img = prev_img[:min_h, :min_w]
        curr_img = curr_img[:min_h, :min_w]

        diff = np.abs(curr_img.astype(float) - prev_img.astype(float))
        diff = np.clip(diff * 3, 0, 255).astype(np.uint8)

        fig, axes = plt.subplots(1, 3, figsize=(15, 5), dpi=150)
        fig.patch.set_facecolor("#1a1a2e")

        for ax, img, title in zip(
            axes,
            [prev_img, curr_img, diff],
            ["Previous State", "Current State", "Difference (3x Amplified)"]
        ):
            ax.imshow(img)
            ax.set_title(title, fontsize=12, color="white", fontweight="bold")
            ax.axis("off")
            ax.set_facecolor("#1a1a2e")

        plt.tight_layout()
        output_path = self.output_dir / f"diff_iter_{iteration:03d}.png"
        plt.savefig(
            output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor()
        )
        plt.close()

        return str(output_path)

    def render_policy_action(
        self, state_path: str, action: Dict, iteration: int
    ) -> str:
        """Overlay policy actions (avoid regions) onto a state image."""
        img = Image.open(state_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        width, height = img.size
        avoid_regions = action.get("avoid_regions", [])
        for region in avoid_regions:
            bbox = region.get("bbox")
            if bbox and len(bbox) == 4:
                x1 = int(bbox[0] * width)
                y1 = int(bbox[1] * height)
                x2 = int(bbox[2] * width)
                y2 = int(bbox[3] * height)
                draw.rectangle(
                    [x1, y1, x2, y2],
                    fill=(255, 0, 0, 80),
                    outline=(255, 0, 0, 200),
                    width=2
                )
                draw.text(
                    (x1 + 5, y1 + 5),
                    f"BLOCK: {region.get('reason', '')}",
                    fill=(255, 255, 255, 255)
                )

        img = Image.alpha_composite(img, overlay)
        output_path = self.output_dir / f"action_iter_{iteration:03d}.png"
        img.save(output_path)

        return str(output_path)

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _render_congestion(self, ax, congestion_map: np.ndarray):
        ax.set_facecolor("black")

        if congestion_map.max() > 0:
            im = ax.imshow(
                congestion_map, cmap=self.congestion_cmap,
                interpolation="nearest", vmin=0, vmax=max(congestion_map.max(), 1.0)
            )
            cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            cbar.ax.tick_params(colors="white")
            cbar.set_label("Overflow", color="white")
        else:
            ax.imshow(congestion_map, cmap="gray", interpolation="nearest")

        ax.set_xlabel("GCell X", color="white")
        ax.set_ylabel("GCell Y", color="white")
        ax.tick_params(colors="white")

        if congestion_map.max() > 0.5:
            flat_indices = np.argsort(congestion_map.ravel())[::-1][:5]
            coords = np.unravel_index(flat_indices, congestion_map.shape)
            for y, x in zip(coords[0], coords[1]):
                val = congestion_map[y, x]
                ax.annotate(
                    f"{val:.1f}", xy=(x, y), fontsize=7, color="white", ha="center",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="red", alpha=0.7)
                )

    def _render_routing_layer(self, ax, routing_map: np.ndarray, layer_name: str):
        ax.set_facecolor("black")
        ax.imshow(routing_map, cmap="Greens", interpolation="nearest", vmin=0, vmax=1)
        ax.set_xlabel("X", color="white")
        ax.set_ylabel("Y", color="white")
        ax.tick_params(colors="white")

        if routing_map.size > 0:
            density = routing_map.sum() / routing_map.size * 100
            ax.text(
                0.02, 0.98, f"Density: {density:.1f}%",
                transform=ax.transAxes, color="yellow", fontsize=9,
                verticalalignment="top",
                bbox=dict(boxstyle="round", facecolor="black", alpha=0.7)
            )

    def _render_drc_markers(
        self, ax, drc_markers: List[Tuple], map_shape: Tuple[int, int]
    ):
        ax.set_facecolor("black")
        h, w = map_shape
        ax.set_xlim(0, w)
        ax.set_ylim(0, h)

        if not drc_markers:
            ax.text(
                0.5, 0.5, "No DRC Violations", ha="center", va="center",
                transform=ax.transAxes, color="green", fontsize=14, fontweight="bold"
            )
            return

        type_groups: Dict[str, List[Tuple[float, float]]] = {}
        for marker in drc_markers:
            if len(marker) >= 3:
                x, y, vtype = marker[0], marker[1], marker[2]
            else:
                x, y = marker[0], marker[1]
                vtype = "unknown"
            type_groups.setdefault(vtype, []).append((x, y))

        for vtype, coords in type_groups.items():
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            color = self.drc_color_map.get(vtype, "white")
            ax.scatter(
                xs, ys, c=color, s=20, alpha=0.8,
                label=f"{vtype}: {len(coords)}", marker="x"
            )

        ax.legend(
            loc="upper right", facecolor="black", edgecolor="white",
            labelcolor="white", fontsize=8
        )
        ax.set_xlabel("X", color="white")
        ax.set_ylabel("Y", color="white")
        ax.tick_params(colors="white")

    def _render_composite(
        self, ax, congestion_map: np.ndarray, routing_layers: Dict[str, np.ndarray]
    ):
        ax.set_facecolor("black")
        h, w = congestion_map.shape

        composite = np.zeros((h, w, 3), dtype=np.float32)
        congestion_norm = np.clip(
            congestion_map / max(congestion_map.max(), 0.1), 0, 1
        )
        composite[:, :, 0] = congestion_norm

        routing_union = np.zeros((h, w), dtype=np.float32)
        for layer_name, layer_map in routing_layers.items():
            if layer_map is not None and layer_map.shape == (h, w):
                routing_union = np.maximum(routing_union, layer_map)
        composite[:, :, 1] = routing_union
        composite[:, :, 2] = np.clip(1 - congestion_norm, 0, 1) * 0.3

        ax.imshow(composite, interpolation="nearest")
        ax.set_xlabel("X", color="white")
        ax.set_ylabel("Y", color="white")
        ax.tick_params(colors="white")

        legend_text = "R=Congestion | G=Routing | B=LowCongestion"
        ax.text(
            0.5, -0.12, legend_text, transform=ax.transAxes,
            ha="center", color="white", fontsize=8
        )

    def _render_hotspot(
        self, ax, congestion_map: np.ndarray, region: Tuple[int, int, int, int]
    ):
        x1, y1, x2, y2 = region
        h, w = congestion_map.shape

        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            ax.text(
                0.5, 0.5, "Invalid Region", ha="center", va="center",
                transform=ax.transAxes, color="white"
            )
            return

        hotspot = congestion_map[y1:y2, x1:x2]

        ax.set_facecolor("black")
        im = ax.imshow(
            hotspot, cmap=self.congestion_cmap,
            interpolation="nearest", vmin=0, vmax=max(hotspot.max(), 0.5)
        )
        cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.tick_params(colors="white")

        ax.set_title(f"Region ({x1},{y1})-({x2},{y2})", color="white")
        ax.set_xlabel("X", color="white")
        ax.set_ylabel("Y", color="white")
        ax.tick_params(colors="white")

        if hotspot.max() > 0:
            max_y, max_x = np.unravel_index(np.argmax(hotspot), hotspot.shape)
            ax.plot(max_x, max_y, "w*", markersize=15)
            ax.annotate(
                f"Max: {hotspot.max():.1f}",
                xy=(max_x, max_y), xytext=(max_x + 5, max_y - 5),
                color="white", fontsize=9,
                arrowprops=dict(arrowstyle="->", color="white")
            )

    def _detect_hotspot(
        self, congestion_map: np.ndarray, window_size: int = 20
    ) -> Optional[Tuple[int, int, int, int]]:
        if congestion_map.max() < 0.3:
            return None

        h, w = congestion_map.shape
        max_y, max_x = np.unravel_index(np.argmax(congestion_map), congestion_map.shape)

        half = window_size // 2
        x1 = max(0, max_x - half)
        y1 = max(0, max_y - half)
        x2 = min(w, max_x + half)
        y2 = min(h, max_y + half)

        return (x1, y1, x2, y2)

    def _format_stats(self, stats: Dict, metrics: Optional[Dict]) -> str:
        lines = []

        lines.append(
            f"Nets: {stats.get('total_nets', 'N/A')} | "
            f"Pins: {stats.get('total_pins', 'N/A')} | "
            f"Components: {stats.get('component_count', 'N/A')}"
        )

        hf_count = len(stats.get("high_fanout_nets", []))
        ld_count = len(stats.get("long_distance_nets", []))
        lines.append(
            f"HighFanout(>50): {hf_count} | "
            f"LongNets(>500um): {ld_count} | "
            f"IO_Pins: {stats.get('io_pin_count', 'N/A')}"
        )

        if metrics:
            lines.append(
                f"DRC: {metrics.get('drc_total', 'N/A')} "
                f"(S:{metrics.get('drc_spacing', 0)} "
                f"W:{metrics.get('drc_min_width', 0)} "
                f"Sh:{metrics.get('drc_short', 0)}) | "
                f"WL: {metrics.get('wirelength', 0) / 1e6:.4f}mm | "
                f"Vias: {metrics.get('via_count', 'N/A')}"
            )

        return " | ".join(lines)


if __name__ == "__main__":
    renderer = VisualRenderer("/tmp/visual_test")
    print("VisualRenderer initialized successfully")
