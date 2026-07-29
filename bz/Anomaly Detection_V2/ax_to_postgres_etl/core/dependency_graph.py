"""Dependency graph visualization for ETL pipeline."""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional
from collections import defaultdict


@dataclass
class GraphNode:
    """A node in the dependency graph."""
    name: str
    table_name: str
    status: str = "pending"
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)


class DependencyGraph:
    """
    Directed acyclic graph for ETL dependencies.

    Features:
    - Topological sorting
    - Cycle detection
    - DOT format export
    - ASCII visualization
    """

    def __init__(self):
        self._nodes: Dict[str, GraphNode] = {}

    def add_node(self, name: str, table_name: str, dependencies: Optional[List[str]] = None):
        """Add a node to the graph."""
        if name not in self._nodes:
            self._nodes[name] = GraphNode(name=name, table_name=table_name)

        self._nodes[name].dependencies = dependencies or []

        # Update dependents
        for dep in self._nodes[name].dependencies:
            if dep in self._nodes:
                self._nodes[dep].dependents.append(name)

    def detect_cycles(self) -> List[List[str]]:
        """Detect cycles in the graph."""
        visited = set()
        path = []
        cycles = []

        def dfs(node: str):
            if node in path:
                cycle_start = path.index(node)
                cycles.append(path[cycle_start:] + [node])
                return
            if node in visited:
                return

            visited.add(node)
            path.append(node)

            for dep in self._nodes.get(node, GraphNode("", "")).dependencies:
                dfs(dep)

            path.pop()

        for node in self._nodes:
            dfs(node)

        return cycles

    def topological_sort(self) -> List[str]:
        """Return nodes in topological order."""
        visited = set()
        order = []

        def dfs(node: str):
            if node in visited:
                return
            visited.add(node)

            for dep in self._nodes.get(node, GraphNode("", "")).dependencies:
                dfs(dep)

            order.append(node)

        for node in self._nodes:
            dfs(node)

        return order

    def get_roots(self) -> List[str]:
        """Get nodes with no dependencies."""
        return [n for n, node in self._nodes.items() if not node.dependencies]

    def get_leaves(self) -> List[str]:
        """Get nodes with no dependents."""
        return [n for n, node in self._nodes.items() if not node.dependents]

    def to_dot(self) -> str:
        """Export as DOT format for Graphviz."""
        lines = ["digraph ETL {"]
        lines.append("  rankdir=LR;")
        lines.append("  node [shape=box];")

        for name, node in self._nodes.items():
            label = f"{name}\\n{node.table_name}"
            lines.append(f'  "{name}" [label="{label}"];')

        for name, node in self._nodes.items():
            for dep in node.dependencies:
                lines.append(f'  "{dep}" -> "{name}";')

        lines.append("}")
        return "\n".join(lines)

    def to_ascii(self) -> str:
        """Generate ASCII visualization."""
        lines = []
        lines.append("DEPENDENCY GRAPH")
        lines.append("=" * 60)

        roots = self.get_roots()
        visited = set()

        def print_tree(node: str, indent: int = 0):
            if node in visited:
                return
            visited.add(node)

            prefix = "  " * indent + ("└── " if indent > 0 else "")
            status = self._nodes[node].status
            table = self._nodes[node].table_name
            lines.append(f"{prefix}{node} ({table}) [{status}]")

            for dep in self._nodes[node].dependents:
                print_tree(dep, indent + 1)

        for root in roots:
            print_tree(root)
            lines.append("")

        return "\n".join(lines)

    def to_mermaid(self) -> str:
        """Export as Mermaid diagram."""
        lines = ["graph LR"]

        for name, node in self._nodes.items():
            safe_name = name.replace("-", "_").replace(" ", "_")
            lines.append(f"    {safe_name}[{name}]")

        for name, node in self._nodes.items():
            for dep in node.dependencies:
                safe_dep = dep.replace("-", "_").replace(" ", "_")
                safe_name = name.replace("-", "_").replace(" ", "_")
                lines.append(f"    {safe_dep} --> {safe_name}")

        return "\n".join(lines)

    def summary(self) -> str:
        """Generate graph summary."""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"DEPENDENCY GRAPH SUMMARY")
        lines.append(f"{'='*60}")
        lines.append(f"  Total nodes: {len(self._nodes)}")
        lines.append(f"  Root nodes:  {len(self.get_roots())}")
        lines.append(f"  Leaf nodes:  {len(self.get_leaves())}")

        cycles = self.detect_cycles()
        if cycles:
            lines.append(f"  ⚠ Cycles detected: {len(cycles)}")
            for cycle in cycles[:3]:
                lines.append(f"    - {' -> '.join(cycle)}")
        else:
            lines.append(f"  ✓ No cycles detected")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
