from pathlib import Path
from dataclasses import asdict
import copy
import os
import json
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from foodmicro.models import Plate,PlateMeta,Project,Boundary,Detection,uid
from foodmicro.reporting import sample_results,export_bundle,training_rows
from foodmicro.storage import ProjectStore


def reviewed_project(tmp_path):
    from PIL import Image
    store=ProjectStore(tmp_path/'data');project=Project();source=tmp_path/'image.png';Image.new('RGB',(150,150),'gray').save(source)
    image_id=store.import_image(source,project)
    for i,n in enumerate([41,45],1):
        p=Plate(uid(),image_id,Boundary(75,75,70,70),PlateMeta(sample_id='S1',sample_name='Synthetic fixture',test_id='T1',additional_dilution_factor=100,replicate=str(i)))
        p.detections=[Detection(uid(),70,70) for j in range(n)];p.automatic=copy.deepcopy(p.detections);p.parameters={'algorithm_version':'test'};p.boundary_reviewed=True;p.mark_reviewed('Analyst');project.plates.append(p)
    return project,store


def test_review_and_export_authorisation_guards(tmp_path):
    project,store=reviewed_project(tmp_path);result=sample_results(project)[0]
    assert result['result']['value']==43000 and not result['authorised']
    info=export_bundle(project,store,tmp_path/'out');assert info['plates']==2
    from openpyxl import load_workbook
    wb=load_workbook(tmp_path/'out/FoodMicro_Count_results.xlsx')
    ws=wb['Training compatible'];assert ws['I2'].value==1000 and ws['N2'].value==41000 and ws['P2'].value==43000
    assert ws['AD2'].value=='=L2*IF(M2="",1,M2/100)*I2/J2'
    assert json.loads((tmp_path/'out/traceability.json').read_text(encoding='utf-8'))['sample_results'][0]['authorised'] is False
    project.plates[0].invalidate_review();draft=sample_results(project)[0]
    assert draft['result']['value']==43000 and draft['review_status']=='not reviewed' and not draft['authorised']


def test_inconsistent_prep_qualitative_and_spreading_blocked(tmp_path):
    project,store=reviewed_project(tmp_path)
    project.plates[1].meta.first_diluent_ml=190;assert sample_results(project)[0]['result']['value'] is None
    project.plates[1].meta.first_diluent_ml=90;project.plates[0].meta.medium='XLD'
    assert sample_results(project)[0]['result']['value'] is None
    project.plates[0].meta.medium='PCA';project.plates[0].exclusions=[{'kind':'spreading','rect':[1,1,20,20]}]
    assert sample_results(project)[0]['result']['value'] is None


def test_csv_excel_formula_injection_prevented(tmp_path):
    project,store=reviewed_project(tmp_path)
    for p in project.plates:p.meta.sample_name='=1+2';p.meta.sample_id='@SUM(A1)'
    for p in project.plates:p.mark_reviewed('Analyst')
    export_bundle(project,store,tmp_path/'out')
    from openpyxl import load_workbook
    ws=load_workbook(tmp_path/'out/FoodMicro_Count_results.xlsx')['Training compatible']
    assert ws['B2'].data_type=='s' and ws['B2'].value=="'=1+2"


def test_out_of_band_change_cannot_reuse_review(tmp_path):
    project,store=reviewed_project(tmp_path)
    project.plates[0].meta.plated_ml=.1
    draft=sample_results(project)[0]
    assert draft['result']['value']==pytest.approx(86/.0011) and draft['review_status']=='not reviewed'


def test_desktop_canvas_review_save_reload(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPointF,Qt
    from PySide6.QtTest import QTest
    from foodmicro.app import MainWindow
    app=QApplication.instance() or QApplication([]);w=MainWindow(tmp_path/'data')
    assert 'Developed by Yaw Agyei Gyasi' in w.credit.text()
    assert 'https://linktr.ee/yawgyasi' in w.credit.text()
    assert not w.windowIcon().isNull()
    assert w.brand.pixmap() is not None and not w.brand.pixmap().isNull()
    errors=[];w.error=lambda e:errors.append(str(e));w.show();app.processEvents()
    demo=Path(__file__).resolve().parents[1]/'demo/synthetic_plates.png'
    w.actor.setText('GUI tester');w.open_image(str(demo));app.processEvents();assert len(w.project.plates)==2
    w.run_count();assert w.plate.count==44
    w.set_tool('Add')
    pos=w.counter_view.mapFromScene(QPointF(285,350));QTest.mouseClick(w.counter_view.viewport(),Qt.MouseButton.LeftButton,pos=pos)
    assert w.plate.count==45
    w.set_tool('Remove');QTest.mouseClick(w.counter_view.viewport(),Qt.MouseButton.LeftButton,pos=pos);assert w.plate.count==44
    w.fields['sample_id'].setText('TEST-S1');w.fields['test_id'].setText('TEST-T1');w.boundary_ok.setChecked(True);w.quality_ok.setChecked(True);w.review()
    assert not errors and w.plate.review_status=='reviewed'
    restored=w.store.load(w.project.id);assert restored.plates[0].count==44 and restored.plates[0].review_status=='reviewed'
    w.fields['plated_ml'].setValue(.1);assert w.plate.review_status=='draft'
    # A medium change cannot reuse the previous medium's detection silently.
    w.medium.setCurrentText('VRBLA');assert w.plate.quality['settings_changed']
    w.dirty=False;w.close()

