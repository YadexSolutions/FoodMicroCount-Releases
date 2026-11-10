"""Independent hybrid detector. Image rules nominate objects, not organism identities."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import copy
import hashlib
import json
import math

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageDraw
from scipy import ndimage as ndi
from skimage.feature import blob_log
from skimage.segmentation import watershed
from skimage.measure import regionprops
from skimage.morphology import h_maxima

from . import ALGORITHM_VERSION
from .models import Boundary, Detection, uid, enumeration_scope
from .morphology import radius_guide, fungal_target, screen_artefacts, broad_regions, internal_pinpoints


def read_rgb(path: str | Path) -> np.ndarray:
    # Pillow handles Unicode paths on Windows; OpenCV's imread historically does not.
    with Image.open(path) as im:
        if im.width*im.height>80_000_000:raise ValueError("Image exceeds 80 megapixels; make a smaller copy")
        return np.array(ImageOps.exif_transpose(im).convert("RGB"))


def image_sha(path: str | Path) -> str:
    with Path(path).open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def profiles() -> dict:
    p=Path(__file__).with_name("data")/"media.json"
    return json.loads(p.read_text(encoding="utf-8"))


@dataclass
class CounterSettings:
    strategy: str="hybrid"
    sensitivity: float=1.0
    min_radius_px: float=0.8
    max_radius_px: float=12.0
    min_contrast: float=9.0
    rim_inset: float=0.13
    split_touching: bool=True
    polarity: str="profile"
    hue_min: float=0.0
    hue_max: float=179.0
    saturation_min: float=0.0
    colour_filter: bool=False
    use_profile_sizes: bool=True
    dish_diameter_mm: float=90.0
    inspect_spreading: bool=True
    screen_marks: bool=True

    def validate(self):
        if self.strategy not in ("hybrid","local","log"):raise ValueError("Unknown detector strategy")
        if self.polarity not in ("profile","bright","dark","both"):raise ValueError("Unknown polarity")
        for v in asdict(self).values():
            if isinstance(v,(int,float)) and not isinstance(v,bool) and not math.isfinite(v):raise ValueError("Nonfinite settings")
        if not 0.25<=self.sensitivity<=4:raise ValueError("Sensitivity must be 0.25–4")
        if not 0.5<=self.min_radius_px<self.max_radius_px<=150:raise ValueError("Invalid colony radius limits")
        if not 0<=self.rim_inset<=0.35:raise ValueError("Rim inset must be 0–35%")
        if not 35<=self.dish_diameter_mm<=200:raise ValueError("Dish diameter must be 35–200 mm")
        if self.min_contrast<1:raise ValueError("Contrast threshold must be >=1")
        if not 0<=self.hue_min<=179 or not 0<=self.hue_max<=179 or not 0<=self.saturation_min<=255:raise ValueError("Invalid HSV range")


def ellipse_mask(shape: tuple[int,...], boundary: Boundary, inset: float=0.0) -> np.ndarray:
    if boundary.rx<=0 or boundary.ry<=0:raise ValueError("Invalid boundary radii")
    yy,xx=np.indices(shape[:2]);a=math.radians(boundary.angle)
    dx,dy=xx-boundary.cx,yy-boundary.cy
    u=dx*math.cos(a)+dy*math.sin(a);v=-dx*math.sin(a)+dy*math.cos(a)
    return (u/(boundary.rx*(1-inset)))**2+(v/(boundary.ry*(1-inset)))**2<=1


def detect_plates(rgb: np.ndarray) -> list[Boundary]:
    """Hough proposals ranked by ring edge support, then overlap suppression.

    Plate coordinates stay in the original image. Ellipse fallback offers manual
    perspective masks, but no homography or invented boundary when detection fails.
    """
    if rgb.ndim!=3 or rgb.shape[2]!=3:raise ValueError("Expected an RGB image")
    h0,w0=rgb.shape[:2];scale=min(1.0,1100/max(h0,w0))
    im=cv2.resize(rgb,(round(w0*scale),round(h0*scale))) if scale<1 else rgb
    gray=cv2.cvtColor(im,cv2.COLOR_RGB2GRAY);gray=cv2.GaussianBlur(gray,(5,5),1)
    h,w=gray.shape;s=min(h,w)
    edges=cv2.Canny(gray,40,100)
    distances=ndi.distance_transform_edt(edges==0)
    cir=cv2.HoughCircles(gray,cv2.HOUGH_GRADIENT,dp=1.15,minDist=s*.23,
                        param1=90,param2=35,minRadius=max(12,int(s*.075)),maxRadius=int(s*.54))
    candidates=[]
    theta=np.linspace(0,2*np.pi,360,endpoint=False)
    for x,y,r in ([] if cir is None else cir[0]):
        x,y,r=float(x),float(y),float(r)
        xx=np.rint(x+r*np.cos(theta)).astype(int);yy=np.rint(y+r*np.sin(theta)).astype(int)
        valid=(xx>=0)&(xx<w)&(yy>=0)&(yy<h)
        if valid.mean()<.70:continue
        ring_support=np.mean(distances[yy[valid],xx[valid]]<=max(2,r*.027))
        # Each of twelve arcs must contain support; avoids unrelated circle fragments.
        arcs=[]
        for ii in np.array_split(np.arange(360),12):
            jj=ii[valid[ii]]
            arcs.append(np.mean(distances[yy[jj],xx[jj]]<=max(2,r*.027)) if len(jj) else 0)
        coverage=np.mean(np.array(arcs)>.4)
        score=float(.7*ring_support+.3*coverage)
        if ring_support<.55 or coverage<.50 or score<.56:continue
        candidates.append(Boundary(x/scale,y/scale,r/scale,r/scale,score=score))
    candidates.sort(key=lambda b:b.score,reverse=True)
    # Use a repeated dish-size constraint only when >=3 proposals support the
    # strongest ring's radius. Manual boundaries remain available for mixed sizes.
    if candidates:
        anchor=candidates[0].rx
        similar=[b for b in candidates if .68*anchor<=b.rx<=1.32*anchor]
        if len(similar)>=3:candidates=similar
    kept=[]
    for b in candidates:
        if any(math.hypot(b.cx-q.cx,b.cy-q.cy)<.70*(b.rx+q.rx) for q in kept):continue
        kept.append(b)
    # Region ellipse proposals only if no trustworthy ring was found.
    if not kept:
        contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if len(contour)<20 or cv2.contourArea(contour)<s*s*.05:continue
            (x,y),(a,b),angle=cv2.fitEllipse(contour)
            if not .65<min(a,b)/max(a,b)<=1 or max(a,b)>s*1.1:continue
            boundary=Boundary(x/scale,y/scale,a/(2*scale),b/(2*scale),angle,score=.5,source="ellipse-fallback")
            if 0<=x<w and 0<=y<h:kept.append(boundary)
        kept=sorted(kept,key=lambda b:b.rx*b.ry,reverse=True)[:1]
    return sorted(kept,key=lambda b:(round(b.cy/(max(1,b.ry))),b.cx))


def quality_assessment(rgb: np.ndarray, mask: np.ndarray, boundary: Boundary) -> dict:
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY);pixels=gray[mask]
    warnings=[]
    if not len(pixels):raise ValueError("Plate mask is empty")
    focus=float(cv2.Laplacian(gray,cv2.CV_32F)[mask].var())
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    glare=(gray>245)&(hsv[:,:,1]<35)&mask
    dark=(gray<20)&mask
    smooth=cv2.GaussianBlur(gray.astype(np.float32),(0,0),max(3,boundary.rx*.10))
    illum=float(np.percentile(smooth[mask],95)-np.percentile(smooth[mask],5))
    diameter=min(boundary.rx,boundary.ry)*2
    if diameter<500:warnings.append("Plate diameter below 500 pixels; pinpoint colonies may be unresolvable. Prefer >=1500 pixels per plate.")
    if focus<30:warnings.append("Low edge sharpness; inspect focus and faint colonies")
    if glare.sum()/mask.sum()>.01:warnings.append("Clipped bright areas may hide colonies or be miscounted as glare")
    if dark.sum()/mask.sum()>.1:warnings.append("Large very dark area may hide colonies")
    if illum>55:warnings.append("Strong illumination gradient; inspect background correction")
    if boundary.cx-boundary.rx<0 or boundary.cy-boundary.ry<0 or boundary.cx+boundary.rx>=rgb.shape[1] or boundary.cy+boundary.ry>=rgb.shape[0]:
        warnings.append("Boundary extends beyond image; check completeness")
    return {"diameter_px":diameter,"focus_variance":focus,"glare_fraction":float(glare.sum()/mask.sum()),
            "dark_fraction":float(dark.sum()/mask.sum()),"illumination_range":illum,
            "warnings":warnings,"decision":"manual review" if warnings else "inspect before use",
            "threshold_status":"provisional engineering heuristics, not validated acceptance criteria"}


def fill_small_holes(binary: np.ndarray, maximum_area: int) -> np.ndarray:
    """Fill colony-scale holes only. A closed dish rim can enclose the whole agar."""
    holes=ndi.binary_fill_holes(binary)&~binary
    labels,_=ndi.label(holes)
    areas=np.bincount(labels.ravel())
    return binary | (holes & (areas[labels]<=maximum_area))


def select_rim(detections, boundary, inset):
    """Set inclusion on a fixed automatic candidate layer, without segmenting again."""
    for d in detections:
        outside=not boundary.contains(d.x,d.y,inset)
        d.features['rim_excluded']=outside
        d.included=not outside and d.features.get('appearance_match',True)



def review_mark_counts(detections):
    keys=('broad-mould','spread-region','pinpoint-in-spread','broad-edge','possible-ink','possible-glare')
    counts=dict.fromkeys(keys,0)
    for d in detections:
        kind=d.features.get('candidate_type')
        if kind in counts:counts[kind]+=1
    return counts

def rim_warning(inset):
    return f'Outer {inset:.0%} of radius excluded from automatic counting. Inspect the omitted agar; no area scaling applied.'


def appearance_match(rule, hue, saturation, centre_signal, dark_signal, threshold):
    # Broad, deliberately editable image heuristics. A match is visual evidence only.
    if rule=='pink-purple':return bool((hue<=15 or hue>=125) and saturation>=38 and centre_signal>=threshold*.55)
    if rule=='golden':return bool(7<=hue<=47 and saturation>=55 and centre_signal>=threshold*.55)
    if rule=='dark-centre':return bool(dark_signal>=threshold*.55)
    return True


def count_colonies(rgb: np.ndarray, boundary: Boundary, medium: str="PCA", settings: CounterSettings | None=None,
                   exclusions: list[dict] | None=None, profile_override: dict | None=None,
                   *, keep_rim_candidates=False, test="") -> tuple[list[Detection],dict,np.ndarray]:
    settings=settings or CounterSettings();settings.validate()
    data=profiles();profile=profile_override if profile_override is not None else data["media"].get(medium)
    if not enumeration_scope(test=test,medium=medium,profile=profile):raise ValueError('This medium is outside the colony counting scope')
    if not profile or not profile.get("counting_enabled"):raise ValueError("This medium is a knowledge-only entry; no counter implemented")
    # Crop a bounding square, keep all outward coordinates referenced to original pixels.
    rad=max(boundary.rx,boundary.ry)
    x0=max(0,int(boundary.cx-rad-2));y0=max(0,int(boundary.cy-rad-2))
    x1=min(rgb.shape[1],int(boundary.cx+rad+3));y1=min(rgb.shape[0],int(boundary.cy+rad+3))
    crop=rgb[y0:y1,x0:x1]
    if crop.size==0:raise ValueError("Boundary lies outside the image")
    local=copy.copy(boundary);local.cx-=x0;local.cy-=y0
    # The slider must not change thresholding, region connectivity, or watershed.
    mask=ellipse_mask(crop.shape,local)
    excluded=np.zeros(mask.shape,bool)
    for ex in exclusions or []:
        a,b,c,d=ex["rect"]
        yy,xx=np.indices(mask.shape);excluded|=(xx+x0>=a)&(xx+x0<=c)&(yy+y0>=b)&(yy+y0<=d)
    mask &= ~excluded
    if mask.sum()<100:raise ValueError("Counting area too small or completely excluded")
    sizes=radius_guide(profile,local,settings)
    fungal=bool(profile["vision"].get("fungal"))
    target=fungal_target(test,profile)
    quality=quality_assessment(crop,mask,local)
    if settings.rim_inset>0:quality['warnings'].append(rim_warning(settings.rim_inset))
    if exclusions:
        quality["warnings"].append("Excluded regions recorded. Spreading/unsuitable regions require a method decision; no area extrapolation applied.")
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY).astype(np.float32)
    lab=cv2.cvtColor(crop,cv2.COLOR_RGB2LAB).astype(np.float32)
    hsv=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)
    polarity=profile["vision"]["polarity"] if settings.polarity=="profile" else settings.polarity
    if polarity=='both':
        # A Gaussian background around bright colonies can create a dark ring,
        # and vice versa. A median resists the small objects in this profile.
        # OpenCV uses 16-bit histogram bins for this 8-bit median path.
        # An aperture above 255 can overflow those bins and is rejected.
        kernel=min(255,2*max(6,round(rad*.15))+1)
        blur=cv2.medianBlur(gray.astype(np.uint8),kernel).astype(np.float32)
    else:blur=cv2.GaussianBlur(gray,(0,0),max(4,rad*.08))
    light=gray-blur;dark=blur-gray
    signal=light if polarity=="bright" else dark if polarity=="dark" else np.maximum(light,dark)
    # Local colour deviation nominates faint coloured objects in the hybrid route.
    a=lab[:,:,1];b=lab[:,:,2]
    colour=np.hypot(a-cv2.GaussianBlur(a,(0,0),max(4,rad*.07)),b-cv2.GaussianBlur(b,(0,0),max(4,rad*.07)))
    if settings.strategy=="hybrid":
        # Colour departure alone must not reverse a selected bright/dark rule.
        # For example, black pen writing should not become a bright PCA colony
        # merely because its chromaticity differs from the agar.
        colour_gate=signal>settings.min_contrast/settings.sensitivity*.4
        signal=np.maximum(signal,colour*.65*colour_gate)
    inner=mask & ellipse_mask(crop.shape,local,.13)
    noise_pixels=signal[inner] if inner.sum()>=100 else signal[mask]
    noise=float(np.median(np.abs(noise_pixels-np.median(noise_pixels)))*1.4826)
    threshold=max(settings.min_contrast,2.5*noise)/settings.sensitivity
    binary=(signal>threshold)&mask
    if settings.colour_filter:
        h=hsv[:,:,0];lo,hi=settings.hue_min,settings.hue_max
        h_ok=(h>=lo)&(h<=hi) if lo<=hi else (h>=lo)|(h<=hi)
        binary &= h_ok&(hsv[:,:,1]>=settings.saturation_min)
    clean=mask.copy()
    review=[]
    inner_edge=mask & ellipse_mask(crop.shape,local,.045)
    if settings.screen_marks:
        blocked,review=screen_artefacts(gray,hsv,dark,mask,inner_edge,fungal,threshold)
        clean &= ~blocked
    broad, large, spread_ids=broad_regions(lab,clean,inner_edge,sizes,threshold,settings.inspect_spreading,polarity)
    for d in large:
        if d.category=='mould-like' and target=='yeast':
            d.features.update(appearance_match=False,uncertain=True)
        # Explicit HSV overrides apply to countable bodies as well as fine marks.
        if d.category=='mould-like' and settings.colour_filter:
            y,x=round(d.y),round(d.x)
            if not (h_ok[y,x] and hsv[y,x,1]>=settings.saturation_min):
                d.features.update(appearance_match=False,uncertain=True)
    details=internal_pinpoints(gray,broad,spread_ids,sizes,threshold,polarity)
    for d in review+large+details:
        d.x+=x0;d.y+=y0
        a,b,c,d_bottom=d.features['bbox'];d.features['bbox']=[a+x0,b+y0,c+x0,d_bottom+y0]
        if 'outline' in d.features:d.features['outline']=[[x+x0,y+y0] for x,y in d.features['outline']]
    foreground_mask=clean & ~ndi.binary_dilation(broad>0,iterations=1)
    binary &= foreground_mask
    rmin,rmax=sizes.compact
    broad_edge=(ndi.binary_dilation(broad>0,iterations=max(3,min(9,round(rmax*.48))))
                if large else np.zeros_like(mask))
    min_area=max(2,int(math.pi*rmin**2))
    max_area=math.pi*rmax**2
    # Fill small interior holes, retain tiny objects without aggressive erosion.
    binary=fill_small_holes(binary,min(32,max(3,int(max_area*.2)))) & foreground_mask
    components,n=ndi.label(binary)
    component_sizes=np.bincount(components.ravel())
    binary &= (component_sizes[components]>=min_area)&(component_sizes[components]<=max_area*6)
    distance=ndi.distance_transform_edt(binary)
    if settings.strategy=="log":
        vals=signal.copy();vals[~foreground_mask]=0
        blobs=blob_log(vals/255,min_sigma=max(.6,rmin/math.sqrt(2)),
                       max_sigma=rmax/math.sqrt(2),num_sigma=8,
                       threshold=threshold/255*.35,overlap=.3)
        markers=np.zeros(mask.shape,np.int32)
        for i,(y,x,sigma) in enumerate(blobs,1):
            yi,xi=int(round(y)),int(round(x))
            if 0<=yi<mask.shape[0] and 0<=xi<mask.shape[1] and binary[yi,xi]:markers[yi,xi]=i
        # Components with no LoG seed get their strongest distance centre.
        comp,n=ndi.label(binary)
        for label in range(1,n+1):
            obj=comp==label
            if not np.any(markers[obj]):
                y,x=np.unravel_index(np.argmax(distance*obj),mask.shape);markers[y,x]=markers.max()+1
        labels=watershed(-distance,markers,mask=binary)
    elif settings.split_touching:
        # Label entire maxima plateaus once. Pixel-by-pixel plateau peaks can split a
        # single irregular colony into many objects, especially at low resolution.
        maxima=h_maxima(distance,max(.75,rmin*.75)) & binary
        markers,nseeds=ndi.label(maxima)
        labels=watershed(-distance,markers,mask=binary) if nseeds else ndi.label(binary)[0]
    else:labels=ndi.label(binary)[0]
    detections=review+large+details;ambiguous=sum(d.features.get('uncertain',False) for d in detections);rule=profile['vision'].get('appearance_rule','any')
    for obj in regionprops(labels,intensity_image=signal):
        if obj.area<min_area or obj.area>max_area:continue
        ymin,xmin,ymax,xmax=obj.bbox
        width,height=xmax-xmin,ymax-ymin
        elong=max(width,height)/max(1,min(width,height))
        if elong>7:continue  # writing/streak guard; irregular colonies remain candidates
        y,x=obj.centroid_weighted
        if not np.isfinite(x+y):y,x=obj.centroid
        if not local.contains(x,y):continue
        # Strong connected bright regions are excluded only when large, preserving white colonies.
        glare_ratio=float(np.mean(gray[obj.coords[:,0],obj.coords[:,1]]>248))
        circularity=float(min(1,4*math.pi*obj.area/max(1,obj.perimeter)**2))
        solidity=float(obj.solidity)
        category="presumptive"
        if fungal:
            category="mould-like" if sizes.broad is None and obj.area>90 and (solidity<.8 or circularity<.5) else "yeast-like"
        yi,xi=int(round(y)),int(round(x))
        hue,saturation,_=hsv[yi,xi]
        matches=appearance_match(rule,int(hue),int(saturation),signal[yi,xi],dark[yi,xi],threshold)
        if (target=='mould' and category=='yeast-like') or (target=='yeast' and category=='mould-like'):matches=False
        on_growth_boundary=bool(broad_edge[yi,xi])
        if on_growth_boundary:matches=False
        near_edge=not local.contains(x,y,.045)
        # Reflective or highly elongated perimeter objects remain available for review.
        if near_edge and (elong>2.5 or glare_ratio>.2):matches=False
        uncertain=bool(not matches or near_edge or elong>3 or circularity<.35 or obj.area>max_area*.65 or (glare_ratio>.8 and obj.area>60))
        if uncertain:ambiguous+=1
        detections.append(Detection(uid(),float(x+x0),float(y+y0),float(math.sqrt(obj.area/math.pi)),float(obj.area),
                                    category=category if matches or fungal else 'uncertain',score=float(min(1,obj.intensity_mean/(threshold*3))),
                                    features={"candidate_type":"broad-edge" if on_growth_boundary else "compact",
                                              "mean_contrast":float(obj.intensity_mean),"circularity":circularity,
                                              "solidity":solidity,"elongation":elong,"uncertain":uncertain,"clipped_bright_fraction":glare_ratio,
                                              "appearance_match":matches,"appearance_rule":rule,"near_edge":near_edge,
                                              "bbox":[xmin+x0,ymin+y0,xmax+x0,ymax+y0]}))
    detections.sort(key=lambda d:(round(d.y/10),d.x))
    foreground=float((binary.sum()+np.count_nonzero(broad))/mask.sum())
    # A few discrete broad moulds can cover a large area without being crowded.
    crowded=bool(sum(d.features.get('appearance_match',True) for d in detections)>=400
                 or (target!='mould' and binary.sum()/mask.sum()>.18))
    if crowded:quality['warnings'].insert(0,'Crowded image — review the plate. Use Mark TNTC when appropriate for the selected method; the candidate total is not a reportable count.')
    if ambiguous:quality["warnings"].append(f"{ambiguous} full-plate candidates flagged for appearance, edge or shape review")
    if fungal:quality["warnings"].append("Yeast-like/mould-like labels describe appearance only. Verify both size classes.")
    if spread_ids:quality["warnings"].append("Possible spreading growth is held out. Check marked regions and any distinct pinpoints before including them.")
    if review:quality["warnings"].append("Possible pen or glare marks are held out. Inspect them before including.")
    if medium!="PCA":quality["warnings"].append("This medium profile uses heuristic appearance support; halos and biochemical identity are not automatically confirmed")
    select_rim(detections,boundary,settings.rim_inset)
    visible=[d for d in detections if not d.features['rim_excluded']]
    quality.update({"threshold":threshold,"candidate_count":len(visible),"full_candidate_count":len(detections),
                    "appearance_review_count":sum(not d.included for d in visible),"crowded_review":crowded,"foreground_fraction":foreground,
                    "profile_version":data["version"],"algorithm_version":ALGORITHM_VERSION,"strategy":settings.strategy,
                    "counted_target":target,"compact_radius_px":list(sizes.compact),
                    "mould_radius_px":list(sizes.broad) if sizes.broad else None,
                    "visible_review_marks":review_mark_counts(visible)})
    diagnostic=np.zeros_like(crop);diagnostic[:]=np.clip(signal[:,:,None]*5,0,255).astype(np.uint8)
    diagnostic[~mask]=[25,32,43]
    return (detections if keep_rim_candidates else visible),quality,diagnostic


def annotate(rgb: np.ndarray, plate, numbers: bool=True) -> Image.Image:
    im=Image.fromarray(rgb);draw=ImageDraw.Draw(im)
    b=plate.boundary
    pts=[]
    a=math.radians(b.angle)
    for t in np.linspace(0,2*np.pi,180):
        u,v=b.rx*math.cos(t),b.ry*math.sin(t)
        pts.append((b.cx+u*math.cos(a)-v*math.sin(a),b.cy+u*math.sin(a)+v*math.cos(a)))
    draw.line(pts,fill="#38bdf8",width=2)
    i=0
    for d in plate.detections:
        if not d.included:continue
        i+=1;r=max(2,min(d.radius,10))
        colour="#22c55e" if d.origin=="manual" else "#22d3ee" if d.features.get("candidate_type")=="broad-mould" else "#f97316"
        if outline:=d.features.get("outline"):draw.line(outline+[outline[0]],fill=colour,width=1)
        draw.ellipse((d.x-r,d.y-r,d.x+r,d.y+r),outline=colour,width=1)
        if numbers:draw.text((d.x+r+1,d.y-r),str(i),fill=colour)
    for e in plate.exclusions:draw.rectangle(e["rect"],outline="#ef4444",width=2)
    # Export a per-plate crop while keeping source coordinates in traceability JSON.
    rad=max(b.rx,b.ry);box=(max(0,int(b.cx-rad-4)),max(0,int(b.cy-rad-4)),min(im.width,int(b.cx+rad+4)),min(im.height,int(b.cy+rad+4)))
    return im.crop(box)

