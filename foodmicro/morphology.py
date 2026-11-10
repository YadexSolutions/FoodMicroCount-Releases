"""Scale-aware candidate proposals and conservative image artefact checks."""
from dataclasses import dataclass
import math
import re

import cv2
import numpy as np
from scipy import ndimage as ndi
from skimage.measure import regionprops
from skimage.feature import peak_local_max
from skimage.morphology import h_maxima
from skimage.segmentation import watershed

from .models import Detection, uid


@dataclass(frozen=True)
class SizeGuide:
    compact: tuple[float, float]
    broad: tuple[float, float] | None = None



def validate_radius_ranges(ranges):
    if not isinstance(ranges,dict) or set(ranges)!= {'compact','mould'}:
        raise ValueError('Enter both the compact and mould radius ranges')
    for pair in ranges.values():
        if (not isinstance(pair,(tuple,list)) or len(pair)!=2 or
                any(not isinstance(x,(float,int)) or isinstance(x,bool) for x in pair) or not 0<pair[0]<pair[1]<=25):
            raise ValueError('Radius limits must increase and be between 0 and 25 mm')

def radius_guide(profile, boundary, settings):
    """Convert engineering radius priors using the area of the fitted ellipse."""
    manual = SizeGuide((settings.min_radius_px, settings.max_radius_px))
    ranges = (profile or {}).get('vision', {}).get('radius_mm')
    if not settings.use_profile_sizes or not ranges:
        return manual
    validate_radius_ranges(ranges)
    scale = 2 * math.sqrt(boundary.rx * boundary.ry) / settings.dish_diameter_mm

    def pair(name, floor, cap):
        low, high = ranges[name]
        lo = min(cap - 1, max(floor, low * scale))
        return lo, min(cap, max(lo + 1, high * scale))

    return SizeGuide(pair('compact', .65, 150), pair('mould', 3, 260))


def fungal_target(test, profile):
    """Use familiar assay labels; unfamiliar text keeps both candidate types."""
    if not (profile or {}).get('vision', {}).get('fungal'):
        return 'all'
    yeast = bool(re.search(r'\byeasts?\b', test or '', re.I))
    mould = bool(re.search(r'\b(?:mould|mold)s?\b', test or '', re.I))
    return 'yeast' if yeast and not mould else 'mould' if mould and not yeast else 'all'


def _object(x, y, area, bbox, kind, category='uncertain', match=False, **extra):
    return Detection(uid(), float(x), float(y), math.sqrt(area / math.pi), float(area),
                     category=category,
                     features={'candidate_type': kind, 'appearance_match': match,
                               'uncertain': not match, 'bbox': list(bbox), **extra})


def screen_artefacts(gray, hsv, dark, mask, inner, fungal, threshold):
    """Hold distinctive pen strokes and specular reflections for analyst review.

    Compact black spots are ambiguous on black-colony media. A round black dot
    is not removed on colour alone. Fungal pigmentation is also not an ink rule.
    """
    h, s = hsv[:, :, 0], hsv[:, :, 1]
    dark_ink = (gray <= min(104, np.median(gray[mask]) - 38)) & (dark > max(18, threshold * 1.5))
    blue_ink = (h >= 87) & (h <= 125) & (s >= 120) & (s > np.median(s[mask]) + 60)
    glare = (gray >= 247) & (s < 32) & (-dark > max(19, threshold * 1.4))
    blocked = np.zeros(mask.shape, dtype=bool)
    detections = []
    max_area=np.count_nonzero(mask)*.025
    for pixels, name in [((dark_ink | blue_ink) & mask, 'possible-ink'), (glare & mask, 'possible-glare')]:
        labels, _ = ndi.label(pixels, structure=np.ones((3, 3)))
        for obj in regionprops(labels):
            if obj.area < 3 or obj.area > max_area:
                continue
            a, b, c, d = obj.bbox
            length = max(c - a, d - b)
            aspect = length / max(1, min(c - a, d - b))
            yy, xx = obj.coords.T
            y, x = obj.centroid
            on_rim = np.mean(~inner[yy, xx]) >= .35
            if name == 'possible-ink':
                # Strongly coloured round dots can be labelled for review on
                # nonfungal media. Require stroke shape on fungal media.
                chromatic = np.mean(blue_ink[yy, xx]) >= .85
                thin = length >= 8 and (aspect >= 3 or obj.extent < .42) and obj.area / length < 4
                if not (thin or (chromatic and not fungal)):
                    continue
            elif length < 7 or not (aspect > 3 or (on_rim and obj.area >= 12)):
                continue
            blocked[yy, xx] = True
            detections.append(_object(x, y, obj.area, (b, a, d, c), name, category='artefact'))
    return ndi.binary_dilation(blocked, iterations=1), detections


def _outline(binary, x, y, radius):
    contours, _ = cv2.findContours(binary.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:return []
    contour = cv2.approxPolyDP(max(contours, key=cv2.contourArea), max(1.2, radius*.04), True)
    return [[int(u+x), int(v+y)] for u,v in contour.reshape(-1,2)]


def _pigmented_bodies(colour, plate, low, high):
    """Look for broad, connected pigments distinct from the fitted agar.

    Opposite directions on both Lab chroma axes cover colour variation. Peaks
    in the distance map keep touching bodies separate when there is a neck.
    """
    smooth=cv2.GaussianBlur(colour,(0,0),max(1.,min(2.4,low*.13)))
    radius_from_edge=ndi.distance_transform_edt(plate)
    proposals=[]
    for axis in (1,2):
        values=smooth[:,:,axis]
        centre=float(np.median(values[plate]))
        band=float(np.clip(2.0*np.median(np.abs(values[plate]-centre)),7.,14.))
        for direction in (-1,1):
            response=(values-centre)*direction
            pigment=(response>=band)&plate&(radius_from_edge>=max(2,low*.18))
            pigment=ndi.binary_opening(pigment,iterations=1)
            if not pigment.any():continue
            depth=ndi.distance_transform_edt(pigment)
            maxima=h_maxima(depth,max(1.,.27*low)) & (depth>=max(2,.53*low))
            seeds,n=ndi.label(maxima)
            if not n:continue
            labels=watershed(-depth,seeds,mask=pigment)
            for obj in regionprops(labels,intensity_image=response):
                r=math.sqrt(obj.area/math.pi)
                if not low*.94<=r<=high:continue
                y,x=obj.centroid
                a,b,c,d=obj.bbox
                if max(c-a,d-b)>2.1*min(c-a,d-b) or obj.extent<.50 or obj.solidity<.78:continue
                if radius_from_edge[round(y),round(x)]<r*.65:continue
                # Require an enclosing pigment drop at this scale. Labelling
                # marks and slow background changes seldom form such a body.
                pad=math.ceil(.4*r)+2
                y0,y1=max(0,a-pad),min(plate.shape[0],c+pad)
                x0,x1=max(0,b-pad),min(plate.shape[1],d+pad)
                yy,xx=np.ogrid[y0:y1,x0:x1]
                dd=np.hypot(yy-y,xx-x)
                ring=(dd>=r*1.04)&(dd<=r*1.36)&plate[y0:y1,x0:x1]
                ring_values=response[y0:y1,x0:x1][ring]
                if len(ring_values)<max(8,.3*math.pi*r*r):continue
                rim_contrast=float(obj.intensity_mean-np.median(ring_values))
                if rim_contrast<max(3.2,band*.48):continue
                proposals.append((rim_contrast+float(obj.intensity_mean)*.5,obj.coords,(x,y,r)))
    proposals.sort(key=lambda v:v[0],reverse=True)
    labels=np.zeros(plate.shape,np.int32)
    centres=[]
    for _,coords,(x,y,r) in proposals:
        yx=tuple(coords.T)
        if np.mean(labels[yx]>0)>.22 or any(math.hypot(x-u,y-v)<.48*(r+s) for u,v,s in centres):
            continue
        available=coords[labels[yx]==0]
        labels[tuple(available.T)]=len(centres)+1
        centres.append((x,y,r))
    return labels,centres

def _mould_proposals(colour, plate, sizes, scale, threshold, polarity):
    """Find a locally coherent body at each colour/contrast scale.

    Each seed needs a distinct centre and an enclosing colour boundary. This
    keeps the outline with the colony and separates broad bodies that touch.
    """
    low,high=np.array(sizes.broad)*scale
    radii=np.geomspace(max(3,low),max(low+.1,high),8)
    proposals=[]
    agar_chroma=np.median(colour[plate,1:],axis=0)
    for r in radii:
        fg=cv2.GaussianBlur(colour,(0,0),max(1.,.34*r))
        bg=cv2.GaussianBlur(colour,(0,0),max(2.,.88*r))
        d=fg-bg
        luminance=d[:,:,0] if polarity=='bright' else -d[:,:,0] if polarity=='dark' else np.abs(d[:,:,0])
        score=np.hypot(np.maximum(0,luminance)*.67,np.hypot(d[:,:,1],d[:,:,2])*1.25)
        score[~plate]=0
        for y,x in peak_local_max(score,min_distance=max(2,round(r*.66)),
                                  threshold_abs=max(3.4,threshold*.32),exclude_border=False,num_peaks=150):
            proposals.append((float(score[y,x]),int(x),int(y),r))
    proposals.sort(reverse=True)
    labels,moulds=_pigmented_bodies(colour,plate,low,high)
    reliable=set(range(1,len(moulds)+1))
    smooth=cv2.GaussianBlur(colour,(0,0),max(1.,min(2.5,low*.18)))
    for score,x,y,r in proposals:
        if labels[y,x] or any(math.hypot(x-u,y-v)<.54*(r+s) for u,v,s in moulds):
            continue
        span=math.ceil(r*1.56)
        x0,x1=max(0,x-span),min(plate.shape[1],x+span+1)
        y0,y1=max(0,y-span),min(plate.shape[0],y+span+1)
        yy,xx=np.ogrid[y0:y1,x0:x1]
        distance=np.hypot(yy-y,xx-x)
        valid=plate[y0:y1,x0:x1]
        core=valid & (distance<=max(1.,r*.36))
        annulus=valid & (distance>=r*1.12) & (distance<=r*1.48) & (labels[y0:y1,x0:x1]==0)
        if annulus.sum()<max(8,.23*math.pi*r*r) or not core.any():
            continue
        patch=smooth[y0:y1,x0:x1]
        bg=np.median(patch[annulus],axis=0)
        centre=np.median(patch[core],axis=0)
        delta=centre-bg
        chroma=float(np.linalg.norm(delta[1:]))
        # Agar pockets surrounded by coloured colonies are the inverse of the
        # desired object: the centre resembles agar more than the annulus.
        if (chroma>6 and np.linalg.norm(centre[1:]-agar_chroma)+4 < np.linalg.norm(bg[1:]-agar_chroma)):
            continue
        if (polarity=='bright' and delta[0]<2) or (polarity=='dark' and delta[0]>-2):
            continue
        magnitude=float(np.linalg.norm(delta))
        if magnitude<max(11,threshold*.96) or (chroma<4 and abs(delta[0])<17):continue
        # A single dot or a glare sliver cannot supply this extent of coherent colour.
        projection=(patch-bg)@(delta/magnitude)
        support=(projection>.46*magnitude) & valid & (distance<=r*1.40)
        islands,_=ndi.label(support)
        ident=islands[y-y0,x-x0]
        if not ident:continue
        body=islands==ident
        pixel_area=np.count_nonzero(body)
        radius=math.sqrt(pixel_area/math.pi)
        if not max(low*.94,r*.66)<=radius<=min(high,r*1.20):continue
        if np.mean(body[distance<=r*.56])<.82:continue
        yy,xx=np.nonzero(body)
        width,height=np.ptp(xx)+1,np.ptp(yy)+1
        if max(width,height)>1.95*min(width,height) or pixel_area < .52*width*height:continue
        hull=cv2.convexHull(np.column_stack((xx,yy)).astype(np.int32))
        if pixel_area<.81*cv2.contourArea(hull):continue
        # Reject projected blobs cut out of a gradient by the circular search window.
        if np.mean(distance[body] >= r*1.31)>.10:continue
        if (labels[y0:y1,x0:x1][body]>0).mean()>.20:continue
        # A bright arc or gradient frequently occupies only one side of the seed.
        u,v=float(xx.mean()+x0),float(yy.mean()+y0)
        if math.hypot(x-u,y-v)>.30*radius:continue
        labels[y0:y1,x0:x1][body & (labels[y0:y1,x0:x1]==0)]=len(moulds)+1
        moulds.append((u,v,radius))
        # Bright, broad, well enclosed growth also occurs without strong pigment.
        # Other scale-only hypotheses stay visible but out of the total.
        if delta[0]>18 and pixel_area>=.63*width*height and abs(width-height)<.38*max(width,height):
            reliable.add(len(moulds))
    return labels,reliable


def broad_regions(lab, mask, inner, sizes, threshold, allow_spread, polarity='both'):
    """Separate broad mould bodies and possible spread from fine objects."""
    if not allow_spread and sizes.broad is None:
        return np.zeros(mask.shape, dtype=np.int32), [], set()
    # Bound the broad search resolution independently of fine colony detection.
    scale = min(1., 680 / max(mask.shape))
    shape = (max(1, round(mask.shape[1]*scale)), max(1, round(mask.shape[0]*scale)))
    plate = cv2.resize(mask.astype(np.uint8), shape, interpolation=cv2.INTER_NEAREST).astype(bool)
    if not plate.any():
        return np.zeros(mask.shape, dtype=np.int32), [], set()
    colour = cv2.resize(lab, shape, interpolation=cv2.INTER_AREA)
    if not plate.all():
        # Extend agar colour before filtering to reduce response to the bench.
        nearest = ndi.distance_transform_edt(~plate, return_distances=False, return_indices=True)
        colour = colour[tuple(nearest)]
    moulds,reliable=(_mould_proposals(colour,plate,sizes,scale,threshold,polarity)
                     if sizes.broad else (np.zeros(plate.shape,np.int32),set()))
    n_mould=int(moulds.max())
    zones=np.zeros_like(moulds)
    if allow_spread:
        smooth=cv2.GaussianBlur(colour,(0,0),float(np.clip(sizes.compact[1]*scale*.29,1.3,4.)))
        sigma=float(np.clip(max(sizes.compact[1]*scale*3.5, math.sqrt(np.count_nonzero(plate))*.14),9,75))
        delta=smooth-cv2.GaussianBlur(smooth,(0,0),sigma)
        bright=delta[:,:,0]
        luminance=bright if polarity=='bright' else -bright if polarity=='dark' else np.abs(bright)
        chroma=np.hypot(delta[:,:,1],delta[:,:,2])
        contrast=np.maximum(luminance,chroma*1.10 if polarity=='both' else chroma*.7*(luminance>1))
        level=float(np.median(contrast[plate]))
        noise=float(np.median(np.abs(contrast[plate]-level))*1.4826)
        cutoff=max(8.,min(threshold,22.)*.88,level+3.2*noise)
        foreground=(contrast>cutoff)&plate
        foreground=ndi.binary_closing(foreground,iterations=2)&plate&~ndi.binary_dilation(moulds>0,iterations=2)
        coarse,_=ndi.label(foreground)
        valid=np.zeros(coarse.max()+1,dtype=np.int32)
        min_broad=max(sizes.compact[1]*1.5, (sizes.broad[0]*1.7 if sizes.broad else 0))
        max_area=.48*np.count_nonzero(plate)
        for obj in regionprops(coarse):
            radius=math.sqrt(obj.area/math.pi)/scale
            if radius>=min_broad and obj.area<=max_area:
                valid[obj.label]=obj.label+n_mould
        zones=valid[coarse]
    labels=cv2.resize((moulds+zones).astype(np.float32),(mask.shape[1],mask.shape[0]),
                      interpolation=cv2.INTER_NEAREST).astype(np.int32)
    labels[~mask]=0
    accepted=np.zeros(labels.max()+1,dtype=bool)
    detections=[]
    spread_ids=set()
    for obj in regionprops(labels):
        a,b,c,d=obj.bbox
        aspect=max(c-a,d-b)/max(1,min(c-a,d-b))
        yy,xx=obj.coords.T
        rim_share=float(np.mean(~inner[yy,xx]))
        is_mould=obj.label<=n_mould
        if rim_share>.67 or (rim_share>.30 and obj.extent<.40) or aspect>(2.25 if is_mould else 5) or obj.extent<.24:
            continue
        if not is_mould and rim_share>.08 and obj.extent<.46:
            # Narrow wedges connected to the dish wall are often the radial
            # response to glare or illumination, not a body of spreading growth.
            continue
        radius=math.sqrt(obj.area/math.pi)
        y,x=obj.centroid
        if labels[int(round(y)),int(round(x))]!=obj.label:
            distance=ndi.distance_transform_edt(np.pad(obj.image,1))[1:-1,1:-1]
            cy,cx=np.unravel_index(np.argmax(distance),distance.shape)
            y,x=a+cy,b+cx
        outline=_outline(obj.image,b,a,radius)
        if len(outline)<3:continue
        accepted[obj.label]=True
        if allow_spread and (not is_mould or obj.label not in reliable):spread_ids.add(obj.label)
        detections.append(_object(x,y,obj.area,(b,a,d,c),
                                  'broad-mould' if is_mould else 'spread-region',
                                  category='mould-like' if is_mould else 'uncertain',
                                  match=obj.label in reliable,outline=outline))
    return np.where(accepted[labels],labels,0),detections,spread_ids

def internal_pinpoints(gray, broad, spread_ids, sizes, threshold, polarity):
    """Nominate discrete local peaks *inside* spread; keep them out of the total."""
    if not spread_ids:
        return []
    spread = np.isin(broad, list(spread_ids))
    lo, hi = sizes.compact
    # A short morphological top-hat removes the broad background; reject its
    # boundary band and any larger bright/dark structure that remains.
    kernel_radius = int(np.clip(round(hi * .65), 3, 11))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*kernel_radius+1, 2*kernel_radius+1))
    source = gray.astype(np.uint8)
    if polarity == 'bright':
        detail = cv2.morphologyEx(source, cv2.MORPH_TOPHAT, kernel)
    elif polarity == 'dark':
        detail = cv2.morphologyEx(source, cv2.MORPH_BLACKHAT, kernel)
    else:
        detail = np.maximum(cv2.morphologyEx(source, cv2.MORPH_TOPHAT, kernel),
                            cv2.morphologyEx(source, cv2.MORPH_BLACKHAT, kernel))
    interior = ndi.binary_erosion(spread, iterations=max(2, kernel_radius // 2))
    cutoff = max(6., threshold * .80)
    labels, _ = ndi.label((detail >= cutoff) & interior)
    candidates = []
    for obj in regionprops(labels, intensity_image=detail):
        if not max(2, math.pi*lo*lo*.75) <= obj.area <= math.pi*(kernel_radius*.74)**2:
            continue
        a,b,c,d = obj.bbox
        if max(c-a,d-b) / max(1,min(c-a,d-b)) > 2.5 or obj.extent < .43:
            continue
        y,x = obj.centroid_weighted
        if not interior[int(round(y)), int(round(x))]:
            continue
        candidates.append(_object(x, y, obj.area, (b,a,d,c), 'pinpoint-in-spread'))
    return candidates
