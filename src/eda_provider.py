"""
src/eda_provider.py

Abstract EDA backend provider interface.  The routing agent and policy executor
depend on this interface, not on any specific EDA tool.

OpenROAD is the first concrete implementation; other providers (commercial EDA
flows) can be added later by subclassing EDAProvider.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
import numpy as np

from .routing_toolkit import RoutingMetrics, DRCViolation


class EDAProvider(ABC):
    """Abstract interface for EDA backend operations."""

    @abstractmethod
    def run_baseline_flow(
        self, def_file: str, guide_file: Optional[str] = None
    ) -> str:
        """Run the baseline global + detailed routing flow."""
        ...

    @abstractmethod
    def run_incremental_route(
        self,
        def_file: str,
        net_list: Optional[List[str]] = None,
        output_name: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Run detailed routing on a DEF (optionally only the given nets)."""
        ...

    @abstractmethod
    def run_detailed_route(
        self, def_file: str, output_name: Optional[str] = None
    ) -> str:
        """Run detailed route and return the resulting DEF path."""
        ...

    @abstractmethod
    def extract_drc_report(self, def_file: str) -> List[DRCViolation]:
        """Return a list of structured DRC violations."""
        ...

    @abstractmethod
    def extract_metrics(self, def_file: str) -> RoutingMetrics:
        """Return routing metrics (DRC, wirelength, via count, ...)."""
        ...

    @abstractmethod
    def extract_congestion_map(
        self, def_file: str, resolution: int = 256
    ) -> np.ndarray:
        """Return a congestion heatmap of the requested resolution."""
        ...

    @abstractmethod
    def extract_routing_layers(
        self,
        def_file: str,
        layers: Optional[List[str]] = None,
        resolution: int = 512,
    ) -> Dict[str, np.ndarray]:
        """Return binary routing-layer images."""
        ...

    @abstractmethod
    def rip_up_nets(self, def_file: str, net_list: List[str]) -> str:
        """Remove routing for the given nets and return the new DEF path."""
        ...

    @abstractmethod
    def set_routing_blockage(
        self,
        def_file: str,
        bbox: Tuple[int, int, int, int],
        layers: List[str],
        output_name: Optional[str] = None,
    ) -> str:
        """Insert a routing blockage into the DEF and return the new path."""
        ...
