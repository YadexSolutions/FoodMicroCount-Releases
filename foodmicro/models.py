from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
import copy
import math
import json
import hashlib
import uuid
import re


def enumeration_scope(test='', medium='', profile=None):
    """Keep old presence/absence records readable without enumerating them."""
    return not (re.search(r'\bsalmonella\b',str(test),re.I) or
                str(medium).strip().casefold() in ('xld','oxford-listeria') or
                bool((profile or {}).get('qualitative_only')))


def uid() -> str:
    return uuid.uuid4().hex


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Boundary:
    """Ellipse in ORIGINAL image pixels. Angle in degrees, positive clockwise."""
    cx: float
    cy: float
    rx: float
    ry: float
    angle: float = 0.0
    score: float = 0.0
    source: str = "automatic"

    def contains(self, x: float, y: float, inset: float = 0.0) -> bool:
        if not 0 <= inset < 1 or self.rx <= 0 or self.ry <= 0:
            return False
        a = math.radians(self.angle)
        dx, dy = x - self.cx, y - self.cy
        u = dx * math.cos(a) + dy * math.sin(a)
        v = -dx * math.sin(a) + dy * math.cos(a)
        return (u / (self.rx * (1-inset)))**2 + (v / (self.ry * (1-inset)))**2 <= 1


@dataclass
class Detection:
    id: str
    x: float
    y: float
    radius: float = 3.0
    area: float = 0.0
    category: str = "presumptive"
    included: bool = True
    origin: str = "automatic"
    score: float = 0.0  # heuristic ranking, NEVER a calibrated probability
    features: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlateMeta:
    sample_id: str = ""
    sample_name: str = ""
    test_id: str = ""
    test: str = "APC"
    medium: str = "PCA"
    method_id: str = "basic_exposure_v1"
    date: str = ""
    analyst: str = ""
    sample_amount: float = 10.0
    first_diluent_ml: float = 90.0
    initial_fraction_override: float | None = None
    additional_dilution_factor: float = 1.0
    plated_ml: float = 1.0
    original_unit: str = "g"
    plating: str = "pour"
    replicate: str = "1"
    manufacturer: str = "not recorded"
    medium_lot: str = ""
    incubation: str = ""
    confirmation_tested: int | None = None
    confirmation_positive: int | None = None
    observation: str = "numeric"
    notes: str = ""

    @property
    def initial_fraction(self) -> float:
        if self.initial_fraction_override is not None:
            return self.initial_fraction_override
        return self.sample_amount / (self.sample_amount + self.first_diluent_ml)

    @property
    def total_fraction(self) -> float:
        return self.initial_fraction / self.additional_dilution_factor

    def validate(self) -> None:
        from .calculations import require_positive
        require_positive(self.sample_amount, "Sample amount")
        if not math.isfinite(self.first_diluent_ml) or self.first_diluent_ml < 0:
            raise ValueError("First diluent must be a finite nonnegative volume")
        require_positive(self.initial_fraction, "Initial fraction")
        if self.initial_fraction > 1:
            raise ValueError("Initial dilution fraction must be <=1")
        require_positive(self.additional_dilution_factor, "Additional dilution factor")
        if self.additional_dilution_factor < 1:
            raise ValueError("Additional inverse dilution factor must be >=1")
        require_positive(self.plated_ml, "Plated volume")
        if self.original_unit not in ("g", "mL"):
            raise ValueError("Original sample unit must be g or mL")
        if (self.confirmation_tested is None) != (self.confirmation_positive is None):
            raise ValueError("Enter both tested and confirmed, or leave both blank")
        if self.confirmation_tested is not None:
            if not isinstance(self.confirmation_tested, int) or self.confirmation_tested <= 0:
                raise ValueError("Confirmed counts require a positive integer tested count")
            if not isinstance(self.confirmation_positive, int) or not 0 <= self.confirmation_positive <= self.confirmation_tested:
                raise ValueError("Confirmed must be an integer from 0 through tested")


@dataclass
class Plate:
    id: str
    image_id: str
    boundary: Boundary
    meta: PlateMeta = field(default_factory=PlateMeta)
    automatic: list[Detection] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    review_status: str = "draft"
    reviewer: str = ""
    reviewed_at: str = ""
    review_fingerprint: str = ""
    boundary_reviewed: bool = False
    quality_acknowledged: bool = False
    count_mode: str = 'automatic'
    count_layers: list[dict[str, Any]] = field(default_factory=list)
    dataset_role: str = 'development'
    _undo: list[dict] = field(default_factory=list, repr=False)
    _redo: list[dict] = field(default_factory=list, repr=False)

    @property
    def count(self) -> int:
        return sum(1 for d in self.detections if d.included)

    @property
    def automatic_count(self) -> int:
        return sum(1 for d in self.automatic if d.included)

    def retain_layer(self, actor: str, reason: str, independent: bool=False) -> str:
        if not actor.strip() or not reason.strip():raise ValueError('Analyst and reason required')
        layer={'id':uid(),'time':timestamp(),'actor':actor,'reason':reason,'mode':self.count_mode,
               'independent_reference':bool(independent and self.count_mode=='manual'),
               'automatic':[asdict(d) for d in self.automatic],'detections':[asdict(d) for d in self.detections],
               'parameters':copy.deepcopy(self.parameters),'quality':copy.deepcopy(self.quality),'exclusions':copy.deepcopy(self.exclusions),'boundary':asdict(self.boundary),
               'count':self.count,'review_status':self.review_status,'dataset_role':self.dataset_role}
        self.count_layers.append(layer);return layer['id']

    def begin_manual(self, actor: str, reason: str) -> None:
        if not actor.strip() or not reason.strip():raise ValueError('Analyst and reason required')
        self.retain_layer(actor,reason)
        before=[asdict(d) for d in self.detections]
        self.automatic=[];self.detections=[];self.exclusions=[];self._undo.clear();self._redo.clear()
        self.count_mode='manual';self.parameters={'algorithm_version':'manual-0.1.2','counted_medium':self.meta.medium}
        self.quality={'warnings':['Independent manual layer: inspect the full plate; no automatic detections were shown in this layer.'],'settings_changed':False}
        self._record('manual_layer',actor,reason,before,[])

    def restore_layer(self, layer_id: str, actor: str, reason: str) -> None:
        layer=next(x for x in self.count_layers if x['id']==layer_id)
        self.retain_layer(actor,reason)
        self.automatic=[Detection(**x) for x in layer['automatic']];self.detections=[Detection(**x) for x in layer['detections']]
        self.parameters=copy.deepcopy(layer['parameters']);self.quality=copy.deepcopy(layer['quality']);self.count_mode=layer['mode']
        self.exclusions=copy.deepcopy(layer.get('exclusions',[]));self.boundary=Boundary(**layer.get('boundary',asdict(self.boundary)))
        self._undo.clear();self._redo.clear();self._record('restore_layer',actor,reason,layer_id,self.count)

    def _snapshot(self) -> dict:
        return {"detections": copy.deepcopy(self.detections), "exclusions": copy.deepcopy(self.exclusions)}

    def invalidate_review(self) -> None:
        self.review_status, self.reviewer, self.reviewed_at = "draft", "", ""
        self.review_fingerprint = ""
        self.quality_acknowledged = False

    def fingerprint(self) -> str:
        payload={"meta":asdict(self.meta),"boundary":asdict(self.boundary),
                 "detections":[asdict(d) for d in self.detections],"parameters":self.parameters,"exclusions":self.exclusions}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()

    def _record(self, action: str, actor: str, reason: str, before: Any, after: Any) -> None:
        if not actor.strip() or not reason.strip():
            raise ValueError("Analyst identity and a correction reason are required")
        self.edits.append({"id": uid(), "plate_id":self.id,"time": timestamp(), "action": action,
                           "actor": actor, "reason": reason, "before": before, "after": after})
        self.invalidate_review()

    def edit(self, action: str, ids: list[str], actor: str, reason: str,
             points: list[tuple[float, float]] | None = None, category: str = "presumptive") -> None:
        """Atomic, reversible object corrections. Original automatic detections never mutate."""
        if not actor.strip() or not reason.strip():
            raise ValueError("Analyst identity and correction reason required")
        if category not in ("presumptive", "yeast-like", "mould-like", "suspicious", "atypical", "artefact", "uncertain"):
            raise ValueError("Unknown visual classification; species confirmation is separate")
        points = points or []
        targets = [d for d in self.detections if d.id in ids]
        if len(targets) != len(set(ids)):
            raise ValueError("Detection identifier not found")
        if action in ("add", "split") and (not points or any(not self.boundary.contains(x,y) for x,y in points)):
            raise ValueError("New colony positions must be inside the plate")
        if action == "split" and (len(targets) != 1 or len(points) < 2):
            raise ValueError("Split requires one detection and at least two new centres")
        if action == "merge" and len(targets) < 2:
            raise ValueError("Merge requires at least two detections")
        if action in ("remove", "reclassify") and not targets:
            raise ValueError("Select a detection first")
        if action not in ("add", "remove", "split", "merge", "reclassify"):
            raise ValueError("Unsupported edit")
        before = self._snapshot()
        self._undo.append(before); self._redo.clear()
        if action in ("remove", "split", "merge"):
            self.detections = [d for d in self.detections if d.id not in ids]
        if action == "reclassify":
            for d in targets:
                d.category, d.included = category, category != "artefact"
        elif action == "merge":
            n = len(targets)
            points = [(sum(d.x for d in targets)/n, sum(d.y for d in targets)/n)]
        if action in ("add", "split", "merge"):
            for x, y in points:
                self.detections.append(Detection(uid(), x, y, category=category,
                                                included=category != "artefact", origin="manual"))
        if self.count_mode!='manual':self.count_mode='assisted'
        self._record(action, actor, reason,
                     [asdict(d) for d in before["detections"]], [asdict(d) for d in self.detections])

    def add_exclusion(self, rect: tuple[float,float,float,float], kind: str, actor: str, reason: str) -> None:
        if not actor.strip() or not reason.strip():
            raise ValueError("Identity and reason required")
        if kind not in ("artefact", "spreading", "unsuitable"):
            raise ValueError("Unknown exclusion kind")
        x1,y1,x2,y2 = rect
        if not all(math.isfinite(x) for x in rect) or x1 >= x2 or y1 >= y2:
            raise ValueError("Draw a nonempty rectangle")
        before = self._snapshot(); self._undo.append(before); self._redo.clear()
        self.exclusions.append({"id":uid(),"rect":list(rect),"kind":kind,"reason":reason})
        for d in self.detections:
            if x1 <= d.x <= x2 and y1 <= d.y <= y2:
                d.included, d.category = False, "artefact" if kind == "artefact" else "uncertain"
        self._record("exclude_region", actor, reason,
                     {"detections":[asdict(d) for d in before["detections"]],"exclusions":before["exclusions"]},
                     {"rect":list(rect),"kind":kind,"detections":[asdict(d) for d in self.detections]})

    def undo(self, actor: str, reason: str) -> bool:
        if not self._undo: return False
        if not actor.strip() or not reason.strip(): raise ValueError("Identity and reason required")
        current = self._snapshot(); state = self._undo.pop(); self._redo.append(current)
        self.detections, self.exclusions = state["detections"], state["exclusions"]
        self._record("undo",actor,reason,[asdict(d) for d in current["detections"]],[asdict(d) for d in self.detections]); return True

    def redo(self, actor: str, reason: str) -> bool:
        if not self._redo: return False
        if not actor.strip() or not reason.strip(): raise ValueError("Identity and reason required")
        current = self._snapshot(); state = self._redo.pop(); self._undo.append(current)
        self.detections, self.exclusions = state["detections"],state["exclusions"]
        self._record("redo",actor,reason,[asdict(d) for d in current["detections"]],[asdict(d) for d in self.detections]); return True

    def mark_reviewed(self, actor: str) -> None:
        self.meta.validate()
        if not actor.strip() or not self.meta.sample_id.strip() or not self.meta.test_id.strip():
            raise ValueError("Sample ID, test ID and analyst required")
        if not self.boundary_reviewed:
            raise ValueError("Check and accept the plate boundary first")
        if self.quality.get("warnings") and not self.quality_acknowledged:
            raise ValueError("Acknowledge image quality warnings before review")
        numeric=self.meta.observation in ('numeric','estimated','zero')
        if numeric and not self.parameters.get("algorithm_version"):
            raise ValueError("Run detection before reviewing the plate")
        if numeric and self.quality.get("settings_changed"):
            raise ValueError("Settings/boundary changed; re-run detection")
        if numeric and self.parameters.get("counted_medium",self.meta.medium)!=self.meta.medium:
            raise ValueError("Detection medium differs from the plate record")
        if numeric and self.meta.confirmation_tested is not None and self.meta.confirmation_tested > self.count:
            raise ValueError("Tested colonies cannot exceed included colonies")
        self.edits.append({"id":uid(),"time":timestamp(),"action":"review","actor":actor,
                           "reason":"Analyst checked plate observation and metadata","after":{"count":self.count if numeric else None,"observation":self.meta.observation}})
        self.review_status, self.reviewer, self.reviewed_at = "reviewed", actor, timestamp()
        self.review_fingerprint = self.fingerprint()


@dataclass
class Project:
    id: str = field(default_factory=uid)
    created_at: str = field(default_factory=timestamp)
    laboratory: str = ""
    images: dict[str, dict[str, Any]] = field(default_factory=dict)
    plates: list[Plate] = field(default_factory=list)
    imported_workbooks: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = 1

    def to_dict(self) -> dict:
        result=asdict(self)
        for p in result["plates"]:
            p.pop("_undo",None);p.pop("_redo",None)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported project schema")
        x=copy.deepcopy(data);plates=[]
        for p in x.pop("plates"):
            p["boundary"]=Boundary(**p["boundary"]);p["meta"]=PlateMeta(**p["meta"])
            for key in ("automatic","detections"):p[key]=[Detection(**d) for d in p[key]]
            plates.append(Plate(**p))
        return cls(plates=plates,**x)

