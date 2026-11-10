"""Read-only workbook ingestion and explicitly reviewed external observations.

No Excel formula evaluator, automatic identity matching or result authorisation.
Source cells and cached values remain separate from audited analyst overrides.
"""
from __future__ import annotations
from pathlib import Path
from datetime import date, datetime
from dataclasses import asdict
import copy
import csv
import hashlib
import json
import math
import re

from .models import uid, timestamp, enumeration_scope
from .calculations import Observation, Result, METHODS, calculate


ALIASES = {
    'SAMPLE ID':'sample_id', 'SAMPLEID':'sample_id', 'SAMPLE NAME':'sample_name',
    'TEST':'test', 'TEST ID':'test_id', 'TEST REPLICATION':'replicate',
    'REPLICATE':'replicate', 'NUMBER OF COLONIES':'count', 'COUNT':'count',
    'DILUTION FACTOR USED':'total_inverse', 'TOTAL INVERSE DILUTION':'total_inverse',
    'VOLUME OF INOCULUM':'volume_ml', 'VOLUME PLATED ML':'volume_ml',
    'MASS OF SAMPLE':'sample_amount', 'VOLUME OF FIRST DILUENT':'first_diluent_ml',
    'FIRST DILUTION':'initial_fraction', 'FIRST DILUTION FACTOR':'initial_inverse', 'ADDITIONAL INVERSE DILUTION':'additional_inverse',
    'COUNT CFU':'source_cfu', 'COUNT LOG CFU':'source_log',
    '%CONFIRMED COLONIES':'confirmation_percent', 'COLONIES TESTED':'tested',
    'COLONIES CONFIRMED':'confirmed', 'RESULT UNIT':'unit', 'DATE':'date',
    'OBSERVATION':'state', 'METHOD VERSION':'method_id','MEDIUM':'medium','PLATE ID':'plate_id'
}
EDITABLE = {'sample_id','sample_name','test_id','test','replicate','count','total_inverse',
            'volume_ml','unit','state','tested','confirmed','sample_amount','first_diluent_ml',
            'initial_fraction','method_id','medium','plate_id'}


def normalise_header(value):
    return re.sub(r'\s+',' ',re.sub(r'[()_\-]',' ',str(value or '').upper())).strip()


def serial(value):
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    return value


def fields(record):
    return {**record['fields'],**record.get('overrides',{})}


def fingerprint(record):
    return hashlib.sha256(json.dumps(fields(record),sort_keys=True,allow_nan=False).encode()).hexdigest()


def number(value, label):
    if isinstance(value,bool) or value is None:raise ValueError(f'{label} is missing or not numeric')
    try:n=float(value)
    except (ValueError,TypeError):raise ValueError(f'{label} is not numeric')
    if not math.isfinite(n):raise ValueError(f'{label} must be finite')
    return n


def observation(record):
    f=fields(record);factor=number(f.get('total_inverse'),'Total reciprocal dilution')
    if factor<1:raise ValueError('Total reciprocal dilution must be >=1')
    state=str(f.get('state') or '').strip()
    raw=f.get('count')
    if not state:
        state=str(raw).strip() if isinstance(raw,str) and raw.strip() in ('TNTC','spreading','contaminated','unreadable','missing','unsuitable') else 'numeric'
    count=number(raw,'Colony count') if state in ('numeric','estimated','zero') else None
    if count==0 and state=='numeric':state='zero'
    tested,confirmed=f.get('tested'),f.get('confirmed')
    if tested not in (None,'') or confirmed not in (None,''):
        tested=number(tested,'Tested');confirmed=number(confirmed,'Confirmed')
        if tested!=int(tested) or confirmed!=int(confirmed):raise ValueError('Confirmation counts must be integers')
        tested,confirmed=int(tested),int(confirmed)
    else:
        tested=confirmed=None
        pct=f.get('confirmation_percent')
        if pct not in (None,'') and number(pct,'Confirmation percent') !=100:
            raise ValueError('A percentage alone cannot reconstruct confirmation evidence; enter tested and confirmed counts')
    o=Observation(record['id'],count,1/factor,number(f.get('volume_ml'),'Plated volume'),state,tested,confirmed)
    o.validate();return o


def import_workbook(path: str | Path, sheet: str | None=None, column_mapping: dict | None=None, selected_header_row: int=1) -> dict:
    """Support training/export headings anywhere in the first 30 rows, with cached values.

    Blank cells are never silently filled from another physical observation.
    Excel formulas are recorded, never executed. Missing formula caches need input.
    """
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    path=Path(path)
    if path.suffix.lower()!='.xlsx':raise ValueError('Import supports .xlsx only; save a separate .xlsx copy of other formats')
    if path.stat().st_size>50*1024*1024:raise ValueError('Workbook exceeds the 50 MB import limit')
    wb=load_workbook(path,data_only=False,read_only=True,keep_links=False)
    cache=load_workbook(path,data_only=True,read_only=True,keep_links=False)
    try:
        if sheet is not None and sheet not in wb.sheetnames:raise ValueError('Worksheet not found')
        selected=[sheet] if sheet else wb.sheetnames
        batches=[]
        for name in selected:
            ws=wb[name];cs=cache[name];mapping={};header_row=None
            if column_mapping is not None:
                mapping={int(k):v for k,v in column_mapping.items()}
                if not {'count','total_inverse','volume_ml'}.issubset(mapping.values()):raise ValueError('Map count, total_inverse and volume_ml')
                if any(k<1 or k>100 for k in mapping) or any(v not in set(ALIASES.values()) for v in mapping.values()):raise ValueError('Invalid column mapping')
                if not 1<=selected_header_row<=30:raise ValueError('Header row must be 1–30')
                header_row=selected_header_row
            for row_index,row in enumerate(ws.iter_rows(min_row=1,max_row=min(ws.max_row or 30,30),max_col=min(ws.max_column or 100,100)),1):
                if header_row is not None:break
                candidate={cell.column:ALIASES[normalise_header(cell.value)] for cell in row if normalise_header(cell.value) in ALIASES}
                if {'count','total_inverse','volume_ml'}.issubset(candidate.values()):
                    mapping=candidate;header_row=row_index;break
            if header_row is None:continue
            if len(set(mapping.values()))!=len(mapping):raise ValueError(f'Duplicate mapped headings in worksheet {name}')
            if (ws.max_row or 0)>100000:raise ValueError('Worksheet exceeds the 100,000-row import limit')
            records=[]
            for row_number,(source_row,cached_row) in enumerate(zip(ws.iter_rows(min_row=header_row+1,max_col=max(mapping)),cs.iter_rows(min_row=header_row+1,max_col=max(mapping))),header_row+1):
                if row_number>100000:raise ValueError('Worksheet exceeds the 100,000-row import limit')
                if not any(source_row[col-1].value is not None for col in mapping):continue
                f={};cells={};issues=[]
                for col,key in mapping.items():
                    cell=source_row[col-1];cached=cached_row[col-1].value
                    value=cached if cell.data_type=='f' else cell.value
                    coordinate=f'{get_column_letter(col)}{row_number}'
                    f[key]=serial(value);cells[coordinate]={'field':key,'original':serial(cell.value),'cached':serial(cached),'is_formula':cell.data_type=='f'}
                    if key=='confirmation_percent' and '%' in (cell.number_format or '') and isinstance(value,(int,float)):f[key]=100*value
                    if cell.data_type=='f' and cached is None:issues.append(f'{coordinate}: formula has no cached value; no formula execution attempted')
                    if key in ('sample_id','test_id') and isinstance(value,(date,datetime)):issues.append(f'{key}: Excel date coercion; verify the original identifier')
                if f.get('confirmation_percent') not in (None,''):issues.append('Confirmation percentage retained as source text/value; it is not evidence of identity confirmation')
                record={'id':uid(),'sheet':name,'row':row_number,'fields':f,'cells':cells,'issues':issues,'overrides':{},'events':[],
                        'review_status':'draft','reviewer':'','reviewed_at':'','review_fingerprint':''}
                try:
                    o=observation(record)
                    if o.count is not None and o.count>0:
                        expected=o.adjusted()/o.exposure
                        supplied=f.get('source_cfu')
                        if supplied is not None and not math.isclose(number(supplied,'Source CFU'),expected,rel_tol=1e-8):
                            issues.append(f'Source CFU differs from count/(total dilution × plated volume): recomputed {expected:.12g}')
                    if o.count==0 and f.get('source_log')==0:issues.append('Source log zero is not a measured log concentration for no-growth observations')
                except ValueError as e:issues.append(str(e))
                records.append(record)
            batches.append({'sheet':name,'header_row':header_row,'records':records})
        if not batches:raise ValueError('No supported count sheet found. Required headings: NUMBER OF COLONIES, DILUTION FACTOR USED, VOLUME OF INOCULUM')
        return {'id':uid(),'source_name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'imported_at':timestamp(),'sheets':batches,'events':[],
                'provenance':'External spreadsheet observations; not image-derived or independently verified reference counts'}
    finally:wb.close();cache.close()


def records(batch):
    return [r for s in batch['sheets'] for r in s['records']]


def review_rows(batch, ids, changes, actor, reason):
    """Atomic explicit external-record correction; source cells remain immutable."""
    if not actor.strip() or not reason.strip():raise ValueError('Analyst identity and review reason required')
    if set(changes)-EDITABLE:raise ValueError('Unsupported spreadsheet override')
    chosen=[r for r in records(batch) if r['id'] in ids]
    if not chosen or len(chosen)!=len(set(ids)):raise ValueError('Choose existing imported rows')
    updates=[]
    for record in chosen:
        candidate=copy.deepcopy(record);candidate['overrides'].update(changes);f=fields(candidate)
        for k in ('sample_id','test_id','test','replicate'):
            if not str(f.get(k) or '').strip():raise ValueError(f'{k} is required; blank spreadsheet identifiers are not filled automatically')
        if f.get('unit') not in ('CFU/g','CFU/mL'):raise ValueError('Explicitly select CFU/g or CFU/mL')
        if f.get('method_id') not in METHODS:raise ValueError('Select an explicit calculation method and edition')
        observation(candidate)
        event={'id':uid(),'time':timestamp(),'actor':actor,'reason':reason,'action':'external_record_review',
               'before':copy.deepcopy(record['overrides']),'after':copy.deepcopy(candidate['overrides'])}
        candidate['events'].append(event);candidate.update(review_status='reviewed',reviewer=actor,reviewed_at=timestamp(),review_fingerprint=fingerprint(candidate))
        updates.append((record,candidate))
    for record,candidate in updates:record.clear();record.update(candidate)
    batch['events'].append({'time':timestamp(),'actor':actor,'reason':reason,'action':'review_rows','row_ids':list(ids)})


def import_results(batch):
    groups={}
    for r in records(batch):
        f=fields(r);key=(str(f.get('sample_id') or ''),str(f.get('test_id') or r['id']))
        groups.setdefault(key,[]).append(r)
    results=[]
    for (sample,test_id),group in groups.items():
        fs=[fields(r) for r in group];method=fs[0].get('method_id') or 'basic_exposure_v1';unit=fs[0].get('unit') or 'CFU/g'
        def blocked(msg):return Result(None,'requires_review',unit,method,warnings=[msg],status='requires_review')
        if any(not enumeration_scope(f.get('test'),f.get('medium')) for f in fs):result=blocked('This observation is outside the colony counting scope')
        elif any(f.get('unit') not in ('CFU/g','CFU/mL') or f.get('method_id') not in METHODS for f in fs):result=blocked('Enter a result unit and calculation method for these observations')
        elif len({(f.get('test'),f.get('medium'),f.get('method_id'),f.get('unit'),f.get('sample_amount'),f.get('first_diluent_ml'),f.get('initial_fraction')) for f in fs})!=1:result=blocked('Joined external records have incompatible preparation, units, test, medium or method')
        elif len({(f.get('total_inverse'),str(f.get('replicate'))) for f in fs})!=len(group):result=blocked('Duplicate replicate label at the same dilution; verify distinct physical plates')
        elif method=='bam_apc_2026_numeric' and str(fs[0]['test']).strip().upper() not in ('APC','AEROBIC PLATE COUNT'):result=blocked('FDA APC arithmetic is restricted to APC')
        else:
            try:result=calculate([observation(r) for r in group],method,unit)
            except ValueError as e:result=blocked(str(e))
        results.append({'source_id':batch['id'],'sample_id':sample,'test_id':test_id,'rows':[r['id'] for r in group],
                        'result':asdict(result),'display':result.text(),'authorised':False,
                        'review_status':'reviewed' if all(r['review_status']=='reviewed' and r['review_fingerprint']==fingerprint(r) for r in group) else 'not reviewed'})
    return results


def export_import(batch, destination):
    """Portable reconciliation report, including untouched source cell records."""
    from .reporting import safe_cell
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    payload={**batch,'results':import_results(batch)}
    (destination/'spreadsheet_audit.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    with (destination/'spreadsheet_reconciliation.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerow(['SOURCE','SHEET','ROW','SAMPLE ID','TEST ID','COUNT','TOTAL INVERSE DILUTION','VOLUME ML','SOURCE CFU','RECOMPUTED INDIVIDUAL CFU','STATE','REVIEW','ISSUES'])
        for r in records(batch):
            f=fields(r);value=None;state=f.get('state')
            try:
                o=observation(r);state=o.state
                if o.count and o.state in ('numeric','estimated'):value=o.adjusted()/o.exposure
            except ValueError:pass
            writer.writerow([safe_cell(v) for v in [batch['source_name'],r['sheet'],r['row'],f.get('sample_id'),f.get('test_id'),f.get('count'),f.get('total_inverse'),f.get('volume_ml'),f.get('source_cfu'),value,state,r['review_status'],'; '.join(r['issues'])]])
    return payload
