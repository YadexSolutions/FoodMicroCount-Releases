"""Method-explicit calculations. Count selection NEVER uses visual species guesses."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
import math
from statistics import mean, stdev


METHODS = {
    "basic_exposure_v1": {
        "name":"Count ÷ (dilution × volume)",
        "min":25,"max":250,"convention":"single","verified_source":"Arithmetic relationship only",
        "reporting_approved":False,"rounding":"half_up"},
    "handbook_apha_2026": {
        "name":"Handbook APHA-style 2026 (educational; lab approval pending)",
        "min":25,"max":250,"convention":"handbook","verified_source":"User handbook, Chapters 2–3",
        "reporting_approved":False,"rounding":"half_up"},
    "handbook_fungi_2026": {
        "name":"Handbook fungi 2026 (10–150; educational)",
        "min":10,"max":150,"convention":"handbook","verified_source":"User handbook; exact fungal method pending",
        "reporting_approved":False,"rounding":"half_up"},
    "bam_apc_2026_numeric": {
        "name":"FDA BAM APC January 2026 (numeric observations; TNTC MLE unavailable)",
        "min":15,"max":300,"convention":"pooled","verified_source":"FDA BAM Ch.3 January 2026 pp.5–8",
        "reporting_approved":False,"rounding":"half_even","test":"APC"},
    "iso7218_2007_2013_arithmetic": {
        "name":"ISO 7218:2007/Amd 2013 arithmetic sandbox (selection NOT verified)",
        "min":10,"max":300,"convention":"historical_weighted","verified_source":"User handbook Chapter 4; full standard unavailable",
        "reporting_approved":False,"rounding":"half_up"},
    "iso7218_2024": {
        "name":"ISO 7218:2024 — blocked until controlled calculation clauses verified",
        "min":None,"max":None,"convention":"blocked","verified_source":"ISO official edition metadata only",
        "reporting_approved":False,"rounding":"half_even"}
}


def require_positive(value: float, label: str) -> float:
    if not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return value


def initial_fraction(amount: float, diluent_ml: float) -> float:
    require_positive(amount,"Sample amount")
    if not math.isfinite(diluent_ml) or diluent_ml < 0: raise ValueError("Diluent must be nonnegative")
    return amount/(amount+diluent_ml)


def total_fraction(first: float, additional_inverse: float) -> float:
    require_positive(first,"Initial fraction");require_positive(additional_inverse,"Additional factor")
    if first>1 or additional_inverse<1:raise ValueError("Invalid dilution record")
    return first/additional_inverse


def round_sig(value: float, digits: int=2, mode: str="half_up") -> float:
    if not math.isfinite(value):raise ValueError("Cannot round a nonfinite value")
    if value==0:return 0.0
    if not isinstance(digits,int) or not 1<=digits<=12:raise ValueError("Invalid significant figures")
    d=Decimal(str(value)); quantum=Decimal(1).scaleb(d.adjusted()-digits+1)
    return float(d.quantize(quantum,rounding=ROUND_HALF_EVEN if mode=="half_even" else ROUND_HALF_UP))


@dataclass
class Observation:
    id: str
    count: float | None
    dilution: float
    volume_ml: float
    state: str="numeric"
    tested: int | None=None
    confirmed: int | None=None

    @property
    def exposure(self) -> float:
        return self.dilution*self.volume_ml

    def validate(self) -> None:
        require_positive(self.dilution,"Dilution fraction");require_positive(self.volume_ml,"Plated volume")
        if self.dilution>1:raise ValueError("Dilution fraction must be <=1")
        require_positive(self.exposure,"Original sample exposure")
        if self.state not in ("numeric","zero","TNTC","spreading","contaminated","unreadable","missing","estimated","unsuitable"):
            raise ValueError("Unknown observation state")
        if self.state in ("numeric","zero","estimated"):
            if self.count is None or not math.isfinite(self.count) or self.count<0:raise ValueError("Numeric observations require nonnegative counts")
            if self.state=="zero" and self.count!=0:raise ValueError("Zero state must have zero colonies")
            if self.state!="estimated" and self.count!=int(self.count):raise ValueError("Colony count must be an integer")
        if (self.tested is None)!=(self.confirmed is None):raise ValueError("Incomplete confirmation record")
        if self.tested is not None:
            if not isinstance(self.tested,int) or self.tested<=0 or not isinstance(self.confirmed,int) or not 0<=self.confirmed<=self.tested:
                raise ValueError("Invalid confirmation counts")
            if self.count is None or self.tested>self.count:raise ValueError("Tested exceeds presumptive colonies")

    def adjusted(self, whole: bool=False) -> float:
        self.validate()
        n=float(self.count)
        if self.tested is not None:n*=self.confirmed/self.tested
        return float(Decimal(str(n)).quantize(Decimal(1),rounding=ROUND_HALF_UP)) if whole else n


@dataclass
class Result:
    value: float | None
    qualifier: str
    unit: str
    method_id: str
    included: list[str]=field(default_factory=list)
    excluded: list[str]=field(default_factory=list)
    warnings: list[str]=field(default_factory=list)
    formula: str=""
    selection: str=""
    status: str="calculated"
    rounded: float | None=None
    log10: float | None=None
    confirmation_status: str="presumptive or unadjusted"

    def finalise(self) -> "Result":
        if self.value is not None:
            mode=METHODS.get(self.method_id,{}).get("rounding","half_up")
            self.rounded=round_sig(self.value,2,mode)
            if self.value>0 and self.qualifier in ("exact","estimated"):
                self.log10=math.log10(self.value)
        return self

    def text(self) -> str:
        if self.value is None:return self.warnings[0] if self.warnings else 'No numerical result'
        prefix={"less_than":"< ","greater_than":"> "}.get(self.qualifier,"")
        extra=" (estimated)" if self.qualifier=="estimated" else ""
        value=self.value
        if value and (abs(value)>=1000 or abs(value)<.01):
            significand,exponent=f'{value:.5e}'.split('e')
            number=f'{float(significand):g} × 10'+str(int(exponent)).translate(str.maketrans('-0123456789','⁻⁰¹²³⁴⁵⁶⁷⁸⁹'))
        else:number=f'{value:.6g}'
        return f'{prefix}{number} {self.unit}{extra}'


def calculate(observations: list[Observation], method_id: str="basic_exposure_v1", unit: str="CFU/g") -> Result:
    if method_id not in METHODS:raise ValueError("Unknown method version")
    if unit not in ("CFU/g","CFU/mL"):raise ValueError("Select original sample basis")
    if len({p.id for p in observations})!=len(observations):raise ValueError("Duplicate plate identifier")
    for p in observations:p.validate()
    m=METHODS[method_id];convention=m["convention"]
    def blocked(msg):return Result(None,"requires_review",unit,method_id,warnings=[msg],status="requires_review")
    if convention=="blocked":return blocked("ISO 7218:2024 calculation clauses have not been verified; no fallback method used")
    usable=[p for p in observations if p.state in ("numeric","zero","estimated")]
    excluded=[p.id for p in observations if p not in usable]
    if not usable:return blocked("No usable numerical plate observations")
    if convention=="pooled" and any(p.state=="TNTC" for p in observations):
        return blocked("FDA BAM 2026 requires censored-count maximum likelihood for TNTC; this solver is not implemented")
    if any(p.tested is not None for p in usable) and not all(p.tested is not None for p in usable):
        return blocked("Confirmation information is missing on some plates; do not mix adjusted and unadjusted counts")
    if convention=="pooled" and any(p.tested is not None for p in usable):
        return blocked("The implemented FDA APC numeric route does not apply confirmation adjustments")
    groups={}
    for p in usable:groups.setdefault(p.dilution,[]).append(p)
    ds=sorted(groups,reverse=True)
    if convention in ("handbook","historical_weighted"):
        for a,b in zip(ds,ds[1:]):
            ca=mean(p.count for p in groups[a]);cb=mean(p.count for p in groups[b])
            if cb>=m["min"] and cb>ca*1.2:
                return blocked("Higher dilution has disproportionately more colonies; investigate labelling, dilution or inhibition")
    # An observed zero does not constitute a measured zero concentration.
    if all(p.count==0 for p in usable):
        first=groups[ds[0]]
        if len({p.volume_ml for p in first})!=1:return blocked("Unequal volumes need a method-approved no-growth bound")
        limit=1/first[0].exposure
        return Result(limit,"less_than",unit,method_id,included=[p.id for p in first],excluded=excluded,
                      warnings=["One-colony, one-plate reporting bound; not a statistical detection limit"],
                      formula=f"1/({first[0].volume_ml} × {ds[0]})",selection="No growth at least diluted inoculated level").finalise()
    if any(p.tested is not None for p in usable) and all(p.adjusted()==0 for p in usable):
        return blocked("No tested presumptive colonies confirmed; a method-specific confirmation bound is required")
    def single(group,qualifier="exact",selection="Retained dilution"):
        c=sum(p.adjusted() for p in group);e=sum(p.exposure for p in group)
        warnings=[]
        if any(p.state=="estimated" or not m["min"]<=p.count<=m["max"] for p in group):
            warnings.append("Some retained raw counts lie outside the configured counting range")
        result=Result(c/e,qualifier,unit,method_id,[p.id for p in group],excluded=excluded,
                      warnings=warnings,formula=f"{c}/Σ(v × d) = {c}/{e}",selection=selection)
        if all(p.tested is not None for p in group):result.confirmation_status="confirmation-adjusted"
        return result.finalise()
    if convention=="single":
        if len(ds)!=1:return blocked("Exposure v1 accepts one retained dilution; explicitly choose another method for consecutive dilutions")
        q="exact" if all(m["min"]<=p.count<=m["max"] and p.state!="estimated" for p in usable) else "estimated"
        return single(usable,q)
    if convention=="pooled":
        q="exact" if all(m["min"]<=p.count<=m["max"] and p.state!="estimated" for p in usable) else "estimated"
        return single(usable,q,"FDA numeric-only Poisson estimate: all usable numeric observations pooled by original-sample exposure")
    if convention=="historical_weighted":
        if len(ds)>2 or (len(ds)==2 and not math.isclose(ds[1]/ds[0],0.1,rel_tol=1e-6)):
            return blocked("Historical arithmetic sandbox requires one dilution or two consecutive decimal dilutions")
        if len({p.volume_ml for p in usable})!=1:return blocked("Historical weighted equation requires equal plated volumes")
        c=sum(p.adjusted(whole=True) for p in usable);e=sum(p.exposure for p in usable)
        return Result(c/e,"estimated",unit,method_id,[p.id for p in usable],excluded,
                      ["Arithmetic only; historical ISO selection, exception and reporting clauses NOT independently verified"],
                      f"Σ adjusted whole counts / Σ(v × d) = {c}/{e}","Explicitly retained plates",status="requires_method_review").finalise()
    # Educational handbook convention. Only the handbook's declared rules, never FDA/ISO rules.
    if any(len({p.volume_ml for p in g})!=1 for g in groups.values()):
        return blocked("Handbook equal-volume replicate convention cannot pool unequal volumes")
    eligible=[d for d in ds if any(m["min"]<=p.count<=m["max"] for p in groups[d])]
    if len(eligible)>2:return blocked("More than two countable dilution levels; select under the approved method")
    if len(eligible)==2:
        d1,d2=eligible
        if not math.isclose(d2/d1,0.1,rel_tol=1e-6):return blocked("Rule 6 requires two consecutive decimal dilutions")
        r1=single(groups[d1]);r2=single(groups[d2]);ratio=max(r1.value,r2.value)/min(r1.value,r2.value)
        chosen=groups[d1]+groups[d2]
        if ratio>2:
            chosen=groups[d1] if r1.value<r2.value else groups[d2]
            result=single(chosen,selection="Handbook Rule 6: ratio >2, smaller concentration retained")
        else:
            result=Result((r1.value+r2.value)/2,"exact",unit,method_id,[p.id for p in chosen],excluded,
                          formula=f"({r1.value}+{r2.value})/2; ratio={ratio}",selection="Handbook Rule 6: concentration arithmetic mean").finalise()
        result.warnings.append(f"Dilution concentration ratio {ratio:.4g}; this is the handbook factor-of-two convention")
        result.excluded += [p.id for p in usable if p.id not in result.included]
        if all(p.tested is not None for p in chosen):result.confirmation_status="confirmation-adjusted"
        return result
    if len(eligible)==1:
        retained=groups[eligible[0]]
        result=single(retained,selection="Handbook Rule 5: all usable partners at retained dilution")
    else:
        d=min(ds,key=lambda d:abs(mean(p.count for p in groups[d])-m["max"])) if any(p.count>m["max"] for p in usable) else min(ds,key=lambda d:abs(mean(p.count for p in groups[d])-m["min"]))
        retained=groups[d]
        result=single(retained,"estimated","Handbook low/crowded/interrupted count-range exception")
    result.excluded += [p.id for p in usable if p.id not in result.included]
    return result


def area_estimate(density: float, area_cm2: float, dilution: float, volume_ml: float, greater_than: bool=False) -> Result:
    for value,label in [(density,"Density"),(area_cm2,"Plate area"),(dilution,"Dilution"),(volume_ml,"Volume")]:require_positive(value,label)
    if dilution>1:raise ValueError("Dilution must be <=1")
    return Result(density*area_cm2/(dilution*volume_ml),"greater_than" if greater_than else "estimated","CFU/g","handbook_apha_2026",
                  warnings=["Educational area extrapolation; actual representative areas and method permission must be recorded"],
                  formula="density × usable area / (d × v)").finalise()


def convert_recovery(result: Result, recovery_ml: float, sampled_amount: float, basis: str="cm2") -> Result:
    require_positive(recovery_ml,"Recovery volume");require_positive(sampled_amount,"Sampled amount")
    if result.unit!="CFU/mL":raise ValueError("Start with the recovery suspension in CFU/mL")
    if basis not in ("cm2","g","package"):raise ValueError("Unsupported recovery basis")
    x=Result(**asdict(result));x.unit={"cm2":"CFU/cm²","g":"CFU/g","package":"CFU/package"}[basis]
    x.value=None if x.value is None else x.value*recovery_ml/sampled_amount
    x.log10=None;x.rounded=None;x.formula+=f"; × {recovery_ml}/{sampled_amount}"
    return x.finalise()


def confirmation_categories(categories: list[tuple[int,int,int]], whole: bool=True) -> float:
    total=sum(Observation(str(i),c,1,1,tested=t,confirmed=b).adjusted() for i,(c,t,b) in enumerate(categories))
    return float(Decimal(str(total)).quantize(Decimal(1),rounding=ROUND_HALF_UP)) if whole else total


def technical_statistics(observations: list[Observation]) -> dict:
    usable=[p for p in observations if p.state in ("numeric","zero")]
    if not usable:return {"n":0,"scope":"technical plate replicates"}
    values=[p.adjusted()/p.exposure for p in usable]
    logs=[math.log10(v) for v in values if v>0]
    return {"n":len(values),"scope":"technical plate variation; not independent biological samples",
            "mean_cfu":mean(values),"sd_cfu":stdev(values) if len(values)>1 else None,
            "mean_log10":mean(logs) if len(logs)==len(values) else None,
            "sd_log10":stdev(logs) if len(logs)==len(values) and len(logs)>1 else None,
            "relative_difference":(max(values)-min(values))/mean(values) if mean(values)>0 else None}
