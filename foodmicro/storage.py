"""Local SQLite snapshots and preserved image bytes. No network requests."""
from __future__ import annotations
from pathlib import Path
from contextlib import closing, contextmanager
from tempfile import NamedTemporaryFile
import hashlib
import json
import os
import shutil
import sqlite3

from .models import Project, uid, timestamp
from .vision import image_sha, read_rgb


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'images').mkdir(exist_ok=True)
        (self.root/'spreadsheets').mkdir(exist_ok=True)
        self.db=self.root/'foodmicro.sqlite'
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS snapshots (project_id TEXT, revision INTEGER, time TEXT, payload TEXT, sha256 TEXT, PRIMARY KEY(project_id,revision))')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            yield connection

    def import_image(self, path: str | Path, project: Project) -> str:
        record,_=self.stage_image(path)
        return self.commit_image(record,project)

    def stage_image(self,path):
        path=Path(path)
        image_dir=self.root/'images'
        with NamedTemporaryFile(dir=image_dir, prefix='import-', suffix='.tmp', delete=False) as stream:
            temporary=Path(stream.name)
        try:
            shutil.copyfile(path,temporary)
            rgb=read_rgb(temporary)
            sha=image_sha(temporary)
            dest=image_dir/(sha+path.suffix.lower())
            if dest.exists():
                if image_sha(dest)!=sha:
                    raise ValueError('Original image hash mismatch; restore the preserved image before continuing')
            else:
                os.replace(temporary,dest)
        finally:
            temporary.unlink(missing_ok=True)
        record={'original_name':path.name,'file':dest.relative_to(self.root).as_posix(),
                                  'sha256':sha,'imported_at':timestamp(),'provenance':'unknown: check original/AI-enhanced status before validation'}
        return record,rgb

    def commit_image(self,record,project):
        for image_id,existing in project.images.items():
            if existing['sha256']==record['sha256']:return image_id
        image_id=uid();project.images[image_id]=record
        return image_id

    def image_path(self, project: Project, image_id: str, verify: bool=True) -> Path:
        record=project.images[image_id]
        relative=Path(record['file'])
        # A managed image is a direct child of images/. Resolve that directory
        # independently: on Windows it can itself be a redirected folder or
        # junction, outside the lexical root used for the SQLite project.
        if relative.is_absolute() or len(relative.parts)!=2 or relative.parts[0]!='images':
            raise ValueError('Invalid managed image path')
        expected_name=record['sha256']+relative.suffix.lower()
        if relative.name!=expected_name:
            raise ValueError('Managed image filename does not match its recorded hash')
        managed_dir=(self.root/'images').resolve()
        p=(self.root/relative).resolve()
        if p.parent!=managed_dir:raise ValueError('Image path escapes managed images directory')
        if verify and image_sha(p)!=record['sha256']:raise ValueError('Original image hash mismatch; restore the preserved image before continuing')
        return p

    def save(self, project: Project) -> int:
        for image_id in project.images:self.image_path(project,image_id)
        self.verify_workbooks(project)
        payload=json.dumps(project.to_dict(),sort_keys=True,ensure_ascii=False,allow_nan=False)
        sha=hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT COALESCE(MAX(revision),0) FROM snapshots WHERE project_id=?',(project.id,)).fetchone()
            revision=row[0]+1
            c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?)',(project.id,revision,timestamp(),payload,sha))
        return revision

    def list_projects(self) -> list[tuple]:
        with self.connect() as c:
            return c.execute('SELECT project_id,MAX(revision),MAX(time) FROM snapshots GROUP BY project_id ORDER BY MAX(time) DESC').fetchall()

    def load(self, project_id: str, revision: int | None=None) -> Project:
        with self.connect() as c:
            row=c.execute('SELECT payload,sha256 FROM snapshots WHERE project_id=? AND (? IS NULL OR revision=?) ORDER BY revision DESC LIMIT 1',(project_id,revision,revision)).fetchone()
        if not row:raise KeyError('Project snapshot not found')
        if hashlib.sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('Snapshot checksum mismatch')
        project=Project.from_dict(json.loads(row[0]))
        for image_id in project.images:self.image_path(project,image_id)
        self.verify_workbooks(project)
        return project

    def import_workbook(self, path, project: Project, sheet=None, **options):
        from .spreadsheet_io import import_workbook
        batch=import_workbook(path,sheet,**options)
        if any(b['sha256']==batch['sha256'] for b in project.imported_workbooks):raise ValueError('This workbook is already imported in this session')
        dest=self.root/'spreadsheets'/(batch['sha256']+'.xlsx')
        if not dest.exists():
            temp=dest.with_suffix('.tmp');shutil.copyfile(path,temp);os.replace(temp,dest)
        batch['file']=dest.relative_to(self.root).as_posix();project.imported_workbooks.append(batch)
        return batch

    def verify_workbooks(self, project):
        for b in project.imported_workbooks:
            p=(self.root/b['file']).resolve()
            if not p.is_relative_to(self.root) or image_sha(p)!=b['sha256']:raise ValueError('Imported workbook hash/path mismatch; restore preserved source bytes')

    def backup(self, destination: str | Path) -> Path:
        """Portable folder with consistent SQLite backup plus preserved original images."""
        dest=Path(destination).resolve()
        if dest==self.root or dest.is_relative_to(self.root):raise ValueError('Backup must be outside the working data directory')
        if dest.exists() and any(dest.iterdir()):raise ValueError('Choose an empty backup directory')
        dest.mkdir(parents=True,exist_ok=True)
        with self.connect() as source, closing(sqlite3.connect(dest/'foodmicro.sqlite')) as target:source.backup(target)
        shutil.copytree(self.root/'images',dest/'images',dirs_exist_ok=True)
        shutil.copytree(self.root/'spreadsheets',dest/'spreadsheets',dirs_exist_ok=True)
        if (self.root/'media_documents').exists():shutil.copytree(self.root/'media_documents',dest/'media_documents',dirs_exist_ok=True)
        for name in ('custom_media.json','ui_preferences.json','excel_mappings.json'):
            if (self.root/name).exists():shutil.copyfile(self.root/name,dest/name)
        for record in self.root.glob('qualitative_*.json'):shutil.copyfile(record,dest/record.name)
        return dest

