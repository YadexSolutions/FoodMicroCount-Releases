from pathlib import Path
import argparse
import copy
import json
from dataclasses import asdict

from .models import Project, Plate, PlateMeta, uid
from . import ALGORITHM_VERSION
from .storage import ProjectStore
from .vision import read_rgb,detect_plates,count_colonies,CounterSettings,profiles
from .reporting import export_bundle
from .calculations import Observation,calculate
from .validation import detection_metrics,split_by_group


def analyse(args):
    output=Path(args.out);store=ProjectStore(output/'local_data');project=Project()
    inputs=[args.image] if Path(args.image).is_file() else sorted(p for p in Path(args.image).iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png','.tif','.bmp'))
    diagnostics=output/'diagnostics';diagnostics.mkdir(parents=True,exist_ok=True)
    summary=[]
    for path in inputs:
        image_id=store.import_image(path,project);rgb=read_rgb(path);bs=detect_plates(rgb)
        for i,b in enumerate(bs,1):
            settings=CounterSettings(strategy=args.strategy,dish_diameter_mm=args.dish_diameter_mm,
                                     inspect_spreading=not args.no_spread_screen,screen_marks=not args.no_artefact_screen)
            ds,q,processed=count_colonies(rgb,b,args.medium,settings,test=args.test)
            p=Plate(uid(),image_id,b,PlateMeta(test=args.test or ('APC' if args.medium=='PCA' else args.medium),medium=args.medium,replicate=str(i)))
            p.automatic=copy.deepcopy(ds);p.detections=ds;p.quality=q;p.parameters={**asdict(settings),'algorithm_version':ALGORITHM_VERSION,'profile_version':profiles()['version'],'counted_medium':args.medium}
            project.plates.append(p)
            from PIL import Image
            Image.fromarray(processed).save(diagnostics/(p.id+'.png'))
            summary.append({'image':Path(path).name,'plate':i,'plate_id':p.id,'automatic_candidates':len(ds),'included_candidates':p.count,'reference_count':None,'reviewed':False,'warnings':len(q['warnings'])})
    store.save(project);export_bundle(project,store,output/'export')
    (output/'processing_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({'images':len(inputs),'plate_regions':len(project.plates),'summary':str(output/'processing_summary.json'),'reference_counts':'not supplied; no real-image accuracy claimed'}))


def main():
    parser=argparse.ArgumentParser(description='FoodMicro Count offline processing and evaluation')
    sub=parser.add_subparsers(dest='command',required=True)
    a=sub.add_parser('analyse');a.add_argument('image');a.add_argument('--out',required=True);a.add_argument('--medium',default='PCA');a.add_argument('--strategy',choices=['hybrid','local','log'],default='hybrid');a.set_defaults(func=analyse)
    a.add_argument('--test',default='',help='Use Yeasts or Moulds to select one size class; blank keeps both')
    a.add_argument('--dish-diameter-mm',default=90.,type=float,help='Actual diameter of the fitted agar')
    a.add_argument('--no-spread-screen',action='store_true')
    a.add_argument('--no-artefact-screen',action='store_true')
    c=sub.add_parser('calculate');c.add_argument('json_input')
    e=sub.add_parser('evaluate');e.add_argument('json_input');e.add_argument('--tolerance',type=float,default=5)
    i=sub.add_parser('import-excel');i.add_argument('workbook');i.add_argument('--sheet');i.add_argument('--out',required=True)
    args=parser.parse_args()
    if args.command=='analyse':args.func(args)
    elif args.command=='import-excel':
        from .spreadsheet_io import export_import,records
        store=ProjectStore(Path(args.out)/'local_data');project=Project();batch=store.import_workbook(args.workbook,project,args.sheet)
        store.save(project);export_import(batch,Path(args.out)/'reconciliation')
        print(json.dumps({'project_id':project.id,'imported_rows':len(records(batch)),'status':'draft: review identifiers, preparation, units and method before calculation','authorised':False}))
    elif args.command=='calculate':
        data=json.loads(Path(args.json_input).read_text());r=calculate([Observation(**p) for p in data['observations']],data['method_id'],data.get('unit','CFU/g'))
        print(json.dumps({**asdict(r),'display':r.text()},indent=2))
    elif args.command=='evaluate':
        data=json.loads(Path(args.json_input).read_text());split_by_group(data['records'])
        results=[]
        for r in data['records']:results.append({'plate_id':r['plate_id'],**detection_metrics(r['predicted'],r['reference'],args.tolerance)})
        print(json.dumps(results,indent=2))


if __name__=='__main__':main()

