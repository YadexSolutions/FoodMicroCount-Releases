"""Independent reference point evaluation; never infer truth from auto counts."""
import numpy as np
from scipy.optimize import linear_sum_assignment


def detection_metrics(predicted, reference, tolerance_px=5.0):
    if tolerance_px<=0:raise ValueError('Positive matching tolerance required')
    a=np.asarray(predicted,dtype=float).reshape((-1,2));b=np.asarray(reference,dtype=float).reshape((-1,2))
    if not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Points must be finite')
    matches=0
    if len(a) and len(b):
        distances=np.linalg.norm(a[:,None,:]-b[None,:,:],axis=2)
        # Penalise out-of-tolerance links before the one-to-one assignment.
        costs=np.where(distances<=tolerance_px,distances,1e9)
        ii,jj=linear_sum_assignment(costs);matches=int(np.sum(costs[ii,jj]<1e9))
    fp,fn=len(a)-matches,len(b)-matches
    return {'reference_n':len(b),'predicted_n':len(a),'true_positive':matches,'false_positive':fp,'false_negative':fn,
            'precision':matches/len(a) if len(a) else None,'recall':matches/len(b) if len(b) else None,
            'absolute_count_error':abs(len(a)-len(b)),
            'percentage_count_error':100*abs(len(a)-len(b))/len(b) if len(b) else None,
            'tolerance_px':tolerance_px}


def split_by_group(records):
    assignments={}
    for row in records:
        group=row['physical_plate_group'];split=row['split']
        if split not in ('training','validation','test'):raise ValueError('Unknown split')
        if group in assignments and assignments[group]!=split:raise ValueError('Leakage: related physical plate images occur in multiple splits')
        assignments[group]=split
    return assignments


def control_limits(values):
    """Provisional mean ±3SD, not validated limits or universal food limits."""
    x=np.asarray(values,dtype=float)
    if len(x)<20 or not np.isfinite(x).all():raise ValueError('At least 20 finite independent stable-process baseline observations required')
    m=float(x.mean());s=float(x.std(ddof=1))
    return {'mean':m,'sd':s,'lower':m-3*s,'upper':m+3*s,
            'status':'provisional; verify stability, independence and distribution before adopting'}

