import hashlib
import json
from pathlib import Path
import pytest
from openpyxl import Workbook,load_workbook
from foodmicro.models import Project
from foodmicro.storage import ProjectStore
from foodmicro.spreadsheet_io import import_workbook,records,fields,review_rows,import_results,observation,export_import


def workbook(tmp_path):
    p=tmp_path/'training.xlsx';wb=Workbook();ws=wb.active;ws.title='Counts'
    ws.append(['SAMPLE ID','TEST','TEST REPLICATION','NUMBER OF COLONIES','DILUTION FACTOR USED','VOLUME OF INOCULUM','COUNT (CFU)','%CONFIRMED COLONIES'])
    ws.append(['SYN-1','APC',1,41,1000,1,.41,100])
    ws.append([None,'APC',2,45,1000,1,.45,100]);wb.save(p);return p


def reviewed(tmp_path):
    batch=import_workbook(workbook(tmp_path));rows=records(batch)
    review_rows(batch,[r['id'] for r in rows],{'sample_id':'SYN-1','test_id':'T-1','unit':'CFU/g','method_id':'basic_exposure_v1'},'Analyst','Verified original total dilution and plate volumes')
    return batch


def test_import_never_fills_blank_identity_or_trusts_source_cfu(tmp_path):
    p=workbook(tmp_path);sha=hashlib.sha256(p.read_bytes()).hexdigest();batch=import_workbook(p);rows=records(batch)
    assert rows[1]['fields']['sample_id'] is None
    assert rows[0]['cells']['G2']['original']==.41
    assert 'recomputed 41000' in ';'.join(rows[0]['issues'])
    assert all(r['result']['value'] is None for r in import_results(batch))
    assert hashlib.sha256(p.read_bytes()).hexdigest()==sha


def test_explicit_external_review_recalculates_duplicate_and_preserves_raw(tmp_path):
    batch=reviewed(tmp_path);result=import_results(batch)[0]
    assert result['result']['value']==43000 and not result['authorised']
    assert records(batch)[1]['fields']['sample_id'] is None
    assert records(batch)[0]['events'][0]['actor']=='Analyst'
    export_import(batch,tmp_path/'report')
    assert json.loads((tmp_path/'report/spreadsheet_audit.json').read_text(encoding='utf-8'))['results'][0]['result']['value']==43000
    assert '41000' in (tmp_path/'report/spreadsheet_reconciliation.csv').read_text(encoding='utf-8-sig')
    records(batch)[0]['overrides']['volume_ml']=.1
    updated=import_results(batch)[0]
    assert updated['result']['value']==pytest.approx(86/.0011) and updated['review_status']=='not reviewed'


def test_review_atomic_and_fractional_confirmation_not_invented(tmp_path):
    batch=import_workbook(workbook(tmp_path));rows=records(batch)
    with pytest.raises(ValueError,match='sample_id'):
        review_rows(batch,[r['id'] for r in rows],{'test_id':'T','unit':'CFU/g','method_id':'basic_exposure_v1'},'A','Check')
    assert all(r['review_status']=='draft' for r in rows)
    rows[0]['fields']['confirmation_percent']=50
    with pytest.raises(ValueError,match='percentage alone'):observation(rows[0])
    review_rows(batch,[rows[0]['id']],{'tested':10,'confirmed':5,'test_id':'T','unit':'CFU/g','method_id':'basic_exposure_v1'},'A','Confirmation record checked')
    assert observation(rows[0]).adjusted()==20.5


def test_formula_cache_missing_not_executed(tmp_path):
    p=workbook(tmp_path);wb=load_workbook(p);wb.active['E2']='=1/0';wb.save(p)
    batch=import_workbook(p);row=records(batch)[0]
    assert row['fields']['total_inverse'] is None and row['cells']['E2']['original']=='=1/0'
    assert 'no cached value' in ';'.join(row['issues'])
    with pytest.raises(ValueError):observation(row)


def test_percentage_number_format_and_qualitative_guard(tmp_path):
    p=workbook(tmp_path);wb=load_workbook(p);ws=wb.active;ws['H2']=.5;ws['H2'].number_format='0%';wb.save(p)
    row=records(import_workbook(p))[0];assert row['fields']['confirmation_percent']==50
    with pytest.raises(ValueError):observation(row)
    batch=reviewed(tmp_path);review_rows(batch,[r['id'] for r in records(batch)],{'test':'Salmonella'},'A','Review portion route')
    assert import_results(batch)[0]['result']['value'] is None


def test_preserved_workbook_hash_backup_and_export(tmp_path):
    from foodmicro.reporting import export_bundle
    p=workbook(tmp_path);store=ProjectStore(tmp_path/'data');project=Project();batch=store.import_workbook(p,project)
    review_rows(batch,[r['id'] for r in records(batch)],{'sample_id':'SYN-1','test_id':'T-1','unit':'CFU/g','method_id':'basic_exposure_v1'},'A','Checked')
    store.save(project);export_bundle(project,store,tmp_path/'export')
    wb=load_workbook(tmp_path/'export/FoodMicro_Count_results.xlsx');assert wb['External results']['D2'].value=='4.3 × 10⁴ CFU/g'
    store.backup(tmp_path/'backup');restored=ProjectStore(tmp_path/'backup').load(project.id)
    assert import_results(restored.imported_workbooks[0])[0]['result']['value']==43000
    (store.root/batch['file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='hash/path'):store.load(project.id)


def test_import_tab_runs_without_image_or_authorisation(tmp_path):
    from PySide6.QtWidgets import QApplication
    from foodmicro.app import MainWindow
    app=QApplication.instance() or QApplication([]);window=MainWindow(tmp_path/'data');errors=[];window.error=lambda e:errors.append(str(e))
    window.actor.setText('Import tester');window.import_excel(str(workbook(tmp_path)))
    assert not errors and window.import_table.rowCount()==2
    assert 'result unit and calculation method' in window.import_table.item(0,9).text()
    assert window.save_project();assert len(window.store.load(window.project.id).imported_workbooks)==1
    window.dirty=False;window.close()


def test_delivered_template_with_missing_optional_excel_dimensions():
    path=Path(__file__).resolve().parents[1]/'tests/fixtures/synthetic-alternate-layout.xlsx'
    batch=import_workbook(path);rows=records(batch);assert len(rows)==4
    for sample in ['SYN-001','SYN-002']:
        review_rows(batch,[r['id'] for r in rows if fields(r)['sample_id']==sample],{},'Fixture reviewer','Check synthetic template')
    results=import_results(batch)
    assert results[0]['result']['value']==43000
    assert results[1]['result']['qualifier']=='less_than' and results[1]['result']['log10'] is None
