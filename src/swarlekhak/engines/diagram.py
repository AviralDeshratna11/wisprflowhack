"""Voice-to-Diagram: explicit node/edge operations only. Layout never adds connections."""
from __future__ import annotations

import copy
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field


class DiagramError(ValueError):
    pass


def canon_name(name: str) -> str:
    n = re.sub(r"\s+", " ", name.strip().strip(".,"))
    n = re.sub(r"^(node|the node)\s+", "", n, flags=re.I)
    if len(n) == 1:
        return n.upper()
    return n


@dataclass
class Diagram:
    nodes: dict[str, str] = field(default_factory=dict)       # id -> label
    edges: list[dict] = field(default_factory=list)           # {id, src, dst, label}
    next_id: int = 1
    _undo: list[dict] = field(default_factory=list, repr=False)

    # --- persistence
    def to_block(self, block_id: str) -> dict:
        return {"id": block_id, "type": "diagram", "nodes": dict(self.nodes), "edges": list(self.edges),
                "next_id": self.next_id, "adjacency": self.adjacency_text()}

    @classmethod
    def from_block(cls, b: dict) -> "Diagram":
        return cls(dict(b.get("nodes", {})), list(b.get("edges", [])), int(b.get("next_id", 1)))

    def _snapshot(self) -> None:
        self._undo.append({"nodes": copy.deepcopy(self.nodes), "edges": copy.deepcopy(self.edges),
                           "next_id": self.next_id})
        del self._undo[:-50]

    def undo(self) -> str:
        if not self._undo:
            raise DiagramError("There is nothing to undo in the diagram.")
        s = self._undo.pop()
        self.nodes, self.edges, self.next_id = s["nodes"], s["edges"], s["next_id"]
        return "Undone. " + self.summary()

    # --- lookup
    def find(self, name: str) -> str:
        c = canon_name(name).lower()
        hits = [nid for nid, label in self.nodes.items() if label.lower() == c]
        if not hits:
            raise DiagramError(f"There is no node called {canon_name(name)}.")
        if len(hits) > 1:
            raise DiagramError(f"More than one node is called {canon_name(name)}.")
        return hits[0]

    # --- operations
    def create(self, name: str) -> str:
        label = canon_name(name)
        if not label:
            raise DiagramError("Say a name for the node.")
        if any(lbl.lower() == label.lower() for lbl in self.nodes.values()):
            raise DiagramError(f"A node called {label} already exists. Choose a different name, for example {label} 2.")
        self._snapshot()
        nid = f"n{self.next_id}"
        self.next_id += 1
        self.nodes[nid] = label
        return f"Created node {label}."

    def rename(self, old: str, new: str) -> str:
        nid = self.find(old)
        label = canon_name(new)
        if any(lbl.lower() == label.lower() for k, lbl in self.nodes.items() if k != nid):
            raise DiagramError(f"A node called {label} already exists.")
        self._snapshot()
        before = self.nodes[nid]
        self.nodes[nid] = label
        return f"Renamed {before} to {label}."

    def delete(self, name: str) -> str:
        nid = self.find(name)
        self._snapshot()
        label = self.nodes.pop(nid)
        removed = [e for e in self.edges if nid in (e["src"], e["dst"])]
        self.edges = [e for e in self.edges if e not in removed]
        extra = f" and {len(removed)} connection{'s' if len(removed) != 1 else ''}" if removed else ""
        return f"Deleted node {label}{extra}."

    def connect(self, a: str, b: str, label: str | None = None) -> str:
        sa, sb = self.find(a), self.find(b)
        if any(e["src"] == sa and e["dst"] == sb for e in self.edges):
            raise DiagramError(f"{self.nodes[sa]} is already connected to {self.nodes[sb]}.")
        self._snapshot()
        eid = f"e{self.next_id}"
        self.next_id += 1
        self.edges.append({"id": eid, "src": sa, "dst": sb, "label": (label or "").strip()})
        lab = f", labelled {label.strip()}" if label else ""
        return f"Connected {self.nodes[sa]} to {self.nodes[sb]}{lab}."

    def disconnect(self, a: str, b: str) -> str:
        sa, sb = self.find(a), self.find(b)
        hit = [e for e in self.edges if e["src"] == sa and e["dst"] == sb]
        if not hit:
            raise DiagramError(f"{self.nodes[sa]} is not connected to {self.nodes[sb]}.")
        self._snapshot()
        self.edges.remove(hit[0])
        return f"Disconnected {self.nodes[sa]} from {self.nodes[sb]}."

    # --- read-back
    def list_nodes(self) -> str:
        if not self.nodes:
            return "The diagram has no nodes."
        return f"{len(self.nodes)} node{'s' if len(self.nodes) != 1 else ''}: " + ", ".join(self.nodes.values()) + "."

    def list_edges(self) -> str:
        if not self.edges:
            return "The diagram has no connections."
        parts = []
        for e in self.edges:
            s = f"{self.nodes[e['src']]} to {self.nodes[e['dst']]}"
            if e.get("label"):
                s += f" labelled {e['label']}"
            parts.append(s)
        return f"{len(parts)} connection{'s' if len(parts) != 1 else ''}: " + "; ".join(parts) + "."

    def summary(self) -> str:
        return self.list_nodes() + " " + self.list_edges()

    def adjacency_text(self) -> str:
        lines = []
        for nid, label in self.nodes.items():
            outs = [self.nodes[e["dst"]] + (f" ({e['label']})" if e.get("label") else "")
                    for e in self.edges if e["src"] == nid]
            lines.append(f"{label} -> {', '.join(outs) if outs else '(none)'}")
        return "\n".join(lines)

    def to_dot(self) -> str:
        def q(s: str) -> str:
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines = ["digraph answer {", "  rankdir=LR;", "  node [shape=box, fontname=\"Segoe UI\"];"]
        for nid, label in self.nodes.items():
            lines.append(f"  {nid} [label={q(label)}];")
        for e in self.edges:
            lab = f" [label={q(e['label'])}]" if e.get("label") else ""
            lines.append(f"  {e['src']} -> {e['dst']}{lab};")
        lines.append("}")
        return "\n".join(lines)

    def layout(self, width: float, height: float) -> dict[str, tuple[float, float]]:
        """Visual-only circular layout (used when Graphviz is not installed)."""
        ids = list(self.nodes)
        if not ids:
            return {}
        cx, cy = width / 2, height / 2
        rx, ry = max(60.0, width / 2 - 70), max(30.0, height / 2 - 26)
        if len(ids) == 1:
            return {ids[0]: (cx, cy)}
        return {nid: (cx + rx * math.cos(2 * math.pi * i / len(ids) - math.pi / 2),
                      cy + ry * math.sin(2 * math.pi * i / len(ids) - math.pi / 2)) for i, nid in enumerate(ids)}


def render_png_with_graphviz(dot_source: str, out_path: str) -> bool:
    exe = shutil.which("dot")
    if not exe:
        return False
    try:
        subprocess.run([exe, "-Tpng", "-o", out_path], input=dot_source.encode("utf-8"),
                       check=True, timeout=20, capture_output=True)
        return True
    except Exception:
        return False
