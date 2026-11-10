"""Local versioned overlays; software updates never overwrite laboratory entries."""
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
from .vision import profiles, CounterSettings
from .models import timestamp, uid, enumeration_scope
from .morphology import validate_radius_ranges


class MediaLibrary:
    def __init__(self, root):
        self.root=Path(root);self.path=self.root/'custom_media.json';self.documents=self.root/'media_documents'
        self.documents.mkdir(parents=True,exist_ok=True)
        self.load_warning=''
        try:
            self.local=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'entries':{},'history':[]}
            if not isinstance(self.local['entries'],dict) or not isinstance(self.local['history'],list):raise ValueError('Invalid media overlay')
        except (ValueError,KeyError):
            preserved=self.path.with_name('custom_media_corrupt_'+uid()+'.json');shutil.copyfile(self.path,preserved)
            self.local={'entries':{},'history':[]};self.load_warning='Custom media could not be read. Original bytes preserved at '+str(preserved)

    def database(self):
        db=profiles();db['media'].update({k:copy.deepcopy(v) for k,v in self.local['entries'].items() if enumeration_scope(medium=k,profile=v)});return db

    def _write(self):
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.local,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(temp,self.path)

    def save(self,key,entry,actor,reason):
        if not actor.strip() or not reason.strip():raise ValueError('Analyst and change reason required')
        if not key.strip() or not entry.get('name','').strip():raise ValueError('Medium key and name required')
        data=copy.deepcopy(entry)
        if not enumeration_scope(medium=key,profile=data):raise ValueError('This medium is outside the colony counting scope')
        if data.get('vision',{}).get('polarity','both') not in ('bright','dark','both'):raise ValueError('Invalid profile polarity')
        CounterSettings(**data.get('custom_settings',{})).validate()
        ranges=data.get('vision',{}).get('radius_mm')
        if ranges is not None:validate_radius_ranges(ranges)
        before=self.database()['media'].get(key)
        revision=1+sum(h['key']==key for h in self.local['history'])
        data.update(local_revision=revision,evidence_status='Laboratory-entered; counting profile experimental',sources=data.get('sources',[]))
        self.local['history'].append({'key':key,'revision':revision,'time':timestamp(),'actor':actor,'reason':reason,'before':before,'after':copy.deepcopy(data)})
        self.local['entries'][key]=data;self._write();return revision

    def attach(self,key,path,actor,reason,title='',source_url='',revision='',notes=''):
        path=Path(path)
        if path.suffix.lower() not in ('.pdf','.png','.jpg','.jpeg','.tif','.tiff','.bmp'):raise ValueError('Attach a PDF or supported image')
        if path.stat().st_size>50*1024*1024:raise ValueError('Attachment exceeds 50 MB')
        data=path.read_bytes()
        if path.suffix.lower()=='.pdf' and not data.startswith(b'%PDF-'):raise ValueError('Not a PDF document')
        if path.suffix.lower()!='.pdf':
            from .vision import read_rgb
            read_rgb(path)
        sha=hashlib.sha256(data).hexdigest();dest=self.documents/(sha+path.suffix.lower())
        if not dest.exists():shutil.copyfile(path,dest)
        entry=copy.deepcopy(self.database()['media'][key]);entry.setdefault('attachments',[]).append({'id':uid(),'file':dest.name,'title':title or path.name,'sha256':sha,'source_url':source_url,'revision':revision,'notes':notes})
        self.save(key,entry,actor,reason)

    def attachment_path(self,record):
        name=record['file'];path=(self.documents/name).resolve()
        if Path(name).name!=name or path.parent!=self.documents.resolve():raise ValueError('Invalid document path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Document hash mismatch')
        return path

    def restore_default_preset(self, key, actor):
        bundled = profiles()['media'].get(key)
        if not bundled or not bundled.get('counting_enabled'):
            raise ValueError('This medium has no bundled counting preset')
        entry = self.database()['media'][key]
        entry['vision'] = copy.deepcopy(bundled['vision'])
        entry.pop('custom_settings', None)
        return self.save(key, entry, actor, 'Restore bundled counting preset')

    def restore(self,key,revision,actor,reason):
        record=next(h for h in self.local['history'] if h['key']==key and h['revision']==revision)
        return self.save(key,record['after'],actor,reason)

    def export(self,path):Path(path).write_text(json.dumps(self.local,indent=2,ensure_ascii=False),encoding='utf-8')

    def import_file(self,path,actor):
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        for key,entry in data['entries'].items():
            # Attachments need the accompanying backup; do not trust imported paths.
            entry=copy.deepcopy(entry);entry.pop('attachments',None);self.save(key,entry,actor,'Imported custom medium')
