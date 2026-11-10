from copy import deepcopy
from io import BytesIO
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from foodmicro.ai_review import AIReviewError, ENDPOINT, Suggestion, make_crop, review_marks
from foodmicro.models import Boundary, Detection, Plate
from foodmicro.vision import CounterSettings, count_colonies


def ellipse(draw, x, y, r, rgb):
    draw.ellipse((x-r,y-r,x+r,y+r), fill=rgb)


@pytest.mark.parametrize('scale', [1, 2])
def test_medium_size_ranges_preserve_broad_colony_and_compact_targets(scale):
    im=Image.new('RGB',(440*scale,440*scale),(169,113,132))
    draw=ImageDraw.Draw(im)
    # Filament-like irregular lobes with a green/cream centre, plus two compact
    # marks of different colours. Geometry is known for this synthetic image.
    x,y=155*scale,200*scale
    for dx,dy,r in [(0,0,29),(-17,3,14),(10,-18,15),(20,11,15)]:
        ellipse(draw,x+dx*scale,y+dy*scale,r*scale,(118,161,136))
    ellipse(draw,x,y,17*scale,(99,144,121))
    for u,v,c in [(262,154,(226,150,154)),(264,255,(88,45,91))]:
        ellipse(draw,u*scale,v*scale,3*scale,c)
    b=Boundary(220*scale,220*scale,201*scale,198*scale)
    ds,q,_=count_colonies(np.asarray(im),b,'DRBC',test='Yeasts and moulds')
    counted=[d for d in ds if d.included]
    assert sorted(d.category for d in counted)==['mould-like','yeast-like','yeast-like']
    mould=next(d for d in counted if d.category=='mould-like')
    assert math.hypot(mould.x-x,mould.y-y)<8*scale and mould.radius>20*scale
    assert len(mould.features['outline'])>=3
    assert q['mould_radius_px'][1]==pytest.approx(2*math.sqrt(b.rx*b.ry)/90*17)
    for test,expected,kept in [('Yeasts',2,'yeast-like'),('Moulds',1,'mould-like')]:
        selected,_,_=count_colonies(np.asarray(im),b,'DRBC',test=test)
        assert len(selected)==len(ds)
        assert [d.category for d in selected if d.included]==[kept]*expected


def test_spread_zone_holds_internal_pinpoints_separately():
    im=Image.new('RGB',(440,440),(138,137,128))
    draw=ImageDraw.Draw(im)
    draw.polygon([(89,182),(140,147),(200,155),(228,179),(204,228),(157,246),(104,216)],
                 fill=(174,172,155))
    for xy in [(135,190),(166,177),(193,202)]:
        ellipse(draw,*xy,2,(221,219,205))
    ellipse(draw,304,240,5,(211,211,190))
    b=Boundary(220,220,203,203)
    ds,q,_=count_colonies(np.asarray(im),b,'PCA')
    spread=[d for d in ds if d.features.get('candidate_type')=='spread-region']
    detail=[d for d in ds if d.features.get('candidate_type')=='pinpoint-in-spread']
    assert len(spread)==1 and not spread[0].included and spread[0].features['outline']
    assert q['visible_review_marks']['pinpoint-in-spread']>=3
    for x,y in [(135,190),(166,177),(193,202)]:
        assert any(math.hypot(x-d.x,y-d.y)<2 and not d.included for d in detail)
    assert [(round(d.x),round(d.y)) for d in ds if d.included]==[(304,240)]
    ds2,_,_=count_colonies(np.asarray(im),b,'PCA',CounterSettings(inspect_spreading=False))
    assert all(d.features.get('candidate_type') not in ('spread-region','pinpoint-in-spread') for d in ds2)


def test_ink_and_glare_do_not_remove_dark_round_candidates():
    im=Image.new('RGB',(420,420),(179,171,147))
    draw=ImageDraw.Draw(im)
    draw.line((142,138,197,156), fill=(24,17,12), width=4)
    ellipse(draw,257,257,5,(29,22,18))
    ellipse(draw,270,119,3,(13,53,196))
    draw.line((190,290,240,297), fill=(255,255,255), width=4)
    b=Boundary(210,210,190,190)
    ds,q,_=count_colonies(np.asarray(im),b,'Baird-Parker',CounterSettings(rim_inset=0))
    assert q['visible_review_marks']['possible-ink']>=2
    assert q['visible_review_marks']['possible-glare']==1
    assert all(not d.included for d in ds if d.features.get('candidate_type','').startswith('possible-'))
    assert [(round(d.x),round(d.y)) for d in ds if d.included]==[(257,257)]


def response(rows):
    return json.dumps({'status':'completed','output':[{'type':'message','content':[
        {'type':'output_text','text':json.dumps({'reviews':rows})}]}]}).encode()


def test_online_request_uses_preview_crop_and_review_only():
    im=np.zeros((400,400,3),dtype=np.uint8)
    im[:]=[220,15,28]
    b=Boundary(190,190,147,147)
    crop=make_crop(im,b,50,190,'A1',Detection('PRIVATE-PLATE-ID',50,190))
    image=Image.open(BytesIO(crop.png))
    assert image.size==(51,51) and image.getpixel((0,25))==(40,40,40)
    assert image.getpixel((25,25))==(220,15,28)
    class Stub:
        def open(self, req, timeout):
            assert req.full_url==ENDPOINT and req.method=='POST'
            assert req.get_header('Authorization')=='Bearer test-only'
            payload=json.loads(req.data)
            assert payload['store'] is False
            assert b'PRIVATE-PLATE-ID' not in req.data and b'local record' not in req.data
            assert payload['input'][0]['content'][2]['type']=='input_image'
            return BytesIO(response([{'id':'A1','appearance':'ink-or-glare','reason':'Narrow straight edge.'}]))
    assert review_marks([crop],'local record','test-only',opener=Stub())==[Suggestion('A1','ink-or-glare','Narrow straight edge.')]


@pytest.mark.parametrize('rows',[
    [{'id':'invented','appearance':'colony-like','reason':'Circle'}],
    [{'id':'A1','appearance':'confirmed species','reason':'Circle'}],
    [],
])
def test_bad_online_response_cannot_supply_new_count(rows):
    b=Boundary(50,50,46,46)
    crop=make_crop(np.zeros((100,100,3),dtype=np.uint8),b,50,50,'A1')
    class Stub:
        def open(self, *args, **kwargs):return BytesIO(response(rows))
    with pytest.raises(AIReviewError,match='unchanged'):
        review_marks([crop],'PCA','test-only',opener=Stub())


def test_focus_cursor_and_analyst_decision_are_preserved(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPointF,Qt
    from PySide6.QtTest import QTest
    from foodmicro.app import MainWindow
    from foodmicro.ai_dialogs import SendReview,ReviewSuggestions

    app=QApplication.instance() or QApplication([])
    win=MainWindow(tmp_path/'store')
    win.actor.setText('Counting analyst')
    path=tmp_path/'plate.png'
    Image.new('RGB',(400,400),(158,150,130)).save(path)
    image_id=win.store.import_image(path,win.project)
    win.rgb[image_id]=np.asarray(Image.open(path))
    mark=Detection('suspect',180,180,category='artefact',included=False,features={'candidate_type':'possible-ink'})
    plate=Plate('plate',image_id,Boundary(200,200,180,180),automatic=[deepcopy(mark)],detections=[mark])
    plate.parameters['algorithm_version']='test'
    win.project.plates.append(plate)
    win.refresh_plate_list()
    win.select_plate(0)
    win.show()
    app.processEvents()
    assert not win.quick_mode.isChecked()
    win.toggle_focus()
    app.processEvents()
    assert win.focus_dialog.isFullScreen() and win.quick_mode.isChecked()
    cursor=win.counter_view.viewport().cursor()
    assert not cursor.pixmap().isNull()
    p=cursor.hotSpot()
    assert cursor.pixmap().toImage().pixelColor(p).red()>200
    point=win.counter_view.mapFromScene(QPointF(230,230))
    QTest.mouseClick(win.counter_view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    win.toggle_focus()
    assert plate.count==1 and not win.quick_mode.isChecked()
    menu=win.make_plate_menu(180,180)
    assert any('AI Assist' in action.text() for action in menu.actions())
    crop=make_crop(win.rgb[image_id],plate.boundary,180,180,'A1',mark)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    preview=SendReview(win,[crop])
    assert not preview.api_key()
    preview.key.setText('test-only')
    assert preview.api_key()=='test-only'
    result=Suggestion('A1','colony-like','Synthetic example')
    dialog=ReviewSuggestions(win,[crop],[result],{'suspect':mark})
    assert dialog.choices[0].currentData()=='keep'
    win.apply_ai_choices(plate,[crop],[result],['keep'])
    assert plate.count==1
    win.apply_ai_choices(plate,[crop],[result],['include'])
    assert plate.count==2 and plate.automatic[0].included is False
    assert 'AI Assist' in plate.edits[-1]['reason'] and plate.edits[-1]['actor']=='Counting analyst'
    win.dirty=False
    win.close()


def test_ai_preview_is_required_and_worker_suggestions_do_not_edit(tmp_path,monkeypatch):
    import threading
    import time
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import foodmicro.app as desktop

    app=QApplication.instance() or QApplication([])
    win=desktop.MainWindow(tmp_path)
    win.actor.setText('Tester')
    image=tmp_path/'sample.png'
    Image.new('RGB',(180,180),(160,150,155)).save(image)
    image_id=win.store.import_image(image,win.project)
    win.rgb[image_id]=np.asarray(Image.open(image))
    p=Plate('plate',image_id,Boundary(90,90,82,82))
    win.project.plates.append(p)
    win.refresh_plate_list()
    win.select_plate(0)
    calls=[]
    main=threading.get_ident()
    class Preview(desktop.SendReview):
        proceed=False
        def exec(self):return int(self.proceed)
        def api_key(self):return 'test-only'
    class Choices(desktop.ReviewSuggestions):
        def exec(self):
            calls.append(('suggestions',threading.get_ident()==main))
            return 0
    def mock_review(crops,medium,key):
        calls.append(('request',threading.get_ident()!=main))
        return [Suggestion(crops[0].label,'colony-like','Visible compact centre.')]
    monkeypatch.setattr(desktop,'SendReview',Preview)
    monkeypatch.setattr(desktop,'ReviewSuggestions',Choices)
    monkeypatch.setattr(desktop,'review_marks',mock_review)
    win.ai_review_spots([(90,90,None)])
    assert not calls and win.task is None
    Preview.proceed=True
    win.ai_review_spots([(90,90,None)])
    deadline=time.monotonic()+5
    while win.task and time.monotonic()<deadline:
        app.processEvents();QTest.qWait(10)
    assert win.task is None and calls==[('request',True),('suggestions',True)]
    assert p.count==0 and not p.edits
    win.dirty=False
    win.close()
