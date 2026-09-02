# Canonical Q4.x numbering for the ten benchmark queries
from __future__ import annotations

# order of \begin{query} in chapters/implementation.tex
QNUM: dict[str, str] = {
    "both_ports":             "Q4.1",
    "harbour_entry":          "Q4.2",
    "harbor_entry":           "Q4.2",   # thesis spelling
    "clip_to_region":         "Q4.3",
    "fleet_summary":          "Q4.4",
    "bounding_box":           "Q4.5",
    "speed_profile":          "Q4.6",
    "position_interpolation": "Q4.7",
    "nearest_approach":       "Q4.8",
    "collision":              "Q4.9",
    "encounter_zone":         "Q4.10",
}

# canonical display order (Q4.1 .. Q4.10)
ORDER: list[str] = [
    "both_ports", "harbour_entry", "clip_to_region", "fleet_summary", "bounding_box",
    "speed_profile", "position_interpolation", "nearest_approach", "collision",
    "encounter_zone",
]

LABEL: dict[str, str] = {
    "both_ports": "two-port co-visit",
    "harbour_entry": "entry into a region",
    "clip_to_region": "presence in a region",
    "fleet_summary": "total distance travelled",
    "bounding_box": "mean per-vessel extent",
    "speed_profile": "median top speed",
    "position_interpolation": "position at an instant",
    "nearest_approach": "closest approach",
    "collision": "pairs within 300 m",
    "encounter_zone": "pairs within 500 m",
}

def qnum(name: str) -> str:
    return QNUM.get(name, name)

def sort_key(name: str) -> int:
    try:
        return ORDER.index(name)
    except ValueError:
        return len(ORDER)

def add_qnum(df, col: str = "query", *, sort: bool = True, as_index: bool = False):
    out = df.copy()
    src = out.index if col not in out.columns else out[col]
    out["Q"] = [qnum(q) for q in src]
    out["_k"] = [sort_key(q) for q in src]
    if sort:
        out = out.sort_values("_k")
    out = out.drop(columns="_k")
    if as_index:
        out = out.set_index("Q")
    return out
