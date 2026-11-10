from pathlib import Path
import json
import numpy as np
import pytest
from PIL import Image,ImageDraw
from foodmicro.vision import read_rgb,detect_plates,count_colonies,CounterSettings,ellipse_mask
from foodmicro.models import Boundary
from foodmicro.validation import detection_metrics,split_by_group

DEMO=Path(__file__).resolve().parents[1]/'demo'


def test_packaged_image_smoke():
    from foodmicro.app import smoke_test
    smoke_test()


@pytest.mark.parametrize('strategy',['hybrid','local','log'])
def test_synthetic_two_plate_detection(strategy):
    image=read_rgb(DEMO/'synthetic_plates.png');truth=json.loads((DEMO/'synthetic_reference.json').read_text())
    bs=detect_plates(image);assert len(bs)==2
    for b,t in zip(bs,truth):
        assert abs(b.cx-t['boundary'][0])<15 and abs(b.cy-t['boundary'][1])<15
        ds,q,_=count_colonies(image,b,'PCA',CounterSettings(strategy=strategy))
        metrics=detection_metrics([(d.x,d.y) for d in ds],t['reference_points'],8)
        assert metrics['recall']>=.9 and metrics['precision']>=.9
        assert all(b.contains(d.x,d.y) for d in ds)
        assert q['algorithm_version'] and q['warnings']


def test_blank_agar_does_not_invent_colonies():
    im=np.zeros((300,300,3),np.uint8);im[:]=[135,140,125]
    ds,q,_=count_colonies(im,Boundary(150,150,145,145),'PCA')
    assert ds==[]


def test_no_hard_circularity_filter_touching_and_dark():
    im=Image.new('RGB',(300,300),(130,130,130));d=ImageDraw.Draw(im)
    d.ellipse((100,100,116,116),fill='white');d.ellipse((114,100,130,116),fill='white');d.rectangle((170,100,182,108),fill='white')
    ds,_,_=count_colonies(np.array(im),Boundary(150,150,145,145),'PCA')
    assert len(ds)>=3
    ds2,_,_=count_colonies(255-np.array(im),Boundary(150,150,145,145),'TSC',CounterSettings(polarity='dark'))
    assert len(ds2)>=3


def test_exclusion_mask_and_invalid_profile():
    image=read_rgb(DEMO/'synthetic_plates.png');b=detect_plates(image)[0]
    ds,_,_=count_colonies(image,b,'PCA',exclusions=[{'rect':[b.cx-b.rx,b.cy-b.ry,b.cx,b.cy+b.ry]}])
    assert all(d.x>b.cx for d in ds)
    with pytest.raises(ValueError):count_colonies(image,b,'EMB')


def test_evaluation_one_to_one_and_leakage_guard():
    m=detection_metrics([[10,10],[11,10]],[[10,10]],3)
    assert m['true_positive']==1 and m['false_positive']==1
    with pytest.raises(ValueError):split_by_group([{'physical_plate_group':'same','split':'training'},{'physical_plate_group':'same','split':'test'}])



def test_large_dish_radius_caps_the_opencv_median_aperture():
    # A clipped high-resolution plate can have a large fitted radius even
    # when the visible crop is small.
    rgb = np.full((96, 96, 3), 160, dtype=np.uint8)
    boundary = Boundary(48, 48, 950, 950, source='manual')
    detections, _, diagnostic = count_colonies(
        rgb, boundary, 'PCA', CounterSettings(polarity='both')
    )
    assert not detections
    assert diagnostic.shape[:2] == rgb.shape[:2]
