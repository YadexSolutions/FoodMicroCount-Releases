from __future__ import annotations
from . import __version__
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
import csv
import json
import math

from .models import Project, enumeration_scope
from .calculations import Observation, Result, calculate, METHODS, technical_statistics
from .vision import profiles, read_rgb, annotate

RESULT_NOTICE='The analyst is responsible for checking the plate count, inputs and result before use.'


TRAINING_HEADERS=['DATE','SAMPLE NAME','SAMPLE ID','TEST','MASS OF SAMPLE','VOLUME OF FIRST DILUENT',
 'FIRST DILUTION','FIRST DILUTION FACTOR','DILUTION FACTOR USED','VOLUME OF INOCULUM','TEST REPLICATION',
 'NUMBER OF COLONIES','%CONFIRMED COLONIES','COUNT (CFU)','COUNT (LOG CFU)','AVERAGE COUNTS - CFU','STANDARD DEVIATION',
 'PLATE ID','TEST ID','METHOD VERSION','MEDIUM','OBSERVATION','REVIEW STATUS','REPORTING QUALIFIER','RESULT UNIT',
 'AUTOMATIC COUNT','REVIEWED COUNT','SAMPLE RESULT (ENGINE)','RESULT STATUS','FORMULA CHECK (CFU)','ANALYST','REVIEWER','IMAGE SHA256']


def plate_observation(plate, meta=None):
    m=meta if meta is not None else plate.meta;m.validate();state=m.observation
    if state in ('numeric','estimated') and not (plate.parameters.get('algorithm_version') or plate.detections):state='missing'
    count=plate.count if state in ('numeric','zero','estimated') else None
    # Non-numeric observations do not enter confirmation arithmetic.
    return Observation(plate.id,count,m.total_fraction,m.plated_ml,state,m.confirmation_tested if count is not None else None,m.confirmation_positive if count is not None else None)


def sample_results(project: Project, meta_overrides=None) -> list[dict]:
    """Calculate current numerical observations independently of optional review records."""
    meta_overrides=meta_overrides or {}
    groups=defaultdict(list)
    for p in project.plates:
        m=meta_overrides.get(p.id,p.meta);groups[(m.sample_id,m.test_id or p.id)].append((p,m))
    results=[]
    database=profiles()
    for (sample_id,test_id),group in groups.items():
        plates,metas=zip(*group);first=metas[0];method=first.method_id;unit='CFU/'+first.original_unit
        def blocked(msg):return Result(None,'requires_review',unit,method,warnings=[msg],status='requires_review')
        if any(not enumeration_scope(m.test,m.medium,p.parameters.get('profile_snapshot',database['media'].get(m.medium))) for p,m in group):r=blocked('This observation is outside the colony counting scope')
        elif len({(m.test,m.medium,m.method_id,m.original_unit,m.sample_amount,m.first_diluent_ml,m.initial_fraction_override) for m in metas})!=1:
            r=blocked('Sample/test identifiers join inconsistent methods, media, units or preparation records; separate the test IDs')
        elif any(e['kind'] in ('spreading','unsuitable') for p in plates for e in p.exclusions):r=blocked('Spreading/unsuitable area marked; resolve physical usability under the approved method')
        elif method=='bam_apc_2026_numeric' and first.test.strip().upper() not in ('APC','AEROBIC PLATE COUNT'):
            r=blocked('FDA BAM APC method is restricted to APC')
        else:
            try:
                observations=[plate_observation(p,m) for p,m in group]
                if len({(o.dilution,m.replicate) for o,m in zip(observations,metas)})!=len(plates):r=blocked('Duplicate replicate labels at the same dilution')
                else:r=calculate(observations,method,unit)
            except (ValueError,ZeroDivisionError) as e:r=blocked(str(e))
        r.status='calculated' if r.value is not None and r.status!='requires_method_review' else r.status
        checked=all(p.review_status=='reviewed' and m==p.meta and p.review_fingerprint==p.fingerprint() for p,m in group)
        results.append({'sample_id':sample_id,'sample_name':first.sample_name,'test_id':test_id,'test':first.test,
                        'plates':[p.id for p in plates], 'result':asdict(r),'display':r.text(),
                        'review_status':'reviewed' if checked else 'not reviewed','authorised':False,'authorisation_note':RESULT_NOTICE})
    return results


def safe_cell(value):
    """Keep untrusted sample identifiers from becoming Excel/CSV formulas."""
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def training_rows(project: Project) -> list[list]:
    rows=[];result_index={p:r for r in sample_results(project) for p in r['plates']}
    for plate in project.plates:
        m=plate.meta
        try:
            m.validate();first=m.initial_fraction;total=m.total_fraction
        except (ValueError,ZeroDivisionError):first=total=None
        group=result_index[plate.id];r=group['result']
        reviewed=plate.review_status=='reviewed' and plate.review_fingerprint==plate.fingerprint()
        numeric=m.observation in ('numeric','zero','estimated')
        raw=plate.count if numeric and (plate.parameters.get('algorithm_version') or plate.detections or m.observation=='zero') else None if numeric else m.observation
        confirmed_pct=100*m.confirmation_positive/m.confirmation_tested if m.confirmation_tested else None
        individual=None
        if numeric and total and r['value'] is not None and plate.id in r['included'] and plate.count>0:
            individual=plate_observation(plate).adjusted(whole=m.method_id=='iso7218_2007_2013_arithmetic')/(m.plated_ml*total)
        log=math.log10(individual) if individual is not None and individual>0 and r['qualifier'] in ('exact','estimated') else None
        stats={}
        if r['value'] is not None and r['qualifier'] in ('exact','estimated'):
            selected=[p for p in project.plates if p.id in r['included']]
            stats=technical_statistics([plate_observation(p) for p in selected])
        sha=project.images[plate.image_id]['sha256']
        rows.append([m.date,m.sample_name,m.sample_id,m.test,m.sample_amount,m.first_diluent_ml,first,
                     1/first if first else None,1/total if total else None,m.plated_ml,m.replicate,raw,confirmed_pct,
                     individual,log,r['value'],stats.get('sd_log10'),plate.id,m.test_id,m.method_id,m.medium,m.observation,
                     'reviewed' if reviewed else 'not reviewed',r['qualifier'],r['unit'],plate.automatic_count,plate.count if reviewed and numeric else None,
                     r['value'],r['status'],None,m.analyst,plate.reviewer,sha])
    return rows


LAYOUT_FIELDS=['SAMPLE ID','SAMPLE NAME','TEST ID','TEST','MEDIUM','PLATE ID','DATE','MASS OF SAMPLE',
               'VOLUME OF FIRST DILUENT','FIRST DILUTION','DILUTION FACTOR USED','VOLUME OF INOCULUM',
               'TEST REPLICATION','NUMBER OF COLONIES','OBSERVATION','METHOD VERSION',
               'RESULT','RESULT VALUE','REPORTING QUALIFIER','RESULT UNIT','RESULT STATUS','SOURCE','REVIEW STATUS']


def template_records(project):
    """One row per counted observation or reviewed exception, with its review status."""
    from .spreadsheet_io import records,fields,fingerprint,import_results,observation
    index={p:r for r in sample_results(project) for p in r['plates']}
    out=[]
    for p,row in zip(project.plates,training_rows(project)):
        r=index[p.id];v=r['result'];data=dict(zip(TRAINING_HEADERS,row))
        if v['value'] is None and not (p.review_status=='reviewed' and p.review_fingerprint==p.fingerprint()):continue
        if not enumeration_scope(p.meta.test,p.meta.medium,p.parameters.get('profile_snapshot')):continue
        data.update({'RESULT':r['display'],'RESULT VALUE':v['value'],'SOURCE':'Image plate record'})
        out.append({key:data.get(key) for key in LAYOUT_FIELDS})
    for batch in project.imported_workbooks:
        index={row:r for r in import_results(batch) for row in r['rows']}
        for record in records(batch):
            f=fields(record);r=index[record['id']];v=r['result']
            if not enumeration_scope(f.get('test'),f.get('medium')):continue
            if v['value'] is None and not (record['review_status']=='reviewed' and record['review_fingerprint']==fingerprint(record)):continue
            o=observation(record)
            data=dict(zip(LAYOUT_FIELDS[:13],[f.get(k) for k in ('sample_id','sample_name','test_id','test','medium','plate_id','date','sample_amount','first_diluent_ml','initial_fraction','total_inverse','volume_ml','replicate')]))
            data.update({'NUMBER OF COLONIES':o.count if o.count is not None else o.state,'OBSERVATION':o.state,'METHOD VERSION':v['method_id'],
                         'RESULT':r['display'],'RESULT VALUE':v['value'],'REPORTING QUALIFIER':v['qualifier'],'RESULT UNIT':v['unit'],
                         'RESULT STATUS':v['status'],'REVIEW STATUS':r['review_status'],'SOURCE':f'{batch["source_name"]} / {record["sheet"]} / row {record["row"]}'})
            out.append(data)
    return out


def write_result_layout(source,target,sheet,first_row,mapping,rows):
    """Fill blank mapped cells in a new .xlsx; preserve source and existing formulas."""
    import os,tempfile
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell
    source,target=Path(source),Path(target)
    if source.resolve()==target.resolve():raise ValueError('Save to a new file so the source template is preserved')
    if source.suffix.lower()!='.xlsx' or target.suffix.lower()!='.xlsx':raise ValueError('Choose .xlsx workbooks')
    if source.stat().st_size>50*1024*1024:raise ValueError('Excel layout exceeds 50 MB')
    if not rows or not mapping or not 1<=first_row<=1048576 or first_row+len(rows)-1>1048576:raise ValueError('Select a valid first row and mapping')
    if any(key not in LAYOUT_FIELDS or not 1<=col<=16384 for key,col in mapping.items()) or len(set(mapping.values()))!=len(mapping):raise ValueError('Map each result field to a distinct Excel column')
    if 'RESULT VALUE' in mapping and not {'REPORTING QUALIFIER','RESULT UNIT'}.issubset(mapping):raise ValueError('Map the qualifier and unit alongside a numeric result, or map the formatted RESULT instead')
    book=load_workbook(source,keep_links=False);temp=None
    try:
        ws=book[sheet]
        for i,row in enumerate(rows,first_row):
            for key,col in mapping.items():
                cell=ws.cell(i,col)
                if isinstance(cell,MergedCell) or cell.value is not None:
                    raise ValueError(f'{ws.title}!{cell.coordinate} is occupied or merged; choose blank output cells')
                cell.value=safe_cell(row.get(key))
        notes=book.create_sheet('FoodMicro Count notes')
        notes.append([RESULT_NOTICE]);notes.column_dimensions['A'].width=103
        with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.xlsx',delete=False) as handle:temp=Path(handle.name)
        book.save(temp);os.replace(temp,target)
    finally:
        book.close()
        if temp:temp.unlink(missing_ok=True)


def export_bundle(project: Project, store, destination: str | Path) -> dict:
    """Raw data + complete audit; no official COA and no automatic result authorisation."""
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    rows=training_rows(project);results=sample_results(project)
    with (destination/'Training_compatible.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(TRAINING_HEADERS);w.writerows([[safe_cell(v) for v in row] for row in rows])
    with (destination/'Sample_results.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['SAMPLE ID','TEST ID','TEST','RESULT','VALUE','QUALIFIER','UNIT','METHOD','STATUS','AUTHORISED','REVIEW STATUS'])
        for r in results:w.writerow([safe_cell(r['sample_id']),safe_cell(r['test_id']),safe_cell(r['test']),r['display'],r['result']['value'],r['result']['qualifier'],r['result']['unit'],r['result']['method_id'],r['result']['status'],False,r['review_status']])
    # Runtime export intentionally uses a small Python Excel dependency suitable for offline Windows.
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb=Workbook();ws=wb.active;ws.title='Training compatible';ws.append(TRAINING_HEADERS)
    for i,row in enumerate(rows,2):
        ws.append([safe_cell(x) for x in row])
        # Formula is a cross-check only; the stored Python-engine result stays authoritative.
        # Blank confirmation is explicitly labelled unadjusted, never assumed confirmed.
        if row[13] is not None:
            expression=f'L{i}*IF(M{i}="",1,M{i}/100)'
            if row[19]=='iso7218_2007_2013_arithmetic':expression=f'ROUND({expression},0)'
            ws.cell(i,30,f'={expression}*I{i}/J{i}')
    summary=wb.create_sheet('Sample results')
    summary.append(['SAMPLE ID','TEST ID','TEST','RESULT TEXT','ENGINE VALUE','QUALIFIER','UNIT','METHOD VERSION','STATUS','AUTHORISED','REVIEW STATUS'])
    for r in results:summary.append([safe_cell(r['sample_id']),safe_cell(r['test_id']),safe_cell(r['test']),r['display'],r['result']['value'],r['result']['qualifier'],r['result']['unit'],r['result']['method_id'],r['result']['status'],False,r['review_status']])
    trace=wb.create_sheet('Traceability');trace.append(['PLATE ID','IMAGE SHA256','PROFILE VERSION','ALGORITHM VERSION','PARAMETERS','INITIAL AUTO','REVIEWED COUNT','EDIT EVENTS','REVIEWER','REVIEWED AT'])
    for p in project.plates:
        trace.append([p.id,project.images[p.image_id]['sha256'],p.parameters.get('profile_version'),p.parameters.get('algorithm_version'),json.dumps(p.parameters),p.automatic_count,p.count if p.review_status=='reviewed' else None,len(p.edits),p.reviewer,p.reviewed_at])
    plates=wb.create_sheet('Plate Observations');plates.append(['PLATE ID','IMAGE','SAMPLE ID','TEST ID','TEST','MEDIUM','REPLICATE','TOTAL DILUTION','VOLUME ML','MODE','AUTO COUNT','CURRENT COUNT','STATE','REVIEW','DATASET ROLE'])
    quality_sheet=wb.create_sheet('Data Quality and Review');quality_sheet.append(['PLATE ID','REVIEW','BOUNDARY CHECKED','QUALITY ACKNOWLEDGED','WARNINGS','CORRECTIONS'])
    paired=wb.create_sheet('Manual versus Automatic Counts');paired.append(['PLATE ID','IMAGE SHA256','OBSERVATION ID','MODE','COUNT','AUTOMATIC COUNT','INDEPENDENT REFERENCE DECLARED','ANALYST','DATE','DATASET ROLE'])
    for p in project.plates:
        m=p.meta
        plates.append([safe_cell(v) for v in [p.id,project.images[p.image_id]['original_name'],m.sample_id,m.test_id,m.test,m.medium,m.replicate,m.total_fraction,m.plated_ml,p.count_mode,p.automatic_count if p.count_mode!='manual' else None,p.count,m.observation,p.review_status,p.dataset_role]])
        quality_sheet.append([p.id,p.review_status,p.boundary_reviewed,p.quality_acknowledged,'; '.join(p.quality.get('warnings',[])),len(p.edits)])
        observations=list(p.count_layers)+[{'id':'current','mode':p.count_mode,'count':p.count,'automatic': [asdict(d) for d in p.automatic],'independent_reference':False,'actor':p.reviewer or m.analyst,'time':p.reviewed_at,'dataset_role':p.dataset_role}]
        for observation in observations:
            paired.append([safe_cell(v) for v in [p.id,project.images[p.image_id]['sha256'],observation['id'],observation['mode'],observation['count'],sum(d['included'] for d in observation['automatic']) if observation['mode']!='manual' else None,observation.get('independent_reference',False),observation.get('actor',''),observation.get('time',''),observation.get('dataset_role',p.dataset_role)]])
    details=wb.create_sheet('Calculation Details');details.append(['SAMPLE ID','TEST ID','METHOD','RESULT','QUALIFIER','PLATES','INPUTS / EQUATION / WARNINGS'])
    for r in results:details.append([safe_cell(r['sample_id']),safe_cell(r['test_id']),r['result']['method_id'],r['display'],r['result']['qualifier'],', '.join(r['plates']),json.dumps(r['result'],ensure_ascii=False)])
    notes=wb.create_sheet('Read me')
    for line in [
        f'FoodMicro Count {__version__}',
        RESULT_NOTICE,
        'Columns A:Q preserve the source training headings. Values are independent Python engine outputs.',
        'DILUTION FACTOR USED means total reciprocal dilution including the initial preparation exactly once.',
        'COUNT (CFU) is individual plate concentration; AVERAGE COUNTS - CFU is the selected method sample result.',
        'STANDARD DEVIATION is technical log10 SD; it is blank for zero/censored/incomplete results.',
        'Formula check is optional single-plate arithmetic, not the sample selection or reporting rule.',
        'Blank confirmation means unadjusted/presumptive. It does not mean all colonies were confirmed.',
        'Qualifiers are separate typed fields. Reporting bounds are not measured concentrations.',
        'Additional serial dilutions multiply the initial inverse factor; never exponentiate a nondecimal preparation.',
        'Read traceability.json for complete object positions, manual edits, exclusions, image quality and versions.'
    ]:notes.append([line])
    if project.imported_workbooks:
        from .spreadsheet_io import records,fields,import_results,export_import
        imported=wb.create_sheet('External observations')
        imported.append(['SOURCE','SHA256','SHEET','ROW','SAMPLE ID','TEST ID','COUNT','TOTAL INVERSE DF','VOLUME ML','REVIEW','SOURCE ISSUES'])
        imported_results=wb.create_sheet('External results')
        imported_results.append(['SOURCE','SAMPLE ID','TEST ID','RESULT','QUALIFIER','METHOD','AUTHORISED'])
        for batch in project.imported_workbooks:
            export_import(batch,destination/'spreadsheet_imports'/batch['id'])
            for record in records(batch):
                f=fields(record)
                imported.append([safe_cell(v) for v in [batch['source_name'],batch['sha256'],record['sheet'],record['row'],f.get('sample_id'),f.get('test_id'),f.get('count'),f.get('total_inverse'),f.get('volume_ml'),record['review_status'],'; '.join(record['issues'])]])
            for result in import_results(batch):
                imported_results.append([safe_cell(v) for v in [batch['source_name'],result['sample_id'],result['test_id'],result['display'],result['result']['qualifier'],result['result']['method_id'],False]])
    for sheet in wb:
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='163C4D');cell.alignment=Alignment(wrap_text=True)
        sheet.row_dimensions[1].height=42
        from openpyxl.utils import get_column_letter
        for j in range(1,sheet.max_column+1):sheet.column_dimensions[get_column_letter(j)].width=23 if sheet.title!='Read me' else 115
    with (destination/'Count_observations.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerows(paired.values)
    wb.save(destination/'FoodMicro_Count_results.xlsx')
    payload=project.to_dict();payload['sample_results']=results;payload['software_version']=__version__
    (destination/'traceability.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    images=destination/'annotated';images.mkdir(exist_ok=True)
    for p in project.plates:
        annotate(read_rgb(store.image_path(project,p.image_id)),p).save(images/(p.id+'.png'))
    return {'directory':str(destination.resolve()),'plates':len(project.plates),'sample_tests':len(results),'authorised':False}

