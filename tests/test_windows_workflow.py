from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from foodmicro.models import Boundary
from foodmicro.vision import CounterSettings, count_colonies, fill_small_holes


DEMO=Path(__file__).resolve().parents[1]/'demo'/'synthetic_plates.png'


def test_rim_ring_cannot_fill_the_whole_agar():
    binary=np.zeros((60,60),bool)
    binary[4:56,4:56]=True;binary[6:54,6:54]=False
    binary[21:28,21:28]=True;binary[23:25,23:25]=False
    out=fill_small_holes(binary,16)
    assert out[23:25,23:25].all()
    assert not out[30,30]
    assert out.sum()-binary.sum()==4


@pytest.mark.parametrize('strategy',['hybrid','local','log'])
def test_less_rim_exclusion_preserves_interior_candidates(strategy):
    image=Image.new('RGB',(320,320),(168,134,136));draw=ImageDraw.Draw(image)
    draw.ellipse((12,12,308,308),outline=(49,30,40),width=3)
    for x,y in [(100,100),(130,100),(200,200),(298,160),(276,160)]:
        draw.ellipse((x-3,y-3,x+3,y+3),fill=(78,28,49))
    b=Boundary(160,160,151,151)
    previous=set();counts=[];thresholds=set()
    for inset in (.13,.10,.01,0):
        ds,q,_=count_colonies(np.array(image),b,'VRBLA',CounterSettings(strategy=strategy,rim_inset=inset))
        points={(d.x,d.y) for d in ds if d.included}
        assert previous<=points
        previous=points;counts.append(len(points));thresholds.add(q['threshold'])
    assert counts[0]>=3 and counts[-1]>counts[0]
    assert len(thresholds)==1


def test_msa_appearance_mismatch_is_reviewable_but_not_counted():
    image=Image.new('RGB',(230,230),(102,46,75));draw=ImageDraw.Draw(image)
    draw.ellipse((70,110,80,120),fill=(225,177,55))
    draw.ellipse((150,110,160,120),fill=(231,120,150))
    ds,_,_=count_colonies(np.array(image),Boundary(115,115,104,104),'MSA')
    gold=min(ds,key=lambda d:abs(d.x-75));pink=min(ds,key=lambda d:abs(d.x-155))
    assert abs(gold.x-75)<2 and gold.included
    assert abs(pink.x-155)<2 and pink.category=='uncertain' and not pink.included


@pytest.fixture
def window(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from foodmicro.app import MainWindow
    app=QApplication.instance() or QApplication([])
    w=MainWindow(tmp_path/'store');errors=[];w.error=lambda e:errors.append(str(e))
    w.actor.setText('Preview analyst');w.open_image(str(DEMO));w.show();app.processEvents()
    yield w,app
    if w.focus_dialog:w.focus_dialog.reject()
    w.dirty=False;w.close()
    assert not errors


def test_four_tabs_and_correct_media_card(window):
    w,_=window
    assert [w.tabs.tabText(i).replace('&&','&') for i in range(w.tabs.count())]==['Plate Count','Data & Results','Media','Settings']
    assert not w.quality_dialog.isVisible()
    w.medium.setCurrentText('VRBLA');w.view_current_medium()
    assert w.media_panel.text.toPlainText().startswith('VRBLA —')
    assert 'Revision not recorded' in w.media_panel.text.toPlainText()
    w.media_panel.select_medium('VRBGA');assert w.media_panel.text.toPlainText().startswith('VRBGA —')
    w.media_panel.search.setText('missing test string');assert 'No matching medium' in w.media_panel.text.toPlainText()


def test_plate_label_preview_and_ellipse_handle(window):
    from PySide6.QtCore import Qt,QPoint
    from PySide6.QtTest import QTest
    from foodmicro.ui_support import GroupDialog
    w,app=window
    d=GroupDialog(w,w.project.plates,w.project,w.library)
    first=d.crop.pixmap().cacheKey();d.table.setCurrentCell(1,1)
    assert d.caption.text().startswith('Plate 2') and d.crop.pixmap().cacheKey()!=first
    assert not d.photo.pixmap().isNull();d.close()
    w.toggle_focus();app.processEvents();assert w.focus_dialog.isFullScreen() and w.counter_view.window() is w.focus_dialog
    w.set_tool('Draw boundary');old=w.plate.boundary.rx;view=w.counter_view
    start=view.mapFromScene(view.handles(w.plate.boundary)['right']);end=start-QPoint(18,0)
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(view.viewport(),end);QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end)
    assert w.plate.boundary.rx<old and w.plate.quality['settings_changed']
    w.toggle_focus();app.processEvents();assert w.focus_dialog is None and w.counter_view.parentWidget() is w.counter_panel


def test_live_rim_filter_and_tntc_review(window):
    from foodmicro.reporting import plate_observation,sample_results
    w,_=window;w.fields['sample_id'].setText('DEMO-1');w.run_count()
    ids={d.id for d in w.plate.automatic if d.included}
    assert ids
    w.controls['rim_inset'].setValue(0)
    assert ids<={d.id for d in w.plate.automatic if d.included}
    assert w.plate.parameters['rim_inset']==0 and not w.plate.quality.get('settings_changed')
    w.mark_tntc();w.boundary_ok.setChecked(True);w.quality_ok.setChecked(True);w.review()
    assert w.plate.review_status=='reviewed' and 'TNTC' in w.count_label.text()
    assert plate_observation(w.plate).count is None and sample_results(w.project)[0]['result']['value'] is None


def test_mapped_workbook_export_preserves_source_and_formula(tmp_path):
    from openpyxl import Workbook,load_workbook
    from openpyxl.styles import Font
    from foodmicro.reporting import write_result_layout
    source=tmp_path/'layout.xlsx';target=tmp_path/'export.xlsx'
    w=Workbook();s=w.active;s.title='Lab results';s.append(['ID','Result','Formula']);s['C2']='=1+2';s['B2'].font=Font(bold=True);w.save(source)
    original=source.read_bytes();data=[{'SAMPLE ID':'LAB-1','RESULT':'<10 CFU/g'}]
    write_result_layout(source,target,'Lab results',2,{'SAMPLE ID':1,'RESULT':2},data)
    assert source.read_bytes()==original
    with open(target,'rb') as file:
        b=load_workbook(file);s=b.active
        assert s['B2'].value=='<10 CFU/g' and s['B2'].font.bold and s['C2'].value=='=1+2'
        b.close()
    with pytest.raises(ValueError,match='occupied'):write_result_layout(source,target,'Lab results',2,{'RESULT':3},data)
    with pytest.raises(ValueError,match='qualifier'):write_result_layout(source,target,'Lab results',2,{'RESULT VALUE':2},data)


def test_full_screen_context_and_laser_counting_keep_edits(window):
    from PySide6.QtCore import Qt,QPoint,QPointF
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    w,app=window;w.manual_mode();view=w.counter_view
    b=w.plate.boundary
    point=view.mapFromScene(QPointF(b.cx,b.cy))
    event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,view.viewport().mapToGlobal(point))
    view.contextMenuEvent(event)
    menu=w.plate_popup;action=menu.actions()[0];assert action.text()=='Open plate full screen'
    menu.hide();action.trigger();app.processEvents();QTest.qWait(40)
    assert w.focus_dialog.isFullScreen() and view.window() is w.focus_dialog
    pointer=view.viewport().cursor();assert not pointer.pixmap().isNull() and pointer.hotSpot()==QPoint(9,9)
    point=view.mapFromScene(QPointF(b.cx,b.cy))
    QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    assert w.plate.count==1 and 'Manual count: 1' in w.focus_count.text()
    assert w.plate.edits[-1]['reason']=='Added by Quick Count'
    QTest.keyClick(view,Qt.Key.Key_Escape);app.processEvents()
    assert w.focus_dialog is None and view.parentWidget() is w.counter_panel and w.plate.count==1


def test_live_count_3680_volume_dilution_and_confirmation_without_review(window):
    from PySide6.QtTest import QTest
    from foodmicro.models import Detection,uid
    w,_=window;p=w.plate;p.detections=[Detection(uid(),120,120) for _ in range(3680)];p.parameters={'algorithm_version':'fixture'}
    w.fields['sample_id'].setText('SINGLE');w.fields['test_id'].setText('SINGLE-TEST')
    w.fields['test'].setText('Enterobacteriaceae');w.medium.setCurrentText('VRBGA');w.update_counts();QTest.qWait(120)
    assert w.plate.review_status=='draft' and 'purple' in w.appearance_label.text()
    assert '3.68 × 10⁴ CFU/g' in w.data_result.values['result'].text()
    assert '36,800 CFU/g' in w.count_result.values['result'].text()
    def value():return next(r['result']['value'] for r in w.calculated if p.id in r['plates'])
    assert value()==pytest.approx(36800)
    w.fields['plated_ml'].setValue(.5);QTest.qWait(120)
    assert value()==pytest.approx(73600)
    w.tested.setText('10');w.confirmed.setText('5');QTest.qWait(120)
    assert value()==pytest.approx(36800)
    assert '1,840' in w.count_result.values['confirmation'].text()
    w.fields['additional_dilution_factor'].setValue(10);QTest.qWait(120)
    assert value()==pytest.approx(368000)
    w.confirmed.setText('11');QTest.qWait(120)
    assert not w.calculated and '36,800' not in w.count_result.values['result'].text()


def test_single_plate_assignment_and_counting_only_catalogue(window):
    from PySide6.QtWidgets import QPushButton
    from foodmicro.ui_support import GroupDialog,TESTS
    w,_=window;p=w.plate;test_id=p.meta.test_id
    dialog=GroupDialog(w,[p],w.project,w.library)
    assert dialog.relation.currentText()=='Single plate — no relationship'
    dialog.table.item(0,1).setText('SINGLE');dialog.commit()
    assert p.meta.sample_id=='SINGLE' and p.meta.test_id==test_id
    assert 'Salmonella' not in TESTS and w.medium.findText('XLD')<0
    assert not any('qualitative' in b.text().casefold() for b in w.findChildren(QPushButton))
