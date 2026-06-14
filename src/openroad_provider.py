"""
src/openroad_provider.py

OpenROAD implementation of the EDAProvider interface.

This provider wraps the existing RoutingToolkit so that the agent/controller
layers can depend on EDAProvider without changing legacy code paths.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np

from .eda_provider import EDAProvider
from .routing_toolkit import RoutingToolkit, RoutingMetrics, DRCViolation


class OpenROADProvider(EDAProvider):
    """
    EDAProvider implementation backed by OpenROAD.

    Parameters
    ----------
    toolkit : RoutingToolkit
        An initialized RoutingToolkit instance.  The provider delegates all
        OpenROAD-specific work to it.
    """

    def __init__(self, toolkit: RoutingToolkit):
        self.toolkit = toolkit

    def run_baseline_flow(
        self, def_file: str, guide_file: Optional[str] = None
    ) -> str:
        return self.toolkit.run_baseline_flow(def_file, guide_file)

    def run_incremental_route(
        self,
        def_file: str,
        net_list: Optional[List[str]] = None,
        output_name: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        kwargs: Dict = {}
        if output_name is not None:
            kwargs["output_name"] = output_name
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self.toolkit.run_incremental_route(def_file, **kwargs)

    def run_detailed_route(
        self, def_file: str, output_name: Optional[str] = None
    ) -> str:
        return self.toolkit.run_incremental_route(def_file, output_name=output_name)

    def extract_drc_report(self, def_file: str) -> List[DRCViolation]:
        # Use the congestion map if already computed; otherwise pass None.
        congestion = getattr(self.toolkit, "_last_congestion_map", None)
        return self.toolkit.extract_structured_drc_report(def_file, congestion)

    def extract_metrics(
        self, def_file: str, drc_violations: Optional[List[DRCViolation]] = None
    ) -> RoutingMetrics:
        drc_data = None
        if drc_violations is not None:
            drc_data = self.toolkit._violations_to_drc_data(drc_violations)
        return self.toolkit.extract_metrics(def_file, drc_data=drc_data)

    def extract_congestion_map(
        self, def_file: str, resolution: int = 256
    ) -> np.ndarray:
        cmap = self.toolkit.extract_congestion_map(def_file, resolution)
        self.toolkit._last_congestion_map = cmap
        return cmap

    def extract_routing_layers(
        self,
        def_file: str,
        layers: Optional[List[str]] = None,
        resolution: int = 512,
    ) -> Dict[str, np.ndarray]:
        return self.toolkit.extract_routing_layers(
            def_file, layers=layers, resolution=resolution
        )

    def rip_up_nets(self, def_file: str, net_list: List[str]) -> str:
        return self.toolkit.rip_up_nets(def_file, net_list)

    def set_routing_blockage(
        self,
        def_file: str,
        bbox: Tuple[int, int, int, int],
        layers: List[str],
        output_name: Optional[str] = None,
    ) -> str:
        return self.toolkit.set_routing_blockage(def_file, bbox, layers, output_name)
