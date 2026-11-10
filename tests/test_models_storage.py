from dataclasses import asdict
import copy
import pytest
from foodmicro.models import Boundary,Detection,Plate,Project,uid
from foodmicro.storage import ProjectStore
from foodmicro.vision import image_sha


def plate():
    p=Plate(uid(),'image',Boundary(50,50,45,45));p.automatic=[Detection('a',35,45),Detection('b',65,45)]
    p.detections=copy.deepcopy(p.automatic);return p


def test_corrections_immutable_auto_undo_redo():
    p=plate();baseline=asdict(p)['automatic']
    p.edit('add',[],'Analyst','missed faint colony',[(50,65)]);assert p.count==3 and p.automatic_count==2
    assert p.undo('Analyst','undo marker') and p.count==2
    assert p.redo('Analyst','reinstate marker') and p.count==3
    p.edit('remove',['a'],'Analyst','bubble');assert p.count==2
    p.edit('split',['b'],'Analyst','two touching colonies',[(61,45),(69,45)]);assert p.count==3
    ids=[d.id for d in p.detections if d.y==45]
    p.edit('merge',ids,'Analyst','one colony with halo');assert p.count==2
    p.edit('reclassify',[p.detections[0].id],'Analyst','artefact',category='artefact');assert p.count==1
    assert asdict(p)['automatic']==baseline
    assert [e['action'] for e in p.edits]==['add','undo','redo','remove','split','merge','reclassify']


def test_edits_atomic_and_regions():
    p=plate()
    with pytest.raises(ValueError):p.edit('add',[],'','',[(50,50)])
    with pytest.raises(ValueError):p.edit('split',['a'],'A','reason',[(200,200),(50,50)])
    assert p.count==2
    p.add_exclusion((30,40,40,50),'artefact','A','bubble zone');assert p.count==1
    p.undo('A','undo region');assert not p.exclusions and p.count==2


def test_review_gate_and_reset():
    p=plate();p.meta.sample_id='S1';p.meta.test_id='T1';p.parameters['algorithm_version']='test'
    with pytest.raises(ValueError):p.mark_reviewed('A')
    p.boundary_reviewed=True;p.quality={'warnings':['low resolution']}
    with pytest.raises(ValueError):p.mark_reviewed('A')
    p.quality_acknowledged=True;p.mark_reviewed('A');assert p.review_status=='reviewed'
    p.edit('add',[],'A','correction',[(50,50)]);assert p.review_status=='draft'


def test_sqlite_image_integrity_snapshot_backup(tmp_path):
    from PIL import Image
    original=tmp_path/'original.png';Image.new('RGB',(100,100),'white').save(original);sha=image_sha(original)
    store=ProjectStore(tmp_path/'data');pr=Project();image_id=store.import_image(original,pr)
    pr.plates=[Plate(uid(),image_id,Boundary(50,50,45,45))]
    assert store.save(pr)==1;assert store.save(pr)==2
    loaded=store.load(pr.id);assert loaded.to_dict()==pr.to_dict();assert image_sha(original)==sha
    backup=store.backup(tmp_path/'backup');assert ProjectStore(backup).load(pr.id).id==pr.id
    managed = store.image_path(pr,image_id)
    managed.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Original image hash mismatch'):
        store.load(pr.id)
    with pytest.raises(ValueError, match='Original image hash mismatch'):
        store.import_image(original, pr)
    assert managed.read_bytes() == b'corrupt'
    assert not list(managed.parent.glob('import-*.tmp'))
    assert image_sha(original) == sha


def test_image_import_with_redirected_managed_folder_and_path_guard(tmp_path):
    from PIL import Image
    source=tmp_path/'external plate ü.png'
    Image.new('RGB',(100,100),'white').save(source)
    outside=tmp_path/'redirected images';outside.mkdir()
    data=tmp_path/'data';data.mkdir()
    try:(data/'images').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Directory symlink permission is unavailable on this workstation')
    store=ProjectStore(data);project=Project()
    image_id=store.import_image(source,project)
    assert store.image_path(project,image_id).parent==outside.resolve()
    assert store.save(project)==1
    record=project.images[image_id]
    safe=record['file']
    record['file']='../external plate ü.png'
    with pytest.raises(ValueError,match='Invalid managed image path'):
        store.image_path(project,image_id)
    record['file']=safe
    assert store.image_path(project,image_id).is_file()

