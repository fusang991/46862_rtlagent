"""Apply an explicitly selected domain checklist to the existing backend."""

from pathlib import Path

from chipmate.backends import Backend

ROOT = Path(__file__).resolve().parents[2]


class SkillBackend(Backend):
    def __init__(self, backend, role: str, name: str):
        if name not in {"rtl_semantics_v1", "rtl_semantics_v2", "rtl_semantics_v3"} or role not in {"v", "p"}:
            raise ValueError(f"Unknown skill policy/role: {name}/{role}")
        self.backend = backend
        directory = ROOT / "skill" / name
        self.notes = ((directory / "common.md").read_text() + "\n\n"
                      + (directory / ("verilog.md" if role == "v" else "python.md")).read_text())

    def generate(self, prompt, n=1, temperature=0.6, max_tokens=4096):
        enriched = ("Use this general RTL semantics checklist when reviewing your design:\n"
                    + self.notes + "\n\nOriginal task and workflow instructions:\n" + prompt)
        return self.backend.generate(enriched, n, temperature, max_tokens)


def apply_skill(backend, role: str, name: str | None = None, *, question: str = ""):
    if name == "rtl_negative_edge_v1":
        from .verification_policy import statement_clock_edge
        name = "rtl_semantics_v1" if statement_clock_edge(question) == "negedge" else None
    return backend if name in (None, "upstream") else SkillBackend(backend, role, name)
