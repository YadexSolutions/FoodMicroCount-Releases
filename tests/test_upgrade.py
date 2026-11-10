from pathlib import Path
import copy
import json
import time
import pytest
from foodmicro.models import Project,Plate,PlateMeta,Boundary,Detection,uid
from foodmicro.storage import ProjectStore
from foodmicro.media_library import MediaLibrary

ROOT=Path(__file__).resolve().parents[1]

def test_legacy_manual_layers_review_and_backup(tmp_path):
    from PIL import Image
    source=tmp_path/'image.png';Image.new('RGB',(100,100),'gray').save(source)
    store=ProjectStore(tmp_path/'data');project=Project();image_id=store.import_image(source,project)
    p=Plate(uid(),image_id,Boundary(50,50,45,45),PlateMeta(sample_id='S1',test_id='T1'))
    project.plates.append(p)
    legacy=project.to_dict()
    for field in ('count_mode','count_layers','dataset_role'):legacy['plates'][0].pop(field)
    assert Project.from_dict(legacy).plates[0].count_mode=='automatic'
    p.automatic=[Detection('a',40,40)];p.detections=copy.deepcopy(p.automatic);p.parameters={'algorithm_version':'test'}
    p.begin_manual('Analyst','Independent reference count');p.edit('add',[],'Analyst','Manual count',[(50,50),(55,55)])
    layer=p.retain_layer('Analyst','Retain reference',independent=True)
    assert p.count==2 and p.automatic_count==0 and p.count_layers[-1]['independent_reference']
    p.boundary_reviewed=True;p.quality_acknowledged=True;p.mark_reviewed('Analyst')
    store.save(project);restored=store.load(project.id);assert restored.plates[0].count==2
    p.restore_layer(p.count_layers[0]['id'],'Analyst','Compare automatic');assert p.count==1
    p.restore_layer(layer,'Analyst','Compare manual');assert p.count==2
    store.save(project);backup=store.backup(tmp_path/'backup');assert ProjectStore(backup).load(project.id).plates[0].count==2

def test_custom_media_revision_attachment_import_backup(tmp_path):
    library=MediaLibrary(tmp_path/'data');entry=copy.deepcopy(library.database()['media']['PCA'])
    entry['name']='Local PCA';entry['custom_settings']={'polarity':'both','min_contrast':5}
    assert library.save('LOCAL-PCA',entry,'A','Local preset')==1
    entry['positive_appearance']='Locally documented';library.save('LOCAL-PCA',entry,'A','Correct note')
    pdf=tmp_path/'sheet.pdf';pdf.write_bytes(b'%PDF-1.4\n%%EOF')
    library.attach('LOCAL-PCA',pdf,'A','Attach document',revision='unknown')
    record=library.database()['media']['LOCAL-PCA']['attachments'][0]
    assert library.attachment_path(record).read_bytes()==pdf.read_bytes()
    library.restore('LOCAL-PCA',1,'A','Restore');assert library.database()['media']['LOCAL-PCA']['positive_appearance']!=entry['positive_appearance']
    exported=tmp_path/'media.json';library.export(exported);other=MediaLibrary(tmp_path/'other');other.import_file(exported,'A');assert 'LOCAL-PCA' in other.database()['media']
    assert MediaLibrary(tmp_path/'data').database()['media']['LOCAL-PCA']['local_revision']==4
    store=ProjectStore(tmp_path/'data');backup=store.backup(tmp_path/'backup')
    assert MediaLibrary(backup).database()['media']['LOCAL-PCA']['name']=='Local PCA'
    bad=tmp_path/'bad.pdf';bad.write_bytes(b'not pdf')
    with pytest.raises(ValueError):library.attach('PCA',bad,'A','bad')

def test_explicit_excel_column_mapping(tmp_path):
    from openpyxl import Workbook
    from foodmicro.spreadsheet_io import import_workbook,records,fields
    wb=Workbook();ws=wb.active;ws.title='Custom';ws.append(['Identifier','Objects','Factor','Inoculum']);ws.append(['S1',12,100,.1]);path=tmp_path/'custom.xlsx';wb.save(path)
    batch=import_workbook(path,'Custom',{1:'sample_id',2:'count',3:'total_inverse',4:'volume_ml'})
    assert fields(records(batch)[0])['count']==12
    assert records(batch)[0]['cells']['B2']['original']==12

def test_ui_batch_manual_quick_and_preferences(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QPointF
    from PySide6.QtTest import QTest
    from foodmicro.app import MainWindow
    app=QApplication.instance() or QApplication([]);w=MainWindow(tmp_path/'data');w.show();app.processEvents();errors=[];w.error=lambda e:errors.append(str(e));w.actor.setText('Tester')
    bad=tmp_path/'corrupt.png';bad.write_bytes(b'bad')
    w.import_paths([str(ROOT/'demo/synthetic_plates.png'),str(bad)],setup={'test':'Yeasts','medium':'DRBC'},ask_group=False)
    deadline=time.monotonic()+10
    while w.task and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert w.task is None and len(w.project.plates)==2 and errors and 'corrupt' in errors[0]
    assert all(p.meta.medium=='DRBC' and p.meta.test=='Yeasts' for p in w.project.plates)
    assert len({p.meta.test_id for p in w.project.plates})==2
    errors.clear();w.manual_mode();app.processEvents()
    point=w.counter_view.mapFromScene(QPointF(w.plate.boundary.cx,w.plate.boundary.cy))
    QTest.mouseClick(w.counter_view.viewport(),Qt.MouseButton.LeftButton,pos=point);assert w.plate.count==1
    QTest.mouseClick(w.counter_view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier,pos=point);assert w.plate.count==0
    w.history(False);assert w.plate.count==1
    prior=w.controls['min_contrast'].value();w.advanced.setChecked(True);w.advanced.setChecked(False);assert w.plate.count==1 and w.controls['min_contrast'].value()==prior
    w.fields['sample_id'].setText('S1');w.fields['test_id'].setText('T1');w.boundary_ok.setChecked(True);w.quality_ok.setChecked(True);w.review()
    assert not errors and w.plate.review_status=='reviewed'
    w.dirty=False;w.close();next_window=MainWindow(tmp_path/'data');assert not next_window.advanced.isChecked();next_window.close()

def test_group_assignment_and_drop_routing(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QMimeData,QUrl,QPointF,QPoint
    from PySide6.QtGui import QDropEvent,QDragEnterEvent
    from foodmicro.app import MainWindow
    from foodmicro.ui_support import GroupDialog,ImportSetup
    app=QApplication.instance() or QApplication([]);w=MainWindow(tmp_path/'data');w.actor.setText('A');w.open_image(str(ROOT/'demo/synthetic_plates.png'))
    setup=ImportSetup(w,w.library,{'medium':'VRBLA','test':'Total Coliforms'},2);assert setup.values()['medium']=='VRBLA';setup.reject()
    dialog=GroupDialog(w,w.project.plates,w.project,w.library);dialog.relation.setCurrentIndex(1);dialog.sample.setText('S1');dialog.apply_relationship();dialog.commit()
    assert len({p.meta.test_id for p in w.project.plates})==1 and [p.meta.replicate for p in w.project.plates]==['1','2']
    captured=[];w.import_paths=lambda paths:captured.extend(paths)
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(ROOT/'demo/synthetic_plates.png'))])
    enter=QDragEnterEvent(QPoint(10,10),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);w.dragEnterEvent(enter);assert enter.isAccepted()
    drop=QDropEvent(QPointF(10,10),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);w.dropEvent(drop);assert len(captured)==1
    w.dirty=False;w.close()


def test_background_count_and_custom_profile(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QTimer
    from foodmicro.app import MainWindow
    from foodmicro.vision import count_colonies,read_rgb,CounterSettings
    app=QApplication.instance() or QApplication([]);w=MainWindow(tmp_path/'data');w.actor.setText('A');errors=[];w.error=lambda e:errors.append(str(e));w.open_image(str(ROOT/'demo/synthetic_plates.png'))
    ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(1)
    w.run_count(asynchronous=True);deadline=time.monotonic()+10
    while w.task and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    timer.stop();assert not errors and w.plate.count==44 and ticks
    custom=copy.deepcopy(w.library.database()['media']['PCA']);w.library.save('CUSTOM',custom,'A','custom preset')
    detected,_,_=count_colonies(read_rgb(ROOT/'demo/synthetic_plates.png'),w.plate.boundary,'CUSTOM',CounterSettings(),profile_override=custom)
    assert len(detected)==44
    w.dirty=False;w.close()


def test_observation_export_preserves_independent_layers(tmp_path):
    from test_reporting_gui import reviewed_project
    from foodmicro.reporting import export_bundle
    from openpyxl import load_workbook
    project,store=reviewed_project(tmp_path);p=project.plates[0]
    p.retain_layer('A','Automatic proposal');p.begin_manual('B','Blind manual observation');p.edit('add',[],'B','Manual count',[(70,70)])
    p.retain_layer('B','Independent reference',True)
    export_bundle(project,store,tmp_path/'out');wb=load_workbook(tmp_path/'out/FoodMicro_Count_results.xlsx')
    assert {'Plate Observations','Calculation Details','Manual versus Automatic Counts','Data Quality and Review'}.issubset(wb.sheetnames)
    ws=wb['Manual versus Automatic Counts'];assert any(ws.cell(r,7).value is True for r in range(2,ws.max_row+1))
    assert (tmp_path/'out/Count_observations.csv').exists()


def test_cancel_worker_and_paste_table(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from foodmicro.app import MainWindow
    from foodmicro.ui_support import PasteTable
    app=QApplication.instance() or QApplication([]);w=MainWindow(tmp_path/'data');results=[]
    def work(cancel,progress):
        cancel.wait(.2)
        return 'should not commit'
    w.start_work(work,results.append);w.cancel_work();deadline=time.monotonic()+3
    while w.task and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    assert w.task is None and not results
    table=PasteTable(2,2);table.setCurrentCell(0,0);QApplication.clipboard().setText('S1\t42\nS2\t0');QTest.keyClick(table,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier)
    assert table.item(0,1).text()=='42' and table.item(1,1).text()=='0'
    w.close()


def test_updated_template_recalculates_through_engine():
    from foodmicro.spreadsheet_io import import_workbook,records,review_rows,import_results
    from openpyxl import load_workbook
    path=ROOT/'foodmicro/assets/example_template.xlsx'
    batch=import_workbook(path,'Count data');review_rows(batch,[r['id'] for r in records(batch)],{},'A','Check synthetic inputs')
    result=import_results(batch);assert result[0]['result']['value']==43000
    assert result[1]['result']['value']==10 and result[1]['result']['qualifier']=='less_than'
    wb=load_workbook(path,data_only=True);ws=wb['Count data'];assert ws['W2'].value==41000 and ws['W3'].value==45000 and ws['W4'].value in (None,'')


def test_restore_bundled_preset_preserves_local_reference_fields(tmp_path):
    library = MediaLibrary(tmp_path)
    original = library.database()['media']['PCA']
    custom = copy.deepcopy(original)
    custom['vision']['polarity'] = 'dark'
    custom['custom_settings'] = {'min_contrast': 5}
    custom['positive_appearance'] = 'Local control notes'
    library.save('PCA', custom, 'Analyst', 'Adjust detector')
    previous = library.database()['media']['PCA']

    assert library.restore_default_preset('PCA', 'Analyst') == 2
    current = MediaLibrary(tmp_path).database()['media']['PCA']
    assert current['vision'] == original['vision']
    assert 'custom_settings' not in current
    assert current['positive_appearance'] == 'Local control notes'
    assert previous['vision']['polarity'] == 'dark'
