"""Reusable import, grouping and media-library dialogs."""
import copy
import html
import json
from dataclasses import asdict
from PySide6.QtCore import Qt, QUrl, QRectF
from PySide6.QtGui import QDesktopServices, QImage, QPixmap, QPainter, QPen, QColor, QIcon
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,QComboBox,QCheckBox,
    QDialogButtonBox,QPushButton,QTableWidget,QTableWidgetItem,QTextBrowser,QPlainTextEdit,QFileDialog,QMessageBox,QInputDialog,QSpinBox,QApplication,QScrollArea)
from .models import uid, timestamp, enumeration_scope
from .vision import CounterSettings, profiles

TESTS=['Aerobic Plate Count','Bacillus cereus','Clostridium perfringens','Total Coliforms','Thermotolerant coliforms','Enterobacteriaceae','Escherichia coli','Lactic acid bacteria','Staphylococcal enumeration','Yeasts','Moulds','Yeasts and Moulds','Other/custom test']

def plate_pixmap(rgb):
    import numpy as np
    rgb=np.ascontiguousarray(rgb);h,w=rgb.shape[:2]
    return QPixmap.fromImage(QImage(rgb.data,w,h,w*3,QImage.Format.Format_RGB888).copy())

class PasteTable(QTableWidget):
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_V and event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            row=max(0,self.currentRow());col=max(0,self.currentColumn());text=QApplication.clipboard().text()
            lines=text.rstrip('\n').splitlines()
            if row+len(lines)>1000:return
            self.setRowCount(max(self.rowCount(),row+len(lines)))
            for i,line in enumerate(lines):
                for j,value in enumerate(line.split('\t')):
                    if col+j<self.columnCount():
                        existing=self.item(row+i,col+j)
                        if existing is None or existing.flags()&Qt.ItemFlag.ItemIsEditable:self.setItem(row+i,col+j,QTableWidgetItem(value))
            event.accept();return
        super().keyPressEvent(event)

def medium_combo(library):
    combo=QComboBox();combo.setEditable(True);combo.setMinimumWidth(240)
    for key,entry in library.database()['media'].items():combo.addItem(f'{key} — {entry["name"]} — {entry.get("manufacturer","")}',key)
    combo.completer().setFilterMode(Qt.MatchFlag.MatchContains);combo.completer().setCompletionMode(combo.completer().CompletionMode.PopupCompletion)
    return combo

class ImportSetup(QDialog):
    def __init__(self,parent,library,previous,count):
        super().__init__(parent);self.setWindowTitle('Test and medium for image import');form=QFormLayout(self)
        form.addRow(QLabel('Confirm these choices for this import. Medium appearance is presumptive.'))
        self.test=QComboBox();self.test.setEditable(True);self.test.addItems(TESTS);self.test.setCurrentText(previous.get('test',TESTS[0]));form.addRow('Which microbiological test?',self.test)
        self.medium=medium_combo(library);index=self.medium.findData(previous.get('medium','PCA'));self.medium.setCurrentIndex(max(0,index));form.addRow('Which culture medium?',self.medium)
        add=QPushButton('Add a new medium…');form.addRow(add)
        def add_medium():
            parent.media_panel.edit(True);self.medium.clear()
            for key,entry in library.database()['media'].items():self.medium.addItem(f'{key} — {entry["name"]} — {entry.get("manufacturer","")}',key)
        add.clicked.connect(add_medium)
        self.all=QCheckBox('Apply these choices to all selected images');self.all.setChecked(True);self.all.setVisible(count>1);form.addRow(self.all)
        self.remember=QCheckBox('Remember choices (always ask for confirmation)');self.remember.setChecked(True);form.addRow(self.remember)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
    def values(self):
        if not self.medium.currentData():raise ValueError('Select an existing medium; add custom media in Media library first')
        if not enumeration_scope(self.test.currentText(),self.medium.currentData()):raise ValueError('Select a colony enumeration test')
        return {'test':self.test.currentText().strip(),'medium':self.medium.currentData()}

class GroupDialog(QDialog):
    def __init__(self,parent,plates,project,library):
        super().__init__(parent);self.setWindowTitle('Identify physical plates and sample relationships');self.resize(1100,550)
        self.plates=plates;self.library=library;self.project=project;layout=QVBoxLayout(self)
        self.relation=QComboBox();self.relation.addItems(['Mixed arrangement — assign manually','Same sample/test — technical replicates','Same sample/test — different dilution levels','Same sample — different tests','Different samples','Single plate — no relationship'])
        if len(plates)==1:self.relation.setCurrentIndex(5)
        layout.addWidget(QLabel('Confirm each physical plate. Shared Test IDs group results only when preparation/method checks agree.'));layout.addWidget(self.relation)
        bar=QHBoxLayout();self.sample=QLineEdit();self.sample.setPlaceholderText('Common sample ID / prefix');bar.addWidget(self.sample)
        apply=QPushButton('Apply relationship');apply.clicked.connect(self.apply_relationship);bar.addWidget(apply);layout.addLayout(bar)
        self.relation.currentIndexChanged.connect(lambda i:(self.sample.setEnabled(i!=5),apply.setEnabled(i!=5)))
        if self.relation.currentIndex()==5:self.sample.setEnabled(False);apply.setEnabled(False)
        if len(plates)>1:self.relation.model().item(5).setEnabled(False)
        preview=QHBoxLayout();layout.addLayout(preview)
        self.photo=QLabel();self.crop=QLabel();self.caption=QLabel();self.caption.setWordWrap(True)
        for widget in (self.photo,self.crop):widget.setAlignment(Qt.AlignmentFlag.AlignCenter);preview.addWidget(widget)
        preview.addWidget(self.caption,1)
        self.table=PasteTable(len(plates),9);self.table.setHorizontalHeaderLabels(['Image / plate','Sample ID','Sample name','Test ID','Test','Medium','Replicate','Additional inverse DF','Volume mL']);layout.addWidget(self.table)
        for row,p in enumerate(plates):
            m=p.meta;values=[f'Plate {row+1} · {p.id[:8]}',m.sample_id,m.sample_name,m.test_id,m.test,m.medium,m.replicate,str(m.additional_dilution_factor),str(m.plated_ml)]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value)
                if col==0:
                    cell.setFlags(cell.flags()&~Qt.ItemFlag.ItemIsEditable);cell.setToolTip(project.images[p.image_id]['original_name'])
                    rgb=parent.rgb.get(p.image_id)
                    if rgb is not None:cell.setIcon(QIcon(self.plate_crop(rgb,p.boundary,40)))
                self.table.setItem(row,col,cell)
        self.table.resizeColumnsToContents()
        self.table.currentCellChanged.connect(self.preview_plate)
        if plates:self.table.setCurrentCell(0,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.commit);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    @staticmethod
    def plate_crop(rgb,b,size):
        r=max(b.rx,b.ry);x=max(0,int(b.cx-r));y=max(0,int(b.cy-r))
        crop=rgb[y:int(b.cy+r)+1,x:int(b.cx+r)+1]
        return plate_pixmap(crop).scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation) if crop.size else QPixmap()

    def preview_plate(self,row,*args):
        if not 0<=row<len(self.plates):return
        p=self.plates[row];host=self.parent();rgb=host.rgb.get(p.image_id)
        self.caption.setText(f'Plate {row+1}\n{self.project.images[p.image_id]["original_name"]}\nYellow outline shows the plate for this row.')
        if rgb is None:self.photo.clear();self.crop.clear();return
        im=plate_pixmap(rgb).scaled(365,220,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        scale=im.width()/rgb.shape[1]
        painter=QPainter(im);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i,other in enumerate(self.plates):
            if other.image_id!=p.image_id:continue
            b=other.boundary;painter.setPen(QPen(QColor('#ffdc55' if i==row else '#54d4ff'),3 if i==row else 1.5))
            painter.save();painter.translate(b.cx*scale,b.cy*scale);painter.rotate(b.angle)
            painter.drawEllipse(QRectF(-b.rx*scale,-b.ry*scale,2*b.rx*scale,2*b.ry*scale));painter.restore()
            painter.drawText(QRectF(b.cx*scale-16,b.cy*scale-12,32,24),Qt.AlignmentFlag.AlignCenter,str(i+1))
        painter.end();self.photo.setPixmap(im);self.crop.setPixmap(self.plate_crop(rgb,p.boundary,220))
    def apply_relationship(self):
        relation=self.relation.currentIndex();sample=self.sample.text().strip();test=uid()[:12]
        if relation in (0,5):return
        for row,p in enumerate(self.plates):
            self.table.item(row,1).setText(sample if relation!=4 else f'{sample}-{row+1}')
            self.table.item(row,3).setText(test if relation in (1,2) else uid()[:12])
            self.table.item(row,6).setText(str(row+1) if relation==1 else '1')
        # Different dilution levels are deliberately not guessed.
    def commit(self):
        try:
            candidates=[];db=self.library.database()['media']
            for row,p in enumerate(self.plates):
                m=copy.deepcopy(p.meta);v=[self.table.item(row,c).text().strip() for c in range(1,9)]
                m.sample_id,m.sample_name,m.test_id,m.test,m.medium,m.replicate=v[:6]
                m.additional_dilution_factor=float(v[6]);m.plated_ml=float(v[7]);m.validate()
                if m.medium not in db:raise ValueError('Use a medium key from Media library')
                if not enumeration_scope(m.test,m.medium,db[m.medium]):raise ValueError('Select a colony enumeration test')
                if self.relation.currentIndex()==5:m.test_id=m.test_id or uid()[:12];m.replicate=m.replicate or '1'
                candidates.append(m)
            actor=self.parent().actor.text().strip()
            if not actor:raise ValueError('Enter the analyst name')
            for p,m in zip(self.plates,candidates):
                before=asdict(p.meta);p.meta=m;p._record('group_assignment',actor,self.relation.currentText(),before,asdict(m))
            self.accept()
        except Exception as e:QMessageBox.warning(self,'Plate records',str(e))

class MediaPanel(QWidget):
    def __init__(self,parent,library):
        super().__init__();self.host=parent;self.library=library;layout=QVBoxLayout(self)
        self.search=QLineEdit();self.search.setPlaceholderText('Search medium, manufacturer, colony characteristics or notes');layout.addWidget(self.search)
        self.choose=QComboBox();layout.addWidget(self.choose);self.text=QTextBrowser();self.text.setOpenExternalLinks(True);layout.addWidget(self.text)
        row=QHBoxLayout();layout.addLayout(row)
        for label,fn in [('Add / variant',lambda:self.edit(True)),('Edit details',lambda:self.edit(False)),('Counting rules',self.edit_rules),('Save current preset',self.preset),('Attach sheet',self.attach),('Attach photo',lambda:self.attach(True)),('Open attachment',self.open_attachment)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        row=QHBoxLayout();layout.addLayout(row)
        for label,fn in [('Export custom records',self.export),('Import custom records',self.import_records),('Restore revision',self.restore)]:
            b=QPushButton(label);b.clicked.connect(fn);row.addWidget(b)
        self.search.textChanged.connect(self.refresh);self.choose.currentTextChanged.connect(self.show_entry);self.refresh()
    def refresh(self,*args):
        old=self.choose.currentText();q=self.search.text().casefold();self.choose.blockSignals(True);self.choose.clear()
        self.choose.addItems([k for k,v in self.library.database()['media'].items() if q in (k+' '+json.dumps(v,ensure_ascii=False)).casefold()])
        if self.choose.findText(old)>=0:self.choose.setCurrentText(old)
        self.choose.blockSignals(False);self.show_entry()

    def select_medium(self,key):
        self.search.clear();self.choose.setCurrentIndex(self.choose.findText(key));self.show_entry()

    def show_entry(self,*args):
        key=self.choose.currentText();db=self.library.database()
        if key not in db['media']:self.text.setPlainText('No matching medium. Clear the search or add a medium.');return
        entry=db['media'][key];out=f'<h2>{html.escape(key)} — {html.escape(entry["name"])}</h2>'
        v=entry.get('vision',{})
        mode='Counting profile enabled' if entry.get('counting_enabled') else 'Reference only; no detector enabled'
        out+=f'<p><b>{mode}.</b> {html.escape(str(v.get("appearance_summary") or "Review local-contrast candidates using the lab method."))}</p>'
        sections=[('Overview',['manufacturer','catalogue','application','principle','evidence_status','lab_notes']),('Colony appearance',['positive_appearance','negative_or_other_appearance','atypical_appearance','size_shape']),('Counting and limitations',['limitations','confirmation','vision_implications']),('Detection profile',['vision','custom_settings','local_revision'])]
        for title,keys in sections:
            out+='<h3>'+title+'</h3>'
            for field in keys:
                value=entry.get(field)
                if isinstance(value,dict):
                    for label,text in value.items():out+='<p><b>'+html.escape(label.replace('_',' ').title())+': </b>'+html.escape(str(text))+'</p>'
                elif value is not None:out+='<p><b>'+html.escape(field.replace('_',' ').title())+': </b>'+html.escape(str(value))+'</p>'
        out+='<h3>References</h3>'
        for source in [db['sources'][s] for s in entry.get('sources',[]) if s in db['sources']]+entry.get('local_sources',[]):
            url=str(source.get('url') or '');out+=f'<p>{html.escape(str(source.get("document") or "Reference"))} — {html.escape(str(source.get("revision") or "Revision not recorded"))} '
            if url.startswith(('https://','http://')):out+=f'<a href="{html.escape(url,quote=True)}">Open official/reference link</a>'
            out+='</p>'
        for a in entry.get('attachments',[]):out+='<p>Local attachment: '+html.escape(str(a.get('title') or 'Untitled'))+' — '+html.escape(str(a.get('revision') or 'Revision not recorded'))+'</p>'
        self.text.setHtml(out)
    def edit(self,new):
        key=self.choose.currentText();existing=copy.deepcopy(self.library.database()['media'].get(key,{}))
        if not new and not existing:return
        dialog=QDialog(self);dialog.setWindowTitle('Laboratory medium');form=QFormLayout(dialog);widgets={}
        names=['key','name','manufacturer','catalogue','application','principle','count_description','positive_appearance','negative_or_other_appearance','atypical_appearance','size_shape','limitations','confirmation','vision_implications','lab_notes']
        for name in names:
            w=QLineEdit('' if new and name=='key' else key if name=='key' else str(existing.get(name,'')));widgets[name]=w;form.addRow(name.replace('_',' ').title(),w)
        widgets['key'].setReadOnly(not new)
        url=QLineEdit();revision=QLineEdit();form.addRow('Additional reference URL',url);form.addRow('Document revision/date',revision)
        reason=QLineEdit();form.addRow('Reason for change',reason)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec():
            try:
                entry=existing;newkey=widgets.pop('key').text().strip();entry.update({n:w.text().strip() for n,w in widgets.items()})
                if new and newkey in self.library.database()['media']:raise ValueError('Use a different medium key for the variant')
                if new:entry.update(sources=[],local_sources=[],attachments=[])
                if not entry.get('vision'):entry['vision']={'polarity':'both','appearance_rule':'any'}
                entry.setdefault('counting_enabled',False)
                if url.text():entry.setdefault('local_sources',[]).append({'url':url.text().strip(),'document':'Laboratory-entered reference','revision':revision.text()})
                self.library.save(newkey,entry,self.host.actor.text(),reason.text());self.host.refresh_media_options();self.refresh();self.choose.setCurrentText(newkey)
            except Exception as e:self.host.error(e)

    def edit_rules(self):
        key=self.choose.currentText();entry=copy.deepcopy(self.library.database()['media'].get(key))
        if not entry:return
        dialog=QDialog(self);dialog.setWindowTitle(f'Counting rules — {key}');form=QFormLayout(dialog)
        v=entry.setdefault('vision',{});rule=QComboBox();rule.addItems(['any','pink-purple','dark-centre','golden']);rule.setCurrentText(v.get('appearance_rule','any'));form.addRow('Appearance',rule)
        polarity=QComboBox();polarity.addItems(['bright','dark','both']);polarity.setCurrentText(v.get('polarity','both'));form.addRow('Contrast',polarity)
        summary=QLineEdit(v.get('appearance_summary',''));form.addRow('Analyst guidance',summary)
        fungal=QCheckBox('Use yeast and mould appearance categories');fungal.setChecked(bool(v.get('fungal')));form.addRow(fungal)
        limits={}
        for ident,title in [('compact','Compact radius range (mm)'),('mould','Broad mould radius range (mm)')]:
            box=QLineEdit(', '.join(str(n) for n in v.get('radius_mm',{}).get(ident,[])))
            box.setPlaceholderText('Minimum, maximum · optional');form.addRow(title,box);limits[ident]=box
        enabled=QCheckBox('Enable colony detection');enabled.setChecked(bool(entry.get('counting_enabled')));form.addRow(enabled)
        reason=QLineEdit();form.addRow('Reason for change',reason)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec():
            try:
                v.update(polarity=polarity.currentText(),appearance_rule=rule.currentText(),appearance_summary=summary.text(),fungal=fungal.isChecked())
                if any(box.text().strip() for box in limits.values()):
                    if not fungal.isChecked():raise ValueError('Enable the yeast and mould categories to use these radius ranges')
                    v['radius_mm']={ident:[float(n.strip()) for n in box.text().split(',')] for ident,box in limits.items()}
                else:v.pop('radius_mm',None)
                entry.update(counting_enabled=enabled.isChecked())
                self.library.save(key,entry,self.host.actor.text(),reason.text());self.host.refresh_media_options();self.refresh()
            except Exception as e:self.host.error(e)
    def preset(self):
        try:
            key=self.choose.currentText();entry=copy.deepcopy(self.library.database()['media'][key]);name,ok=QInputDialog.getText(self,'Custom profile','New profile key',text=key+' - Laboratory custom profile')
            if ok:
                entry['custom_settings']=asdict(self.host.settings());self.library.save(name,entry,self.host.actor.text(),'Save current detector settings as a custom preset');self.host.refresh_media_options();self.refresh()
        except Exception as e:self.host.error(e)
    def attach(self,image=False):
        try:
            path=QFileDialog.getOpenFileName(self,'Attach reference photograph' if image else 'Attach technical sheet','','Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)' if image else 'PDF (*.pdf)')[0]
            if not path:return
            from pathlib import Path
            dialog=QDialog(self);dialog.setWindowTitle('Document metadata (laboratory-entered)');form=QFormLayout(dialog);inputs={}
            for key in ('title','source_url','revision','notes'):
                field=QLineEdit(Path(path).name if key=='title' else '');inputs[key]=field;form.addRow(key.replace('_',' ').title(),field)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
            if dialog.exec():self.library.attach(self.choose.currentText(),path,self.host.actor.text(),'Attach reference document',**{k:v.text() for k,v in inputs.items()});self.refresh()
        except Exception as e:self.host.error(e)
    def open_attachment(self):
        try:
            records=self.library.database()['media'][self.choose.currentText()].get('attachments',[])
            if not records:raise ValueError('No local attachments for this medium')
            name,ok=QInputDialog.getItem(self,'Attachment','Choose',[a['title'] for a in records],0,False)
            if ok:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.attachment_path(next(a for a in records if a['title']==name)))))
        except Exception as e:self.host.error(e)
    def export(self):
        path=QFileDialog.getSaveFileName(self,'Export custom media','','JSON (*.json)')[0]
        if path:self.library.export(path)
    def import_records(self):
        try:
            path=QFileDialog.getOpenFileName(self,'Import custom media','','JSON (*.json)')[0]
            if path:self.library.import_file(path,self.host.actor.text());self.host.refresh_media_options();self.refresh()
        except Exception as e:self.host.error(e)
    def restore(self):
        try:
            key=self.choose.currentText();revs=[str(h['revision']) for h in self.library.local['history'] if h['key']==key]
            default = 'Bundled counting preset'
            if profiles()['media'].get(key, {}).get('counting_enabled'):
                revs.insert(0, default)
            if not revs:raise ValueError('No local revisions')
            rev,ok=QInputDialog.getItem(self,'Restore media settings','Choose a preset or revision',revs,0,False)
            if ok:
                if rev == default:
                    self.library.restore_default_preset(key, self.host.actor.text())
                else:
                    self.library.restore(key,int(rev),self.host.actor.text(),'Restore earlier medium revision')
                self.host.refresh_media_options()
                self.refresh()
        except Exception as e:self.host.error(e)

class WorkbookSetup(QDialog):
    def __init__(self,parent,path):
        super().__init__(parent)
        from openpyxl import load_workbook
        from pathlib import Path
        from .spreadsheet_io import ALIASES,normalise_header
        if Path(path).stat().st_size>50*1024*1024:raise ValueError('Workbook exceeds 50 MB')
        self.wb=load_workbook(path,read_only=True,data_only=False,keep_links=False);self.aliases=ALIASES;self.normalise=normalise_header
        self.setWindowTitle('Preview worksheet and map source columns');self.resize(1000,480);layout=QVBoxLayout(self)
        form=QFormLayout();layout.addLayout(form);self.sheet=QComboBox();self.sheet.addItems(self.wb.sheetnames);form.addRow('Worksheet',self.sheet)
        self.header=QSpinBox();self.header.setRange(1,30);form.addRow('Header row',self.header)
        self.table=QTableWidget();layout.addWidget(self.table);self.remember=QCheckBox('Remember this mapping by worksheet name');layout.addWidget(self.remember)
        self.mapping_path=parent.store.root/'excel_mappings.json'
        self.saved=json.loads(self.mapping_path.read_text()) if self.mapping_path.exists() else {}
        self.sheet.currentTextChanged.connect(self.preview);self.header.valueChanged.connect(self.preview)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons);self.preview()
    def preview(self,*args):
        ws=self.wb[self.sheet.currentText()];rows=list(ws.iter_rows(min_row=self.header.value(),max_row=self.header.value()+5,max_col=min(ws.max_column or 1,50),values_only=True))
        n=len(rows[0]);self.table.setColumnCount(n);self.table.setRowCount(len(rows));self.table.setHorizontalHeaderLabels([str(v or '') for v in rows[0]]);self.combos=[]
        saved=self.saved.get(self.sheet.currentText(),{})
        for col,value in enumerate(rows[0]):
            combo=QComboBox();combo.addItem('Ignore','');combo.addItems(sorted(set(self.aliases.values())))
            combo.setCurrentText(saved.get(str(col+1),self.aliases.get(self.normalise(value),'Ignore')));self.combos.append(combo);self.table.setCellWidget(0,col,combo)
        for row,values in enumerate(rows[1:],1):
            for col,value in enumerate(values):
                item=QTableWidgetItem(str(value) if value is not None else '');item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(row,col,item)
    def selection(self):
        mapping={i+1:c.currentText() for i,c in enumerate(self.combos) if c.currentText()!='Ignore'}
        if not {'count','total_inverse','volume_ml'}.issubset(mapping.values()):raise ValueError('Map count, total_inverse and volume_ml')
        if self.remember.isChecked():self.saved[self.sheet.currentText()]=mapping;self.mapping_path.write_text(json.dumps(self.saved,indent=2))
        return self.sheet.currentText(),mapping,self.header.value()
    def done(self,result):
        self.wb.close();super().done(result)


class ResultLayout(QDialog):
    def __init__(self,parent,path,rows):
        super().__init__(parent)
        from pathlib import Path
        from openpyxl import load_workbook
        from .reporting import LAYOUT_FIELDS
        if Path(path).stat().st_size>50*1024*1024:raise ValueError('Excel layout exceeds 50 MB')
        self.wb=load_workbook(path,read_only=True,keep_links=False)
        self.fields=LAYOUT_FIELDS;self.setWindowTitle('Map count results to Excel columns');self.resize(670,650)
        layout=QVBoxLayout(self);label=QLabel(f'{len(rows)} observation(s). Select blank destination cells. The source workbook is preserved.');label.setWordWrap(True);layout.addWidget(label)
        form=QFormLayout();layout.addLayout(form)
        self.sheet=QComboBox();self.sheet.addItems(self.wb.sheetnames);form.addRow('Worksheet',self.sheet)
        self.header=QSpinBox();self.header.setRange(1,1000);form.addRow('Header row',self.header)
        self.first=QSpinBox();self.first.setRange(2,1048576);self.first.setValue(2);form.addRow('First output row',self.first)
        scroll=QScrollArea();scroll.setWidgetResizable(True);layout.addWidget(scroll,1);choices=QWidget();scroll.setWidget(choices);mapping=QFormLayout(choices)
        self.combos={}
        for field in self.fields:
            self.combos[field]=QComboBox();mapping.addRow(field.title(),self.combos[field])
        self.sheet.currentTextChanged.connect(self.columns);self.header.valueChanged.connect(self.columns);self.columns()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def columns(self,*args):
        from openpyxl.utils import get_column_letter
        from .spreadsheet_io import normalise_header
        ws=self.wb[self.sheet.currentText()];header=self.header.value()
        values=next(ws.iter_rows(min_row=header,max_row=header,max_col=min(max(20,ws.max_column or 1),100),values_only=True),())
        self.first.setMinimum(header+1)
        for field,combo in self.combos.items():
            combo.clear();combo.addItem('Leave blank',0)
            for col,value in enumerate(values,1):
                combo.addItem(f'{get_column_letter(col)} — {value or "(blank header)"}',col)
            matching=[i for i,v in enumerate(values,1) if normalise_header(v)==normalise_header(field)]
            if len(matching)==1:combo.setCurrentIndex(matching[0])

    def selection(self):
        return self.sheet.currentText(),self.first.value(),{key:combo.currentData() for key,combo in self.combos.items() if combo.currentData()}

    def done(self,result):
        self.wb.close();super().done(result)
