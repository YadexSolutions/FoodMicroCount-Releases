"""Local Qt desktop counter with optional, explicitly requested online mark review."""
from __future__ import annotations
from pathlib import Path
from dataclasses import asdict, replace
import argparse
import copy
import json
import math
import os
import sys
from tempfile import TemporaryDirectory

import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QThreadPool, QUrl, QTimer, Slot
from PySide6.QtGui import QImage, QPixmap, QPen, QColor, QPainter, QPainterPath, QBrush, QAction, QKeySequence, QIcon, QShortcut, QCursor
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QSplitter,
    QTabWidget,QListWidget,QLabel,QPushButton,QComboBox,QLineEdit,QDoubleSpinBox,QSpinBox,QCheckBox,
    QFormLayout,QScrollArea,QGroupBox,QPlainTextEdit,QFileDialog,QMessageBox,QInputDialog,
    QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,
    QDialog,QDialogButtonBox,QMenu,QProgressBar)

from . import __version__,ALGORITHM_VERSION,DISPLAY_VERSION
from .models import Project,Plate,PlateMeta,Boundary,uid,timestamp,enumeration_scope
from .vision import read_rgb,detect_plates,count_colonies,CounterSettings,profiles,ellipse_mask,select_rim,rim_warning,review_mark_counts
from .storage import ProjectStore
from .reporting import sample_results,export_bundle,plate_observation,RESULT_NOTICE
from .calculations import METHODS
from .media_library import MediaLibrary
from .ui_support import ImportSetup, GroupDialog, MediaPanel, WorkbookSetup, PasteTable
from .tasks import Task
from .morphology import radius_guide, fungal_target
from .ai_review import make_crop, review_marks, MODEL as AI_MODEL, MAX_CROPS
from .ai_dialogs import SendReview, ReviewSuggestions

ICON_PATH=Path(__file__).with_name('assets')/'icon.png'


def pixmap(rgb: np.ndarray) -> QPixmap:
    array=np.ascontiguousarray(rgb,dtype=np.uint8);h,w=array.shape[:2]
    return QPixmap.fromImage(QImage(array.data,w,h,w*3,QImage.Format.Format_RGB888).copy())


def counting_cursor(state='add'):
    """A small pen point with its click hotspot exactly in the centre."""
    dot=QPixmap(19,19);dot.fill(Qt.GlobalColor.transparent)
    painter=QPainter(dot);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#162635'),1.5));painter.setBrush(QColor('#ffffff'))
    painter.drawEllipse(QPointF(9,9),4.2,4.2)
    painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#e4213d'))
    painter.drawEllipse(QPointF(9,9),2.6,2.6)
    if state=='remove':
        painter.setPen(QPen(QColor('#ffffff'),1))
        painter.drawLine(QPointF(7,9),QPointF(11,9))
    elif state=='edit':
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#ffcb40'),1.5))
        painter.drawEllipse(QPointF(9,9),6,6)
    painter.end()
    return QCursor(dot,9,9)


class PlateView(QGraphicsView):
    clicked=Signal(float,float)
    drawn=Signal(float,float,float,float)
    quick=Signal(float,float,int,int)
    boundary_edited=Signal(object)
    context_requested=Signal(float,float,object)

    def __init__(self):
        super().__init__();self.setScene(QGraphicsScene(self));self.tool='Inspect';self.start=None;self.rubber=None;self.quick_enabled=False;self.pan_point=None
        self.boundary=None;self.handle_drag=None;self.draft_boundary=None;self.auto_fit=True;self.fit_pending=False
        self.pen_cursors={state:counting_cursor(state) for state in ('add','remove','edit')}
        self.setRenderHint(QPainter.RenderHint.Antialiasing);self.setBackgroundBrush(QColor('#111c28'))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def image(self,rgb,offset=(0,0)):
        self.scene().clear();self.rubber=None;self.start=None;self.boundary=None;self.handle_drag=None
        item=self.scene().addPixmap(pixmap(rgb));item.setPos(*offset)
        self.setSceneRect(item.boundingRect().translated(*offset));self.fit()

    def fit(self):
        self.auto_fit=True
        if not self.sceneRect().isEmpty():self.fitInView(self.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.auto_fit and not self.fit_pending:
            self.fit_pending=True;QTimer.singleShot(0,self.refit_after_resize)

    def refit_after_resize(self):
        if self.auto_fit:self.fit()
        self.fit_pending=False

    def set_tool(self,tool):
        self.tool=tool
        self.update_pointer()

    def update_pointer(self):
        counting=(self.quick_enabled and self.tool=='Inspect') or self.tool in ('Add','Remove','Split','Merge','Reclassify')
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if self.tool=='Inspect' and not counting else QGraphicsView.DragMode.NoDrag)
        if counting:
            state={'Remove':'remove','Split':'edit','Merge':'edit','Reclassify':'edit'}.get(self.tool,'add')
            self.viewport().setCursor(self.pen_cursors[state])
        elif self.tool=='Inspect':self.viewport().unsetCursor()
        else:self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def wheelEvent(self,event):
        self.auto_fit=False
        factor=1.2 if event.angleDelta().y()>0 else 1/1.2
        if .05<self.transform().m11()*factor<40:self.scale(factor,factor)
        event.accept()

    def handles(self,b):
        angle=math.radians(b.angle);c,s=math.cos(angle),math.sin(angle)
        distance=25/max(self.transform().m11(),.01)
        return {name:QPointF(b.cx+c*x-s*y,b.cy+s*x+c*y) for name,(x,y) in
                {'move':(0,0),'left':(-b.rx,0),'right':(b.rx,0),'top':(0,-b.ry),'bottom':(0,b.ry),'rotate':(0,-b.ry-distance)}.items()}

    def move_handle(self,p):
        name,b,start=self.handle_drag;angle=math.radians(b.angle)
        dx,dy=p.x()-b.cx,p.y()-b.cy
        u,v=dx*math.cos(angle)+dy*math.sin(angle),-dx*math.sin(angle)+dy*math.cos(angle)
        if name=='move':b=replace(b,cx=b.cx+p.x()-start.x(),cy=b.cy+p.y()-start.y())
        elif name in ('left','right'):b=replace(b,rx=max(5,abs(u)))
        elif name in ('top','bottom'):b=replace(b,ry=max(5,abs(v)))
        elif math.hypot(dx,dy)>1:b=replace(b,angle=math.degrees(math.atan2(dy,dx))+90)
        self.draft_boundary=replace(b,source='manual')
        self.rubber.setRect(-b.rx,-b.ry,2*b.rx,2*b.ry);self.rubber.setPos(b.cx,b.cy);self.rubber.setRotation(b.angle)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.MiddleButton:self.auto_fit=False;self.pan_point=event.position().toPoint();event.accept();return
        if self.quick_enabled and self.tool=='Inspect' and event.button()==Qt.MouseButton.LeftButton:
            p=self.mapToScene(event.position().toPoint());self.quick.emit(p.x(),p.y(),event.button().value,event.modifiers().value);event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and self.tool!='Inspect':
            p=self.mapToScene(event.position().toPoint())
            if self.tool=='Draw boundary':
                if self.boundary:
                    for name,point in self.handles(self.boundary).items():
                        if (self.mapFromScene(point)-event.position().toPoint()).manhattanLength()<=15:
                            self.handle_drag=(name,self.boundary,p);break
                self.rubber=self.scene().addEllipse(QRectF(p,p),QPen(QColor('#facc15'),2))
                if self.handle_drag:self.move_handle(p)
                else:self.start=p
            elif self.tool in ('Artefact area','Spreading area','Unsuitable area'):
                self.start=p;self.rubber=self.scene().addRect(QRectF(p,p),QPen(QColor('#facc15'),1))
            else:self.clicked.emit(p.x(),p.y())
            event.accept();return
        if event.button()==Qt.MouseButton.LeftButton:self.auto_fit=False
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.pan_point is not None:
            pos=event.position().toPoint();delta=pos-self.pan_point;self.pan_point=pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-delta.x());self.verticalScrollBar().setValue(self.verticalScrollBar().value()-delta.y());event.accept();return
        if self.handle_drag and self.rubber is not None:
            self.move_handle(self.mapToScene(event.position().toPoint()));return
        if self.start is not None and self.rubber is not None:
            self.rubber.setRect(QRectF(self.start,self.mapToScene(event.position().toPoint())).normalized());return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.MiddleButton:self.pan_point=None;event.accept();return
        if self.handle_drag:
            self.move_handle(self.mapToScene(event.position().toPoint()));self.handle_drag=None
            if self.rubber is not None:self.scene().removeItem(self.rubber);self.rubber=None
            self.boundary_edited.emit(self.draft_boundary);event.accept();return
        if self.start is not None:
            p=self.mapToScene(event.position().toPoint());r=QRectF(self.start,p).normalized()
            self.start=None
            if self.rubber is not None:self.scene().removeItem(self.rubber);self.rubber=None
            self.drawn.emit(r.left(),r.top(),r.right(),r.bottom());return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self,event):
        p=self.mapToScene(event.pos());self.context_requested.emit(p.x(),p.y(),event.globalPos());event.accept()

    def overlay(self,plate,numbers=True,selected=None,visible=True,marker_size=1):
        # Preserve the pixmap and camera transform while rebuilding vector markers.
        for item in list(self.scene().items()):
            if not isinstance(item,QGraphicsPixmapItem):self.scene().removeItem(item)
        b=plate.boundary;self.boundary=b
        ellipse=self.scene().addEllipse(-b.rx,-b.ry,2*b.rx,2*b.ry,QPen(QColor('#38bdf8'),1.5))
        ellipse.setPos(b.cx,b.cy);ellipse.setRotation(b.angle)
        inset=plate.parameters.get('rim_inset',.13)
        if inset>0:
            band=QPainterPath();band.addEllipse(-b.rx,-b.ry,2*b.rx,2*b.ry)
            inner=QPainterPath();inner.addEllipse(-b.rx*(1-inset),-b.ry*(1-inset),2*b.rx*(1-inset),2*b.ry*(1-inset))
            shade=self.scene().addPath(band.subtracted(inner),QPen(Qt.PenStyle.NoPen),QBrush(QColor(50,65,88,78)))
            shade.setPos(b.cx,b.cy);shade.setRotation(b.angle)
            ring=self.scene().addEllipse(-b.rx*(1-inset),-b.ry*(1-inset),2*b.rx*(1-inset),2*b.ry*(1-inset),QPen(QColor('#64748b'),1,Qt.PenStyle.DashLine))
            ring.setPos(b.cx,b.cy);ring.setRotation(b.angle)
        if self.tool=='Draw boundary':
            for name,point in self.handles(b).items():
                r=5/max(self.transform().m11(),.01);x,y=point.x(),point.y()
                self.scene().addEllipse(x-r,y-r,2*r,2*r,QPen(QColor('#13283a')),QBrush(QColor('#ffcf53' if name in ('move','rotate') else '#5ee6fb')))
        selected=set(selected or ())
        i=0
        for d in plate.detections:
            if not visible or d.features.get('rim_excluded'):continue
            if d.included:i+=1
            kind=d.features.get('candidate_type')
            if d.id in selected:colour='#facc15'
            elif kind=='broad-mould':colour='#67e8f9' if d.included else '#a78bfa'
            elif kind=='spread-region':colour='#f0abfc'
            elif kind=='pinpoint-in-spread':colour='#a78bfa' if not d.included else '#22c55e'
            elif not d.included and d.category=='artefact':colour='#ef4444'
            elif d.category=='uncertain' or d.features.get('uncertain'):colour='#a78bfa'
            elif not d.included:colour='#ef4444'
            else:colour='#22c55e' if d.origin=='manual' else '#fb923c'
            if d.features.get('outline'):
                points=d.features['outline']
                path=QPainterPath(QPointF(*points[0]))
                for x,y in points[1:]:path.lineTo(x,y)
                path.closeSubpath()
                pen=QPen(QColor(colour),1.25,Qt.PenStyle.SolidLine if d.included else Qt.PenStyle.DashLine)
                self.scene().addPath(path,pen)
            r=max(2,min(d.radius,8 if kind in ('broad-mould','spread-region') else 10))*marker_size
            self.scene().addEllipse(d.x-r,d.y-r,2*r,2*r,QPen(QColor(colour),1))
            if numbers and d.included:
                t=self.scene().addSimpleText(str(i));t.setBrush(QColor(colour));t.setPos(d.x+r,d.y-r)
                font=t.font();font.setPointSize(6);t.setFont(font)
        for e in plate.exclusions:
            x1,y1,x2,y2=e['rect'];self.scene().addRect(x1,y1,x2-x1,y2-y1,QPen(QColor('#ef4444'),2))


class PlateResultCard(QGroupBox):
    def __init__(self):
        super().__init__('Live plate result');form=QFormLayout(self);self.values={}
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for key,label in [('count','Colonies'),('confirmation','Confirmation'),('dilution','Total dilution'),('volume','Inoculum'),('result','Concentration')]:
            widget=QLabel('—');widget.setTextFormat(Qt.TextFormat.PlainText);widget.setWordWrap(True);widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);self.values[key]=widget;form.addRow(label,widget)
        self.values['result'].setStyleSheet('font-weight:700; color:#163c4d')

    def update_result(self,observation,result,display):
        o=observation;r=result;value=r['value'] if r else None
        count=f'{o.count:,}' if o and o.count is not None else o.state.upper() if o and o.state!='missing' else '—'
        confirmation=f'{o.confirmed}/{o.tested} · {o.count*o.confirmed/o.tested:,.6g} applied' if o and o.tested and o.count is not None else 'Unadjusted'
        result_text=display
        if value is not None and abs(value)>=1000:
            prefix={'less_than':'< ','greater_than':'> '}.get(r['qualifier'],'')
            decimal=f'{value:,.4f}'.rstrip('0').rstrip('.')
            result_text+=f'\n{prefix}{decimal} {r["unit"]}'
        for key,text in {'count':count,'confirmation':confirmation if o else '—','dilution':f'1 in {1/o.dilution:.6g}' if o else '—','volume':f'{o.volume_ml:.6g} mL' if o else '—','result':result_text}.items():self.values[key].setText(text)


class MainWindow(QMainWindow):
    def __init__(self,data_dir):
        super().__init__();self.store=ProjectStore(data_dir);self.library=MediaLibrary(self.store.root);self.project=Project();self.rgb={};self.current_id=None
        self.pref_path=self.store.root/'ui_preferences.json'
        try:self.preferences=json.loads(self.pref_path.read_text(encoding='utf-8')) if self.pref_path.exists() else {}
        except (ValueError,OSError):self.preferences={}
        self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1);self.task=None;self.setAcceptDrops(True)
        self.pending_ids=[];self.split_points=[];self.diagnostic=None;self.loading=False;self.dirty=False
        self.setWindowTitle(f'FoodMicro Count {DISPLAY_VERSION}');self.setWindowIcon(QIcon(str(ICON_PATH)));self.resize(1280,800)
        body=QWidget();layout=QVBoxLayout(body);self.setCentralWidget(body)
        heading=QHBoxLayout();layout.addLayout(heading)
        self.brand=QLabel();self.brand.setPixmap(QPixmap(str(ICON_PATH)).scaled(42,42,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        heading.addWidget(self.brand)
        title=QLabel('FoodMicro Count');title.setStyleSheet('font-size:26px; font-weight:700; color:#163c4d');heading.addWidget(title);heading.addStretch()
        version=QLabel(DISPLAY_VERSION);version.setStyleSheet('color:#385465');heading.addWidget(version)
        self.credit=QLabel('Developed by Yaw Agyei Gyasi · Connect: <a href="https://linktr.ee/yawgyasi">linktr.ee/yawgyasi</a>')
        self.credit.setOpenExternalLinks(True)
        self.credit.setStyleSheet('color:#385465; padding-bottom:3px')
        layout.addWidget(self.credit)
        bar=QHBoxLayout();layout.addLayout(bar)
        for label,fn in [('New session',self.new_project),('Open image',self.open_image),('Open saved',self.open_saved),('Save session',self.save_project),('Export results',self.export),('Backup',self.backup)]:
            b=QPushButton(label);b.clicked.connect(fn);bar.addWidget(b)
        bar.addStretch();bar.addWidget(QLabel('Analyst:'));self.actor=QLineEdit();self.actor.setPlaceholderText('Your name');self.actor.setMaximumWidth(220);bar.addWidget(self.actor)
        split=QSplitter();layout.addWidget(split,1)
        left=QWidget();self.session_list_panel=left;ll=QVBoxLayout(left);ll.addWidget(QLabel('Plates in this session'))
        self.plate_list=QListWidget();self.plate_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.plate_list.setTextElideMode(Qt.TextElideMode.ElideMiddle);self.plate_list.currentRowChanged.connect(self.select_plate);ll.addWidget(self.plate_list)
        self.dashboard=QLabel('Open or drop a photograph to start counting.');self.dashboard.setWordWrap(True);ll.addWidget(self.dashboard)
        add=QPushButton('Adjust agar ellipse');add.clicked.connect(lambda:self.set_tool('Draw boundary'));ll.addWidget(add)
        add_new=QPushButton('Add a missed plate');add_new.clicked.connect(self.add_manual_plate);ll.addWidget(add_new)
        group=QPushButton('Group / edit plate records');group.clicked.connect(self.group_plates);ll.addWidget(group)
        remove=QPushButton('Remove selected plate');remove.clicked.connect(self.remove_plate);ll.addWidget(remove)
        split.addWidget(left)
        self.tabs=QTabWidget();split.addWidget(self.tabs);split.setSizes([230,1270])
        self.build_counter();self.build_results();self.build_media();self.build_settings()
        self.live_timer=QTimer(self);self.live_timer.setSingleShot(True);self.live_timer.setInterval(60);self.live_timer.timeout.connect(self.refresh_results)
        self.tabs.currentChanged.connect(lambda _:self.schedule_results())
        self.refresh_results()
        self.statusBar().showMessage(f'Local records: {self.store.root}')
        self.progress=QProgressBar();self.progress.setMaximumWidth(180);self.progress.setVisible(False);self.statusBar().addPermanentWidget(self.progress)
        self.cancel_task=QPushButton('Cancel task');self.cancel_task.clicked.connect(self.cancel_work);self.cancel_task.setVisible(False);self.statusBar().addPermanentWidget(self.cancel_task)
        footer=QLabel('Yaw Agyei Gyasi · <a href="https://linktr.ee/yawgyasi">Connect</a>');footer.setOpenExternalLinks(True);self.statusBar().addPermanentWidget(footer)
        for seq,fn in [('Ctrl+S',self.save_project),('Ctrl+Z',lambda:self.history(False)),('Ctrl+Y',lambda:self.history(True)),('F11',self.toggle_focus),('Esc',lambda:self.toggle_focus() if self.focusing else None)]:
            action=QAction(self);action.setShortcut(QKeySequence(seq));action.triggered.connect(fn);self.addAction(action)
        self.setStyleSheet('QPushButton{padding:6px 10px;} QGroupBox{font-weight:600;} QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox{padding:3px;}')

    def build_counter(self):
        page=QWidget();outer=QVBoxLayout(page);tools=QHBoxLayout();outer.addLayout(tools)
        self.active_tool='Inspect';self.focusing=False;self.focus_dialog=None
        self.numbered=QCheckBox('Numbers');self.numbered.setChecked(True);self.numbered.toggled.connect(self.redraw);tools.addWidget(self.numbered)
        self.processed=QCheckBox('Contrast view');self.processed.toggled.connect(self.refresh_images);tools.addWidget(self.processed)
        self.focus_button=QPushButton('Focus plate');self.focus_button.setToolTip('Full screen (F11); Esc to return');self.focus_button.clicked.connect(self.toggle_focus);tools.addWidget(self.focus_button)
        self.quick_mode=QCheckBox('Counting pointer');self.quick_mode.setToolTip('Left-click to add; Shift+click to remove');self.quick_mode.toggled.connect(self.configure_quick);tools.addWidget(self.quick_mode)
        self.markers=QCheckBox('Show markers');self.markers.setChecked(True);self.markers.toggled.connect(self.redraw);tools.addWidget(self.markers)
        self.marker_size=QDoubleSpinBox();self.marker_size.setRange(.5,3);self.marker_size.setValue(1);self.marker_size.valueChanged.connect(self.redraw);tools.addWidget(QLabel('Marker size'));tools.addWidget(self.marker_size)
        self.show_source=QCheckBox('Source photo');self.show_source.setChecked(True);tools.addWidget(self.show_source)
        tools.addStretch()
        main=QSplitter();outer.addWidget(main,1)
        views=QWidget();vl=QVBoxLayout(views)
        viewrow=QSplitter();self.original_view=PlateView();self.counter_view=PlateView();self.source_panel=QWidget();self.counter_panel=QWidget()
        for widget,label,view in [(self.source_panel,'Source photograph',self.original_view),(self.counter_panel,'Selected plate',self.counter_view)]:
            column=QVBoxLayout(widget);column.setContentsMargins(0,0,0,0);column.addWidget(QLabel(label));column.addWidget(view,1);viewrow.addWidget(widget)
        vl.addWidget(viewrow,1);viewrow.setSizes([235,600]);viewrow.setStretchFactor(1,1)
        self.show_source.toggled.connect(self.source_panel.setVisible)
        for view in [self.original_view,self.counter_view]:view.clicked.connect(self.click);view.drawn.connect(self.draw);view.quick.connect(self.quick_click);view.boundary_edited.connect(self.commit_boundary);view.context_requested.connect(self.plate_menu)
        self.count_label=QLabel('No plate selected');self.count_label.setStyleSheet('font-size:18px; font-weight:600');vl.addWidget(self.count_label)
        self.appearance_label=QLabel();self.appearance_label.setTextFormat(Qt.TextFormat.PlainText);self.appearance_label.setWordWrap(True);vl.addWidget(self.appearance_label)
        self.count_label.setWordWrap(True);review_bar=QHBoxLayout();vl.addLayout(review_bar)
        self.tntc_button=QPushButton('Mark TNTC');self.tntc_button.clicked.connect(self.mark_tntc);review_bar.addWidget(self.tntc_button)
        adjust=QPushButton('Adjust ellipse');adjust.clicked.connect(lambda:self.set_tool('Draw boundary'));review_bar.addWidget(adjust);review_bar.addStretch()
        self.mark_summary=QLabel();self.mark_summary.setWordWrap(True);self.mark_summary.hide();vl.addWidget(self.mark_summary)
        self.messages=QLabel();self.messages.setWordWrap(True);vl.addWidget(self.messages)
        main.addWidget(views)
        scroll=QScrollArea();self.detection_panel=scroll;scroll.setWidgetResizable(True);panel=QWidget();pl=QVBoxLayout(panel);scroll.setWidget(panel);main.addWidget(scroll);main.setSizes([850,320])
        self.count_result=PlateResultCard();pl.addWidget(self.count_result)
        g=QGroupBox('Detection');f=QFormLayout(g);f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows);pl.addWidget(g);self.controls={}
        self.medium=QComboBox();self.medium.addItems(list(self.library.database()['media']));f.addRow('Medium profile',self.medium)
        profile_info=QPushButton('View selected profile');profile_info.clicked.connect(self.view_current_medium);f.addRow(profile_info)
        self.mode=QComboBox()
        for label,mode in [('Quick Count','automatic'),('Assisted Count','assisted'),('Manual Count','manual')]:
            self.mode.addItem(label,mode)
        self.mode.currentIndexChanged.connect(self.mode_changed);f.addRow('Counting mode',self.mode)
        self.advanced=QCheckBox('Advanced detection settings');self.advanced.setChecked(self.preferences.get('advanced',False));f.addRow(self.advanced)
        advanced=QWidget();af=QFormLayout(advanced);f.addRow(advanced);advanced.setVisible(self.advanced.isChecked());self.advanced.toggled.connect(advanced.setVisible);self.advanced.toggled.connect(lambda v:self.save_preference('advanced',v))
        self.strategy=QComboBox();self.strategy.addItems(['hybrid','local','log']);af.addRow('Detector',self.strategy)
        self.polarity=QComboBox();self.polarity.addItems(['profile','bright','dark','both']);af.addRow('Contrast polarity',self.polarity)
        for key,label,lo,hi,step,value in [('sensitivity','Sensitivity',.25,4,.1,1),('min_radius_px','Min radius (pixels)',.5,149,.1,.8),('max_radius_px','Max radius (pixels)',1,150,.5,12),('min_contrast','Min local contrast',1,100,1,9),('rim_inset','Rim exclusion (0.13 = 13%)',0,.35,.01,.13)]:
            w=QDoubleSpinBox();w.setRange(lo,hi);w.setSingleStep(step);w.setValue(value);w.setDecimals(2);self.controls[key]=w;af.addRow(label,w)
            w.setToolTip({'sensitivity':'Higher sensitivity proposes more faint objects and may add artefacts.','rim_inset':'Filters candidate centres after detection. 0.00 shows the full agar ellipse.'}.get(key,'Use original image pixels and check against reviewed plates.'))
        self.use_profile_sizes=QCheckBox('Use medium size ranges');self.use_profile_sizes.setChecked(True);af.addRow(self.use_profile_sizes)
        self.use_profile_sizes.setToolTip('DRBC, DG18 and PDA use separate compact and broad ranges, scaled by the dish diameter.')
        diameter=QDoubleSpinBox();diameter.setRange(35,200);diameter.setValue(90);diameter.setDecimals(1)
        diameter.setToolTip('Enter the physical diameter of the fitted agar. Size estimates depend on the ellipse fit.')
        self.controls['dish_diameter_mm']=diameter;af.addRow('Dish diameter (mm)',diameter)
        self.size_summary=QLabel();self.size_summary.setWordWrap(True);af.addRow(self.size_summary)
        self.inspect_spreading=QCheckBox('Flag spreaders and internal pinpoints');self.inspect_spreading.setChecked(True);af.addRow(self.inspect_spreading)
        self.screen_marks=QCheckBox('Flag likely ink and glare');self.screen_marks.setChecked(True);af.addRow(self.screen_marks)
        self.ai_button=QPushButton('AI Assist flagged marks…');self.ai_button.setEnabled(False);self.ai_button.clicked.connect(self.ai_review_flagged);af.addRow(self.ai_button)
        self.split_touching=QCheckBox('Watershed touching objects');self.split_touching.setChecked(True);af.addRow(self.split_touching)
        self.colour=QCheckBox('Apply custom HSV filter');af.addRow(self.colour)
        for key,label,hi in [('hue_min','Hue minimum (0–179)',179),('hue_max','Hue maximum',179),('saturation_min','Minimum saturation',255)]:
            w=QDoubleSpinBox();w.setRange(0,hi);w.setValue(179 if key=='hue_max' else 0);self.controls[key]=w;af.addRow(label,w)
        run=QPushButton('Run colony detection');run.clicked.connect(lambda:self.run_count(asynchronous=True));f.addRow(run)
        self.boundary_ok=QCheckBox('Boundary checked');f.addRow(self.boundary_ok)
        self.quality_ok=QCheckBox('Image and rim checked');f.addRow(self.quality_ok)
        review=QPushButton('Record analyst check');review.clicked.connect(self.review);f.addRow(review)
        b=QPushButton('Boundary coordinates…');b.clicked.connect(self.edit_boundary);af.addRow(b)
        g=QGroupBox('Selected plate and preparation');self.record_box=g;f=QFormLayout(g);f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows);self.fields={}
        extras=QWidget();ef=QFormLayout(extras);ef.setContentsMargins(0,0,0,0)
        for key,label in [('sample_id','Sample ID'),('sample_name','Sample name'),('test_id','Test ID (shared by replicates)'),('test','Test'),('date','Analysis date'),('replicate','Replicate label'),('manufacturer','Manufacturer'),('medium_lot','Medium lot'),('incubation','Incubation record'),('notes','Plate notes')]:
            w=QLineEdit();self.fields[key]=w;(f if key in ('sample_id','sample_name','test_id','test','replicate') else ef).addRow(label,w)
        self.method=QComboBox()
        for k,v in METHODS.items():self.method.addItem(v['name'],k)
        self.method.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.method.setMinimumContentsLength(18)
        f.addRow('Calculation method / edition',self.method)
        for key,label,hi,value in [('sample_amount','Original sample amount',1e6,10),('first_diluent_ml','First diluent (mL)',1e6,90),('additional_dilution_factor','Additional inverse DF',1e12,1),('plated_ml','Volume plated (mL)',1000,1)]:
            w=QDoubleSpinBox();w.setDecimals(6);w.setRange(0 if key=='first_diluent_ml' else .000001,hi);w.setValue(value);self.fields[key]=w;f.addRow(label,w)
        self.initial_override=QLineEdit();self.initial_override.setPlaceholderText('Blank = amount/(amount+diluent)');ef.addRow('Explicit initial fraction',self.initial_override)
        self.units=QComboBox();self.units.addItems(['g','mL']);f.addRow('Original sample unit',self.units)
        self.plating=QComboBox();self.plating.addItems(['pour','spread','membrane','drop']);ef.addRow('Plating technique',self.plating)
        self.observation=QComboBox();self.observation.addItems(['numeric','zero','TNTC','spreading','contaminated','unreadable','missing','estimated','unsuitable']);f.addRow('Observation state',self.observation)
        self.record_count=QLabel('Choose a plate');self.record_count.setWordWrap(True);f.addRow('Plate observation',self.record_count)
        self.tested=QLineEdit();self.confirmed=QLineEdit();self.tested.setPlaceholderText('Optional');self.confirmed.setPlaceholderText('Optional');f.addRow('Colonies tested',self.tested);f.addRow('Colonies confirmed',self.confirmed)
        more=QCheckBox('More plate details');f.addRow(more);f.addRow(extras);extras.hide();more.toggled.connect(extras.setVisible)
        self.dilution_label=QLabel('Total DF calculated from preparation');self.dilution_label.setWordWrap(True);f.addRow(self.dilution_label)
        b=QPushButton('Apply plate record');b.clicked.connect(self.apply_and_calculate);f.addRow(b)
        pl.addWidget(self.record_box)
        pl.addStretch();self.tabs.addTab(page,'Plate Count')
        for combo in panel.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8);combo.setMinimumWidth(100);combo.setMaximumWidth(240)
        # Editing inputs invalidates review immediately; unsaved visible changes cannot masquerade as reviewed.
        for w in list(self.fields.values())+[self.initial_override,self.tested,self.confirmed]:
            (w.textChanged if isinstance(w,QLineEdit) else w.valueChanged).connect(self.input_changed)
        for w in [self.medium,self.method,self.units,self.plating,self.observation]:w.currentIndexChanged.connect(self.input_changed)
        for key,w in self.controls.items():w.valueChanged.connect(self.rim_changed if key=='rim_inset' else self.setting_changed)
        for w in [self.strategy,self.polarity]:w.currentIndexChanged.connect(self.setting_changed)
        for w in [self.split_touching,self.colour,self.use_profile_sizes,self.inspect_spreading,self.screen_marks]:w.toggled.connect(self.setting_changed)

    def build_results(self):
        page=QWidget();layout=QVBoxLayout(page);split=QSplitter();layout.addWidget(split,1)
        results=QWidget();split.addWidget(results);split.setSizes([370,780]);l=QVBoxLayout(results)
        self.data_result=PlateResultCard();l.addWidget(self.data_result)
        actions=QHBoxLayout();l.addLayout(actions)
        for label,fn in [('Calculate',self.apply_and_calculate),('Edit plate record',self.show_plate_record),('Save Excel template',self.save_excel_template),('Export to Excel layout…',self.export_excel_layout)]:
            b=QPushButton(label);b.clicked.connect(fn);actions.addWidget(b)
        self.results_table=QTableWidget(0,4);self.results_table.setHorizontalHeaderLabels(['Sample ID','Test','Sample result','Qualifier']);self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.results_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch);l.addWidget(self.results_table,2)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.calculation_detail=QPlainTextEdit();self.calculation_detail.setReadOnly(True);self.calculation_detail.setMaximumHeight(94);l.addWidget(self.calculation_detail)
        self.results_table.currentCellChanged.connect(self.result_details)
        self.build_spreadsheets(l)
        notice=QLabel(RESULT_NOTICE);notice.setWordWrap(True);notice.setStyleSheet('color:#526475');l.addWidget(notice)
        self.tabs.addTab(page,'Data && Results')

    def show_plate_record(self):
        self.tabs.setCurrentIndex(0)
        self.detection_panel.ensureWidgetVisible(self.record_box, 0, 8)
        self.fields['sample_id'].setFocus()

    def build_media(self):
        self.media_panel=MediaPanel(self,self.library);self.tabs.addTab(self.media_panel,'Media')

    def build_spreadsheets(self,parent_layout):
        page=QWidget();layout=QVBoxLayout(page)
        toggle=QCheckBox('Spreadsheet import and manual data entry');parent_layout.addWidget(toggle);parent_layout.addWidget(page,1);page.hide();toggle.toggled.connect(page.setVisible)
        label=QLabel('Import a workbook or enter counts directly. Spreadsheet observations are listed separately from image counts.');label.setWordWrap(True);layout.addWidget(label)
        bar=QHBoxLayout();layout.addLayout(bar)
        for text,fn in [('Import Excel',self.import_excel),('Enter / paste count data',self.enter_count_data),('Review selected rows',self.review_external),('Refresh',self.refresh_imports)]:
            button=QPushButton(text);button.clicked.connect(fn);bar.addWidget(button)
        self.import_table=QTableWidget(0,10);self.import_table.setHorizontalHeaderLabels(['Workbook','Sheet / row','Sample ID','Test ID','Test','Count','Total DF','Volume mL','Review','Method result'])
        self.import_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.import_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.import_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.import_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.import_table);self.import_detail=QPlainTextEdit();self.import_detail.setReadOnly(True);layout.addWidget(self.import_detail)
        self.import_table.currentCellChanged.connect(self.external_details);self.external_rows=[]

    def apply_and_calculate(self):
        try:self.apply_meta(silent=True);self.refresh_results();self.refresh_imports()
        except Exception as e:self.error(e)

    def save_excel_template(self):
        from shutil import copyfile
        path=QFileDialog.getSaveFileName(self,'Save example template','FoodMicro_Count_Excel_Template.xlsx','Excel (*.xlsx)')[0]
        if not path:return
        try:copyfile(Path(__file__).with_name('assets')/'example_template.xlsx',path)
        except Exception as e:self.error(e)

    def export_excel_layout(self):
        from .ui_support import ResultLayout
        from .reporting import template_records,write_result_layout
        path=QFileDialog.getOpenFileName(self,'Choose Excel layout','','Excel (*.xlsx)')[0]
        if not path:return
        try:
            if self.plate:self.apply_meta(silent=True)
            rows=template_records(self.project)
            if not rows:raise ValueError('No count observations are ready to export')
            dialog=ResultLayout(self,path,rows)
            if not dialog.exec():return
            sheet,first,mapping=dialog.selection()
            target=QFileDialog.getSaveFileName(self,'Save results to a new workbook','FoodMicro_Count_Mapped_Results.xlsx','Excel (*.xlsx)')[0]
            if target:write_result_layout(path,target,sheet,first,mapping,rows);self.statusBar().showMessage('Saved results to the mapped workbook')
        except Exception as e:self.error(e)

    def import_excel(self,path=None):
        interactive=not path or isinstance(path,bool)
        if interactive:path=QFileDialog.getOpenFileName(self,'Import spreadsheet observations','','Excel workbook (*.xlsx)')[0]
        if not path:return
        try:
            if not self.actor.text().strip():raise ValueError('Enter your analyst name before importing')
            options={};sheet=None
            if interactive:
                preview=WorkbookSetup(self,path)
                if not preview.exec():return
                sheet,mapping,row=preview.selection();options={'column_mapping':mapping,'selected_header_row':row}
            batch=self.store.import_workbook(path,self.project,sheet,**options)
            batch['events'].append({'time':timestamp(),'actor':self.actor.text().strip(),'action':'import','reason':'External workbook ingestion; source bytes preserved'})
            self.dirty=True;self.refresh_imports()
        except Exception as e:self.error(e)

    def enter_count_data(self):
        dialog=QDialog(self);dialog.setWindowTitle('Enter external plate observations');dialog.resize(1000,440);layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Raw observations, separate from image markers. Paste tab-separated cells; select rows and review after import.'))
        headers=['SAMPLE ID','TEST ID','TEST','REPLICATE','MASS OF SAMPLE','VOLUME OF FIRST DILUENT','ADDITIONAL INVERSE DILUTION','NUMBER OF COLONIES','VOLUME OF INOCULUM','RESULT UNIT','METHOD VERSION','OBSERVATION']
        table=PasteTable(10,len(headers));table.setHorizontalHeaderLabels(headers);layout.addWidget(table)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if not dialog.exec():return
        try:
            if not self.actor.text().strip():raise ValueError('Enter the analyst name')
            from openpyxl import Workbook
            workbook=Workbook();sheet=workbook.active;sheet.append(headers+['DILUTION FACTOR USED'])
            for row in range(table.rowCount()):
                values=[table.item(row,col).text().strip() if table.item(row,col) else None for col in range(table.columnCount())]
                if not any(values):continue
                for col in (4,5,6,7,8):
                    if values[col] not in (None,''):
                        try:values[col]=float(values[col])
                        except ValueError:pass
                from .reporting import safe_cell
                from .calculations import initial_fraction, total_fraction
                try:total=1/total_fraction(initial_fraction(float(values[4]),float(values[5])),float(values[6]))
                except (ValueError,TypeError,ZeroDivisionError) as e:raise ValueError(f'Row {row+1}: enter a valid sample amount, first diluent and additional dilution factor') from e
                sheet.append([safe_cell(v) for v in values]+[total])
            path=self.store.root/('manual_table_'+uid()+'.xlsx');workbook.save(path);self.import_excel(str(path))
        except Exception as e:self.error(e)

    def refresh_imports(self):
        from .spreadsheet_io import records,fields,import_results
        self.external_rows=[]
        for batch in self.project.imported_workbooks:
            results={row:r for r in import_results(batch) for row in r['rows']}
            for record in records(batch):self.external_rows.append((batch,record,results[record['id']]))
        self.import_table.setRowCount(len(self.external_rows))
        for i,(batch,record,result) in enumerate(self.external_rows):
            f=fields(record);values=[batch['source_name'],f"{record['sheet']} / {record['row']}",f.get('sample_id'),f.get('test_id'),f.get('test'),f.get('count'),f.get('total_inverse'),f.get('volume_ml'),record['review_status'],result['display']]
            for j,value in enumerate(values):self.import_table.setItem(i,j,QTableWidgetItem('' if value is None else str(value)))

    def external_details(self,row,*args):
        if 0<=row<len(self.external_rows):
            batch,record,result=self.external_rows[row]
            self.import_detail.setPlainText(json.dumps({'source_sha256':batch['sha256'],'record':record,'sample_result':result},indent=2,ensure_ascii=False))

    def review_external(self):
        from .spreadsheet_io import fields,review_rows
        selected=sorted({index.row() for index in self.import_table.selectedIndexes()})
        if not selected:return
        targets=[self.external_rows[i] for i in selected];batch=targets[0][0]
        if any(t[0]['id']!=batch['id'] for t in targets):self.error('Select rows from one workbook per review');return
        dialog=QDialog(self);dialog.setWindowTitle('Review external observations');form=QFormLayout(dialog);inputs={};f=fields(targets[0][1])
        label=QLabel('Blank fields preserve each source row. Enter identifiers deliberately; selected rows become one test group when they share Sample ID and Test ID. Review all source warnings first.');label.setWordWrap(True);form.addRow(label)
        for key,label in [('sample_id','Sample ID'),('test_id','Test ID'),('test','Test'),('medium','Medium'),('replicate','Replicate (one row only)'),('count','Corrected count (one row only)'),('total_inverse','Total inverse dilution'),('volume_ml','Plated volume mL'),('tested','Colonies tested'),('confirmed','Colonies confirmed')]:
            widget=QLineEdit();widget.setPlaceholderText('Blank preserves source value');inputs[key]=widget;form.addRow(label,widget)
        units=QComboBox();units.addItems(['CFU/g','CFU/mL']);units.setCurrentText(f.get('unit') or 'CFU/g');form.addRow('Original sample basis (check)',units)
        method=QComboBox()
        for key,value in METHODS.items():method.addItem(value['name'],key)
        method.setCurrentIndex(max(0,method.findData(f.get('method_id'))));form.addRow('Explicit calculation method',method)
        reason=QLineEdit();reason.setPlaceholderText('Required: review source data and describe any correction');form.addRow('Review reason',reason)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            changes={k:w.text().strip() for k,w in inputs.items() if w.text().strip()}
            if len(targets)>1 and any(k in changes for k in ('replicate','count')):raise ValueError('Review count or replicate corrections one row at a time')
            for key in ('count','total_inverse','volume_ml','tested','confirmed'):
                if key in changes:changes[key]=float(changes[key])
            changes.update(unit=units.currentText(),method_id=method.currentData())
            review_rows(batch,[t[1]['id'] for t in targets],changes,self.actor.text(),reason.text())
            self.dirty=True;self.refresh_imports();self.save_project()
        except Exception as e:self.error(e)

    def build_quality(self):
        page=QDialog(self);self.quality_dialog=page;page.setWindowTitle('Counting diagnostics');page.resize(760,530);l=QVBoxLayout(page)
        self.dataset_role=QComboBox();self.dataset_role.addItems(['demonstration','development','reference-counted','held-out evaluation','formal validation']);self.dataset_role.setCurrentText('development');self.dataset_role.currentTextChanged.connect(self.set_dataset_role);l.addWidget(QLabel('Dataset designation (an analyst declaration, not proof of validation)'));l.addWidget(self.dataset_role)
        q=QLabel('No independent expert reference counts have been supplied. Automatic counts are candidate counts.\nLabel reference colony centres and physical plate groups, then run foodmicro-cli evaluate.\nThe validation plan and observed software tests are in docs/VALIDATION.md.');q.setWordWrap(True);l.addWidget(q)
        self.quality_text=QPlainTextEdit();self.quality_text.setReadOnly(True);l.addWidget(self.quality_text)

    def build_settings(self):
        page=QWidget();l=QVBoxLayout(page)
        self.build_quality();diagnostics=QPushButton('Open diagnostics…');diagnostics.clicked.connect(self.quality_dialog.show);l.addWidget(diagnostics)
        if self.library.load_warning:
            warning=QLabel(self.library.load_warning);warning.setWordWrap(True);l.addWidget(warning)
        q=QLabel(f'Local data folder: {self.store.root}\nCounting, calculations and export run offline. Optional AI Assist sends only approved crops.\nDetection settings are saved with each plate.');q.setWordWrap(True);l.addWidget(q)
        f=QFormLayout();self.lab=QLineEdit(self.project.laboratory);f.addRow('Laboratory',self.lab);l.addLayout(f)
        f.addRow('Counting pointer',QLabel('Left-click adds · Shift+click removes · Right-click opens plate actions'))
        about=QPushButton('About FoodMicro Count');about.clicked.connect(self.show_about);l.addWidget(about)
        q=QLabel('Default initial fraction uses amount/(amount + diluent). Enter an explicit fraction for a different preparation. An initial 1/50 plus two decimal dilutions gives a total dilution factor of 5,000.');q.setWordWrap(True);l.addWidget(q);l.addStretch();self.tabs.addTab(page,'Settings')

    def show_about(self):
        QMessageBox.about(self, 'About FoodMicro Count',
            f'<b>FoodMicro Count · {DISPLAY_VERSION}</b><br>Developed by Yaw Agyei Gyasi<br><br>'
            'Offline colony counting, analyst review and quantitative results for food microbiology.<br><br>'
            'FoodMicro Count is licensed under GPL-3.0-or-later.<br>'
            'Uses Qt and Qt for Python under LGPLv3. Qt is &copy; The Qt Company Ltd. and other contributors.<br>'
            'Also uses OpenCV, NumPy, SciPy, scikit-image, Pillow and openpyxl. '
            'See the included licence and third-party notices.')

    @property
    def plate(self):return next((p for p in self.project.plates if p.id==self.current_id),None)

    def error(self,e):QMessageBox.warning(self.focus_dialog or self,'FoodMicro Count',str(e))

    def save_preference(self,key,value):
        self.preferences[key]=value;self.pref_path.write_text(json.dumps(self.preferences,indent=2),encoding='utf-8')

    def set_dataset_role(self,value):
        if self.plate and not self.loading:self.plate.dataset_role=value;self.dirty=True

    def refresh_media_options(self):
        old=self.medium.currentText();self.medium.blockSignals(True);self.medium.clear();self.medium.addItems(list(self.library.database()['media']));self.medium.setCurrentText(old);self.medium.blockSignals(False)

    def view_current_medium(self):
        self.media_panel.select_medium(self.medium.currentText());self.tabs.setCurrentWidget(self.media_panel)

    def toggle_focus(self):
        if self.focus_dialog:self.focus_dialog.reject();return
        if not self.plate:return
        self.tabs.setCurrentIndex(0)
        self.quick_before_focus=self.quick_mode.isChecked();self.quick_mode.setChecked(True)
        self.set_tool('Inspect')
        view=self.counter_view
        self.focus_camera=(view.auto_fit,view.transform(),view.mapToScene(view.viewport().rect().center()))
        dlg=QDialog(self,Qt.WindowType.Window);dlg.setWindowTitle('FoodMicro Count — Focus plate');dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self.focus_dialog=dlg;self.focusing=True;layout=QVBoxLayout(dlg);layout.setContentsMargins(7,7,7,7)
        toolbar=QHBoxLayout();layout.addLayout(toolbar)
        self.focus_count=QLabel();self.focus_count.setStyleSheet('font-size:17px; font-weight:650');toolbar.addWidget(self.focus_count)
        quick=QCheckBox('Counting pointer');quick.setChecked(self.quick_mode.isChecked());quick.toggled.connect(self.quick_mode.setChecked);self.quick_mode.toggled.connect(quick.setChecked);toolbar.addWidget(quick)
        markers=QCheckBox('Show markers');markers.setChecked(self.markers.isChecked());markers.toggled.connect(self.markers.setChecked);self.markers.toggled.connect(markers.setChecked);toolbar.addWidget(markers)
        toolbar.addStretch();mode=QPushButton('Windowed');toolbar.addWidget(mode)
        def change_size():
            if dlg.isFullScreen():dlg.showNormal();dlg.resize(1100,800);mode.setText('Full screen')
            else:dlg.showFullScreen();mode.setText('Windowed')
            QTimer.singleShot(0,view.fit)
        mode.clicked.connect(change_size)
        back=QPushButton('Back to session · Esc');back.clicked.connect(dlg.reject);toolbar.addWidget(back)
        layout.addWidget(view,1)
        self.focus_hint=QLabel('Wheel: zoom   ·   Middle drag: pan   ·   Counting pointer: click to add, Shift+click to remove   ·   Right-click: plate actions');self.focus_hint.setWordWrap(True);layout.addWidget(self.focus_hint)
        QShortcut(QKeySequence('F11'),dlg).activated.connect(dlg.reject)
        QShortcut(QKeySequence('Ctrl+Z'),dlg).activated.connect(lambda:self.history(False))
        QShortcut(QKeySequence('Ctrl+Y'),dlg).activated.connect(lambda:self.history(True))
        dlg.finished.connect(self.restore_focus);dlg.showFullScreen();view.show();view.setFocus();self.update_counts();QTimer.singleShot(0,view.fit)

    def restore_focus(self,*args):
        dlg=self.focus_dialog
        if dlg is None:return
        self.focus_dialog=None;self.focusing=False;self.quick_mode.setChecked(self.quick_before_focus)
        view=self.counter_view;self.counter_panel.layout().addWidget(view,1);view.show()
        auto,transform,centre=self.focus_camera
        if auto:QTimer.singleShot(0,view.fit)
        else:view.auto_fit=False;view.setTransform(transform);view.centerOn(centre)
        view.setFocus();dlg.deleteLater();self.schedule_results()

    def configure_quick(self,enabled):
        for view in (self.original_view,self.counter_view):view.quick_enabled=enabled;view.update_pointer()
        if enabled:self.set_tool('Inspect')

    def mode_changed(self,index):
        if self.loading or not self.plate:return
        mode=self.mode.itemData(index)
        if mode=='manual':
            self.manual_mode()
        else:
            if self.plate.count_mode!='manual' and self.plate.count_mode!=mode:
                self.plate.count_mode=mode
                self.dirty=True
            self.quick_mode.setChecked(mode=='assisted')

    def manual_mode(self):
        if not self.plate:return
        try:
            self.apply_meta(silent=True)
            profile=copy.deepcopy(self.library.database()['media'].get(self.plate.meta.medium,{}))
            if not enumeration_scope(self.plate.meta.test,self.plate.meta.medium,profile):raise ValueError('Select a colony enumeration test')
            self.plate.begin_manual(self.actor.text(),'Independent manual count');self.plate.parameters['profile_snapshot']=profile
            self.loading=True;self.mode.setCurrentIndex(self.mode.findData('manual'));self.loading=False;self.quick_mode.setChecked(True);self.dirty=True;self.redraw()
        except Exception as e:self.error(e)

    def retain_observation(self):
        if not self.plate:return
        try:
            independent=self.plate.count_mode=='manual' and QMessageBox.question(self,'Reference observation','Was this count made independently of the automatic detections?')==QMessageBox.StandardButton.Yes
            self.plate.retain_layer(self.actor.text(),'Retain paired count observation',independent);self.dirty=True
        except Exception as e:self.error(e)

    def choose_layer(self):
        if not self.plate or not self.plate.count_layers:return
        layers=list(self.plate.count_layers);labels=[f'{x["time"][:19]} / {x["actor"]} / {x["mode"]} / {x["count"]} objects' for x in layers]
        choice,ok=QInputDialog.getItem(self,'Count layers','Restoring retains the current layer first',labels,0,False)
        if ok:
            try:self.plate.restore_layer(layers[labels.index(choice)]['id'],self.actor.text(),'Compare earlier layer');self.dirty=True;self.select_plate(self.project.plates.index(self.plate))
            except Exception as e:self.error(e)

    def clear_manual(self):
        if self.plate and self.plate.count_mode=='manual' and QMessageBox.question(self,'Clear manual layer','Retain this observation and start an empty manual layer?')==QMessageBox.StandardButton.Yes:self.manual_mode()

    def quick_click(self,x,y,button,modifiers):
        p=self.plate
        if not p or button!=Qt.MouseButton.LeftButton.value:return
        d=self.nearest(x,y)
        try:
            if modifiers&Qt.KeyboardModifier.ControlModifier.value:
                if d:
                    if d.id in self.pending_ids:self.pending_ids.remove(d.id)
                    else:self.pending_ids.append(d.id)
                    self.redraw()
            elif modifiers&Qt.KeyboardModifier.ShiftModifier.value:
                if d:self.edit_marker('remove',[d.id],'Removed by Quick Count')
            elif p.boundary.contains(x,y) and (not d or math.hypot(d.x-x,d.y-y)>1):
                self.edit_marker('add',[],'Added by Quick Count',[(x,y)])
        except Exception as e:self.error(e)

    def edit_marker(self,action,ids,reason,points=None,category='presumptive'):
        p=self.plate
        if not p:return
        try:
            p.edit(action,ids,self.actor.text(),reason,points,category);self.dirty=True;self.redraw()
        except Exception as e:self.error(e)

    def make_plate_menu(self,x,y):
        menu=QMenu(self.focus_dialog or self)
        menu.addAction('Back to session' if self.focusing else 'Open plate full screen',self.toggle_focus).setEnabled(bool(self.plate))
        menu.addAction('Fit image to window',self.fit_views)
        p=self.plate
        if not p:return menu
        menu.addSeparator();d=self.nearest(x,y)
        if p.boundary.contains(x,y):menu.addAction('Add colony here',lambda:self.edit_marker('add',[],'Added from plate menu',[(x,y)]))
        if d:
            menu.addAction('Remove marker',lambda:self.edit_marker('remove',[d.id],'Removed from plate menu'))
            if not d.included:
                menu.addAction('Include marker',lambda:self.edit_marker('reclassify',[d.id],'Included by analyst',
                               category=d.category if d.category in ('yeast-like','mould-like') else 'presumptive'))
            else:
                menu.addAction('Exclude as ink or artefact',lambda:self.edit_marker('reclassify',[d.id],'Ink or artefact confirmed by analyst',category='artefact'))
            def split():
                self.set_tool('Split');self.pending_ids=[d.id];self.redraw()
                if self.focusing:self.focus_hint.setText('Click the two colony centres that should replace the selected marker.')
            menu.addAction('Split touching colonies',split)
        if p.boundary.contains(x,y):
            menu.addAction('AI Assist this mark…' if d else 'AI Assist this spot…',
                           lambda:self.ai_review_spots([(d.x,d.y,d)] if d else [(x,y,None)])).setEnabled(self.task is None)
        if len(self.pending_ids)>=2:menu.addAction('Merge selected markers',self.apply_merge)
        if self.active_tool!='Inspect':menu.addAction('Finish adjustment',lambda:self.set_tool('Inspect'))
        menu.addAction('Adjust agar ellipse',lambda:self.set_tool('Draw boundary'))
        history=menu.addMenu('Count history')
        history.addAction('Retain current count',self.retain_observation);history.addAction('Compare / restore a count',self.choose_layer)
        history.addAction('Clear manual count',self.clear_manual).setEnabled(p.count_mode=='manual')
        return menu

    def plate_menu(self,x,y,position):
        menu=self.make_plate_menu(x,y);self.plate_popup=menu
        def dismissed():
            if self.plate_popup is menu:self.plate_popup=None
            menu.deleteLater()
        menu.aboutToHide.connect(dismissed);menu.popup(position)

    def ai_review_flagged(self):
        p=self.plate
        if not p:return
        pending=set(self.pending_ids)
        marks=[d for d in p.detections if not d.features.get('rim_excluded') and
               (d.id in pending or (not pending and not d.included))]
        if not marks:
            self.statusBar().showMessage('No flagged marks. Right-click a spot on the plate to review it.')
            return
        self.ai_review_spots([(d.x,d.y,d) for d in marks[:MAX_CROPS]])

    def ai_review_spots(self, spots):
        p=self.plate
        if p is None or self.task or not spots:return
        parent=self.focus_dialog or self
        try:
            rgb=self.rgb[p.image_id]
            medium=self.medium.currentText()
            crops=[make_crop(rgb,p.boundary,x,y,f'A{i+1}',d) for i,(x,y,d) in enumerate(spots[:MAX_CROPS])]
            dlg=SendReview(parent,crops)
            if not dlg.exec():
                dlg.deleteLater();return
            key=dlg.api_key();dlg.key.clear();dlg.deleteLater()
            expected=p.fingerprint()

            def compute(cancel,progress):
                if cancel.is_set():return []
                progress(20,'AI Assist is reviewing the selected crops')
                return review_marks(crops,medium,key)

            def show(suggestions):
                if self.plate is not p or p.fingerprint()!=expected or self.medium.currentText()!=medium:
                    self.error('Plate changed while AI Assist was running. Your count is unchanged.')
                    return
                dlg=ReviewSuggestions(self.focus_dialog or self,crops,suggestions,{d.id:d for d in p.detections})
                if dlg.exec():
                    self.apply_ai_choices(p,crops,suggestions,[c.currentData() for c in dlg.choices])
                dlg.deleteLater()
            self.start_work(compute,show)
        except Exception as e:self.error(e)

    def apply_ai_choices(self,p,crops,suggestions,choices):
        if p is not self.plate:return
        if all(choice=='keep' for choice in choices):return
        try:
            actor=self.actor.text().strip()
            if not actor:raise ValueError('Enter your analyst name to save choices')
            for crop,suggestion,choice in zip(crops,suggestions,choices):
                if choice=='keep':continue
                reason=f'Analyst choice after AI Assist ({AI_MODEL}; suggested {suggestion.appearance})'
                category=suggestion.appearance if suggestion.appearance in ('yeast-like','mould-like') else 'presumptive'
                if choice=='exclude':
                    p.edit('reclassify',[crop.detection_id],actor,reason,category='artefact')
                elif choice=='include' and crop.detection_id:
                    p.edit('reclassify',[crop.detection_id],actor,reason,category=category)
                elif choice=='include':
                    p.edit('add',[],actor,reason,[(crop.x,crop.y)],category)
            self.dirty=True;self.redraw()
        except Exception as e:self.error(e)

    def group_plates(self,plates=None):
        if self.plate:self.apply_meta(silent=True)
        selected=plates if isinstance(plates,list) else self.project.plates
        if selected and GroupDialog(self,selected,self.project,self.library).exec():
            row=self.project.plates.index(self.plate) if self.plate else 0
            self.current_id=None;self.dirty=True;self.refresh_plate_list();self.select_plate(row)

    def input_changed(self,*args):
        if self.loading or self.plate is None:return
        self.plate.invalidate_review();self.dirty=True
        if self.sender() is self.medium:
            settings={**asdict(CounterSettings()),**self.library.database()['media'].get(self.medium.currentText(),{}).get('custom_settings',{})}
            self.loading=True
            for key,w in self.controls.items():w.setValue(settings[key])
            self.load_detector_switches(settings)
            self.loading=False
        profile=self.library.database()['media'].get(self.medium.currentText(),{})
        target=fungal_target(self.fields['test'].text(),profile)
        if (self.medium.currentText()!=self.plate.parameters.get('counted_medium',self.plate.meta.medium)
                or target!=self.plate.quality.get('counted_target','all')):
            self.plate.quality['settings_changed']=True
        self.preview_dilution();self.update_counts()

    def preview_dilution(self):
        try:
            from .calculations import initial_fraction,total_fraction
            override=self.initial_override.text().strip()
            initial=float(override) if override else initial_fraction(self.fields['sample_amount'].value(),self.fields['first_diluent_ml'].value())
            total=total_fraction(initial,self.fields['additional_dilution_factor'].value())
            self.dilution_label.setText(f'Initial dilution: 1 in {1/initial:.6g}\nTotal dilution: 1 in {1/total:.6g}')
        except (ValueError,ZeroDivisionError):self.dilution_label.setText('Check preparation and dilution inputs')

    def setting_changed(self,*args):
        if self.loading or self.plate is None:return
        if self.plate.count_mode=='manual':return
        self.plate.invalidate_review();self.plate.quality['settings_changed']=True;self.dirty=True
        self.update_counts()

    def rim_changed(self,inset):
        p=self.plate
        if self.loading or p is None or p.count_mode=='manual':return
        if (self.task or p.automatic!=p.detections or
            p.parameters.get('algorithm_version')!=ALGORITHM_VERSION or p.quality.get('settings_changed')):
            self.setting_changed();return
        before=p.parameters.get('rim_inset',.13)
        if before==inset:return
        for layer in (p.automatic,p.detections):select_rim(layer,p.boundary,inset)
        p.parameters['rim_inset']=inset
        p._record('rim_filter',self.actor.text().strip() or 'unassigned','Change automatic counting area',before,inset)
        visible=[d for d in p.automatic if not d.features.get('rim_excluded')]
        p.quality['candidate_count']=len(visible);p.quality['appearance_review_count']=sum(not d.included for d in visible)
        p.quality['visible_review_marks']=review_mark_counts(visible)
        p.quality['warnings']=[w for w in p.quality.get('warnings',[]) if not w.startswith('Outer ')]
        if inset:p.quality['warnings'].append(rim_warning(inset))
        self.quality_ok.setChecked(False);self.dirty=True;self.redraw();self.show_quality()

    def mark_tntc(self):
        if self.plate is None:return
        if not enumeration_scope(self.fields['test'].text(),self.medium.currentText()):return
        try:
            if not self.actor.text().strip():raise ValueError('Enter the analyst name before marking TNTC')
            self.observation.setCurrentText('TNTC');self.apply_meta(silent=True)
            self.markers.setChecked(False);self.update_counts()
            self.statusBar().showMessage('TNTC recorded. Check the plate and save the reviewed observation.')
        except Exception as e:self.error(e)

    def new_project(self):
        if self.task:self.error('Wait for or cancel the current task before starting another session');return
        if self.dirty and not self.save_project():return
        self.project=Project();self.rgb={};self.current_id=None;self.plate_list.clear();self.dirty=False
        self.original_view.scene().clear();self.counter_view.scene().clear();self.update_counts();self.refresh_results();self.refresh_imports()

    def add_manual_plate(self):
        if not self.project.images:return
        if self.plate:
            try:self.apply_meta(silent=True)
            except Exception as e:self.error(e);return
            self.active_image_id=self.plate.image_id
        elif not hasattr(self,'active_image_id'):self.active_image_id=next(iter(self.project.images))
        self.current_id=None;self.plate_list.setCurrentRow(-1)
        rgb=self.rgb.get(self.active_image_id)
        if rgb is None:rgb=read_rgb(self.store.image_path(self.project,self.active_image_id));self.rgb[self.active_image_id]=rgb
        self.original_view.image(rgb);self.counter_view.image(rgb);self.set_tool('Draw boundary')
        self.count_label.setText('Drag around the missing agar plate to add a separate record')

    def open_image(self,path=None):
        if not path or isinstance(path,bool):
            paths=QFileDialog.getOpenFileNames(self,'Open plate photographs','','Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp)')[0]
            if paths:self.import_paths(paths)
            return
        if not path:return
        try:
            image_id=self.store.import_image(path,self.project)
            rgb=read_rgb(self.store.image_path(self.project,image_id));self.rgb[image_id]=rgb
            self.active_image_id=image_id
            if any(p.image_id==image_id for p in self.project.plates):raise ValueError('This image is already in the session; select its existing plates')
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:boundaries=detect_plates(rgb)
            finally:QApplication.restoreOverrideCursor()
            test_id=uid()[:12]
            for i,b in enumerate(boundaries,1):self.project.plates.append(Plate(uid(),image_id,b,PlateMeta(test_id=test_id,replicate=str(i),date=timestamp()[:10])))
            self.dirty=True;self.refresh_plate_list()
            if boundaries:self.plate_list.setCurrentRow(len(self.project.plates)-len(boundaries))
            else:
                self.current_id=None;self.original_view.image(rgb);self.counter_view.image(rgb);self.set_tool('Draw boundary')
                self.messages.setText('Draw an ellipse around the agar to select the plate.')
            self.statusBar().showMessage(f'Imported {Path(path).name}: {len(boundaries)} plate boundary proposal(s). Verify each boundary.')
        except Exception as e:self.error(e)

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()

    def dropEvent(self,event):
        paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:self.import_paths(paths);event.acceptProposedAction()

    def paste_image(self):
        clipboard=QApplication.clipboard();mime=clipboard.mimeData()
        if mime.hasUrls():self.import_paths([u.toLocalFile() for u in mime.urls() if u.isLocalFile()])
        elif mime.hasImage():
            path=self.store.root/('clipboard_'+uid()+'.png');clipboard.image().save(str(path));self.import_paths([str(path)])

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_V and event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            self.paste_image();event.accept();return
        super().keyPressEvent(event)

    def start_work(self,fn,callback):
        if self.task:raise ValueError('Another task is running; cancel or wait for it')
        task=Task(fn);self.task=task;self.progress.setRange(0,0);self.progress.setVisible(True);self.cancel_task.setVisible(True)
        task.callback=callback
        task.signals.result.connect(self.commit_work, Qt.ConnectionType.QueuedConnection)
        task.signals.error.connect(self.error, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.work_progress);task.signals.finished.connect(self.work_finished, Qt.ConnectionType.QueuedConnection);self.pool.start(task)

    @Slot(object)
    def commit_work(self,result):
        if self.task and not self.task.cancelled.is_set():
            self.task.callback(result)

    def work_progress(self,value,message):
        if self.focusing and message.startswith('AI Assist'):
            self.focus_hint.setText(message+'…')
        self.progress.setRange(0,100);self.progress.setValue(value);self.statusBar().showMessage(message)

    def cancel_work(self):
        if self.task:self.task.cancelled.set();self.statusBar().showMessage('Cancellation requested; current image operation must finish before the worker stops')

    def work_finished(self):
        if self.focusing and self.focus_hint.text().startswith('AI Assist'):
            self.focus_hint.setText('Wheel: zoom · Middle drag: pan · Counting pointer: click to add, Shift+click to remove · Right-click: plate actions')
        self.task=None;self.progress.setVisible(False);self.cancel_task.setVisible(False)

    def import_paths(self,paths,setup=None,ask_group=True):
        if self.task:self.error('Another task is running');return
        paths=[str(Path(p)) for p in paths];choices=[]
        if not paths:return
        if not self.actor.text().strip():self.error('Enter your analyst name before importing');return
        try:
            if setup is None:
                dialog=ImportSetup(self,self.library,self.preferences.get('import',{}),len(paths))
                if not dialog.exec():return
                choice=dialog.values();choices=[choice]*len(paths)
                if dialog.remember.isChecked():self.save_preference('import',choice)
                if len(paths)>1 and not dialog.all.isChecked():
                    for i,path in enumerate(paths):
                        single=ImportSetup(self,self.library,choice,1);single.setWindowTitle('Configure '+Path(path).name)
                        if not single.exec():return
                        choices[i]=single.values()
            else:choices=[setup]*len(paths)
            project_id=self.project.id
            def prepare(cancel,progress):
                out=[]
                for i,path in enumerate(paths):
                    if cancel.is_set():break
                    try:
                        if Path(path).suffix.lower() not in ('.jpg','.jpeg','.png','.tif','.tiff','.bmp'):raise ValueError('Unsupported image format')
                        record,rgb=self.store.stage_image(path);out.append((record,rgb,detect_plates(rgb),choices[i],None))
                    except Exception as e:out.append((None,None,[],choices[i],Path(path).name+': '+str(e)))
                    progress(round((i+1)*100/len(paths)),f'Prepared image {i+1} of {len(paths)}')
                return out
            def commit(rows):
                if self.project.id!=project_id:return
                added=[];errors=[]
                for record,rgb,boundaries,choice,error in rows:
                    if error:errors.append(error);continue
                    image_id=self.store.commit_image(record,self.project);self.rgb[image_id]=rgb;self.active_image_id=image_id
                    if any(p.image_id==image_id for p in self.project.plates):errors.append(record['original_name']+': already imported; existing plates retained');continue
                    for i,b in enumerate(boundaries,1):
                        p=Plate(uid(),image_id,b,PlateMeta(test=choice['test'],medium=choice['medium'],test_id=uid()[:12],replicate='1',date=timestamp()[:10]));self.project.plates.append(p);added.append(p)
                    if not boundaries:
                        self.original_view.image(rgb);self.counter_view.image(rgb);self.current_id=None;self.set_tool('Draw boundary');self.pending_import_choice=choice
                self.dirty=True;self.refresh_plate_list()
                if added:self.plate_list.setCurrentRow(self.project.plates.index(added[0]))
                if added and ask_group:self.group_plates(added)
                if errors:self.error('\n'.join(errors))
            self.start_work(prepare,commit)
        except Exception as e:self.error(e)

    def refresh_plate_list(self):
        previous=self.current_id;self.plate_list.blockSignals(True);self.plate_list.clear()
        for i,p in enumerate(self.project.plates,1):
            name=self.project.images[p.image_id]['original_name'];summary='TNTC' if p.meta.observation=='TNTC' else f'{p.count:,} colonies · Crowded' if p.quality.get('crowded_review') else f'{p.count:,} colonies' if p.parameters.get('algorithm_version') else 'No count yet'
            self.plate_list.addItem(f'{i}. {p.meta.sample_id or "Unassigned"} / {p.meta.medium}\n{name} / {p.id[:6]}\n{summary}')
            self.plate_list.item(i-1).setToolTip(name)
            if p.image_id in self.rgb:self.plate_list.item(i-1).setIcon(QIcon(GroupDialog.plate_crop(self.rgb[p.image_id],p.boundary,60)))
        if previous:
            for i,p in enumerate(self.project.plates):
                if p.id==previous:self.plate_list.setCurrentRow(i)
        self.plate_list.blockSignals(False)
        self.dashboard.setText(f'{len(self.project.images)} image(s)\n{len(self.project.plates)} plate(s)')

    def select_plate(self,index):
        if index<0 or index>=len(self.project.plates):return
        if self.plate is not None:
            try:self.apply_meta(silent=True)
            except Exception as e:self.error(e);return
        self.current_id=self.project.plates[index].id;self.pending_ids=[];self.split_points=[];self.diagnostic=None
        self.loading=True;p=self.plate;m=p.meta
        self.dataset_role.setCurrentText(p.dataset_role)
        self.mode.setCurrentIndex(self.mode.findData(p.count_mode));self.quick_mode.setChecked(p.count_mode in ('assisted','manual'))
        for k,w in self.fields.items():
            if isinstance(w,QLineEdit):w.setText(str(getattr(m,k)))
            else:w.setValue(getattr(m,k))
        self.initial_override.setText('' if m.initial_fraction_override is None else str(m.initial_fraction_override))
        self.tested.setText('' if m.confirmation_tested is None else str(m.confirmation_tested));self.confirmed.setText('' if m.confirmation_positive is None else str(m.confirmation_positive))
        if self.medium.findText(m.medium)<0:self.medium.addItem(m.medium)
        self.medium.setCurrentText(m.medium);self.method.setCurrentIndex(max(0,self.method.findData(m.method_id)));self.units.setCurrentText(m.original_unit);self.plating.setCurrentText(m.plating);self.observation.setCurrentText(m.observation)
        defaults=asdict(CounterSettings());settings={**defaults,**self.library.database()['media'].get(m.medium,{}).get('custom_settings',{}),**p.parameters}
        for k,w in self.controls.items():w.setValue(settings[k])
        self.load_detector_switches(settings)
        self.boundary_ok.setChecked(p.boundary_reviewed);self.quality_ok.setChecked(p.quality_acknowledged)
        self.loading=False;self.preview_dilution();self.refresh_images();self.show_quality()

    def refresh_images(self,*args):
        p=self.plate
        if p is None:return
        rgb=self.rgb.get(p.image_id)
        if rgb is None:rgb=read_rgb(self.store.image_path(self.project,p.image_id));self.rgb[p.image_id]=rgb
        if self.processed.isChecked() and self.diagnostic is not None:
            rad=max(p.boundary.rx,p.boundary.ry);offset=(max(0,int(p.boundary.cx-rad-2)),max(0,int(p.boundary.cy-rad-2)))
            self.original_view.image(self.diagnostic,offset)
        else:self.original_view.image(rgb)
        self.counter_view.image(rgb);b=p.boundary;rad=max(b.rx,b.ry)
        self.counter_view.setSceneRect(QRectF(b.cx-rad-5,b.cy-rad-5,2*rad+10,2*rad+10));self.counter_view.fit();self.redraw()

    def fit_views(self):self.original_view.fit();self.counter_view.fit()

    def redraw(self,*args):
        if self.plate is None:return
        for view in [self.original_view,self.counter_view]:view.overlay(self.plate,self.numbered.isChecked(),self.pending_ids,self.markers.isChecked(),self.marker_size.value())
        self.update_counts()

    def update_counts(self):
        p=self.plate
        profile=self.library.database()['media'].get(self.medium.currentText(),{})
        self.tntc_button.setEnabled(bool(p) and enumeration_scope(self.fields['test'].text(),self.medium.currentText(),profile))
        self.focus_button.setEnabled(bool(p));self.ai_button.setEnabled(bool(p));self.schedule_results()
        self.update_radius_summary(p,profile)
        if p is None:
            self.count_label.setText('No plate selected');self.record_count.setText('Choose a plate');self.appearance_label.clear();self.mark_summary.hide();self.messages.clear();return
        added=sum(d.included and d.origin=='manual' for d in p.detections)
        included_ids={x.id for x in p.detections if x.included}
        removed=sum(d.included and d.id not in included_ids for d in p.automatic)
        state=self.observation.currentText()
        if state=='TNTC':label='TNTC'
        elif p.parameters.get('algorithm_version'):
            if p.count_mode=='manual':
                label=f'Manual count: {p.count:,}'
            else:
                label=f'Automatic: {p.automatic_count:,}   ·   Current: {p.count:,}'
            if p.quality.get('crowded_review'):label+=' · Crowded plate'
            if added or removed:label+=f' · +{added} added / −{removed} removed'
        else:label='No count yet'
        self.count_label.setText(label)
        visible=[d for d in p.detections if not d.features.get('rim_excluded')]
        types=review_mark_counts(visible)
        portions=[]
        if profile.get('vision',{}).get('fungal') and p.count_mode!='manual':
            yeast=sum(d.included and d.category=='yeast-like' for d in visible)
            mould=sum(d.included and d.category=='mould-like' for d in visible)
            if yeast or mould:portions.append(f'{yeast:,} yeast-like · {mould:,} mould-like (cyan)')
        if types['spread-region'] or types['pinpoint-in-spread']:
            portions.append(f'{types["spread-region"]:,} possible spread areas / {types["pinpoint-in-spread"]:,} pinpoints (violet)')
        if types['broad-edge']:portions.append(f'{types["broad-edge"]:,} marks along growth edges to check')
        artefacts=types['possible-ink']+types['possible-glare']
        if artefacts:portions.append(f'{artefacts:,} possible ink or glare (red)')
        self.mark_summary.setText(' · '.join(portions));self.mark_summary.setVisible(bool(portions))
        if self.focusing:self.focus_count.setText(label)
        self.record_count.setText(state if state not in ('numeric','estimated','zero') else f'{p.count:,} colonies' if p.parameters.get('algorithm_version') else 'No count yet')
        appearance=profile.get('count_description') or profile.get('positive_appearance') or ''
        self.appearance_label.setText(f'{self.fields["test"].text().strip() or "Plate count"} · {self.medium.currentText()}: {appearance}' if appearance else '')
        inset=p.parameters.get('rim_inset',0)
        self.messages.setText('Detection settings changed. Rerun to update the markers.' if p.quality.get('settings_changed') else f'{inset:.0%} of radius excluded from automatic counting.' if inset else '')
        self.refresh_plate_list()

    def show_quality(self):
        if self.plate:self.quality_text.setPlainText(json.dumps(self.plate.quality,indent=2))

    def update_radius_summary(self,p,profile):
        physical=bool(self.use_profile_sizes.isChecked() and profile.get('vision',{}).get('radius_mm'))
        for field in ('min_radius_px','max_radius_px'):self.controls[field].setEnabled(not physical)
        self.controls['dish_diameter_mm'].setEnabled(physical)
        if not p or not physical:self.size_summary.hide();return
        try:
            sizes=radius_guide(profile,p.boundary,self.settings())
            c,m=sizes.compact,sizes.broad
            self.size_summary.setText(f'Radius search: compact {c[0]:.1f}–{c[1]:.1f} px; broad {m[0]:.1f}–{m[1]:.1f} px.')
        except (ValueError,TypeError,KeyError):
            self.size_summary.setText('Check the medium size profile.')
        self.size_summary.show()

    def load_detector_switches(self,settings):
        self.strategy.setCurrentText(settings['strategy']);self.polarity.setCurrentText(settings['polarity'])
        for key,widget in [('split_touching',self.split_touching),('colour_filter',self.colour),
                           ('use_profile_sizes',self.use_profile_sizes),('inspect_spreading',self.inspect_spreading),('screen_marks',self.screen_marks)]:
            widget.setChecked(settings[key])

    def settings(self):
        return CounterSettings(**{k:w.value() for k,w in self.controls.items()},
                               strategy=self.strategy.currentText(),polarity=self.polarity.currentText(),
                               split_touching=self.split_touching.isChecked(),colour_filter=self.colour.isChecked(),
                               use_profile_sizes=self.use_profile_sizes.isChecked(),inspect_spreading=self.inspect_spreading.isChecked(),
                               screen_marks=self.screen_marks.isChecked())

    def read_meta(self):
        values={k:w.text().strip() if isinstance(w,QLineEdit) else w.value() for k,w in self.fields.items()}
        values.update(medium=self.medium.currentText(),method_id=self.method.currentData(),original_unit=self.units.currentText(),plating=self.plating.currentText(),observation=self.observation.currentText(),analyst=self.actor.text().strip(),
                      initial_fraction_override=float(self.initial_override.text()) if self.initial_override.text().strip() else None,
                      confirmation_tested=int(self.tested.text()) if self.tested.text().strip() else None,
                      confirmation_positive=int(self.confirmed.text()) if self.confirmed.text().strip() else None)
        m=PlateMeta(**values);m.validate();return m

    def apply_meta(self,checked=False,silent=False):
        if self.plate is None:return
        m=self.read_meta()
        if self.plate.count_mode=='manual':
            self.plate.parameters['counted_medium']=m.medium;self.plate.quality['settings_changed']=False
        if m.observation=='zero' and self.plate.count!=0:raise ValueError('Zero observation conflicts with included colony markers')
        before=asdict(self.plate.meta)
        if before!=asdict(m):
            self.plate.edits.append({'id':uid(),'time':timestamp(),'action':'metadata','actor':self.actor.text().strip() or 'unassigned','reason':'Plate/preparation record updated','before':before,'after':asdict(m)})
            self.plate.meta=m;self.plate.invalidate_review();self.dirty=True
        self.dilution_label.setText(f'Initial d = {m.initial_fraction:.6g}\nTotal d = {m.total_fraction:.6g}\nTotal inverse DF = {1/m.total_fraction:.6g}\nOriginal amount plated = {m.plated_ml*m.total_fraction:.6g} {m.original_unit}')
        self.schedule_results()
        if not silent:self.statusBar().showMessage('Plate record applied')

    def run_count(self,asynchronous=False):
        if self.plate is None:return
        try:
            if self.task:raise ValueError('Another task is running')
            self.apply_meta(silent=True);p=self.plate;settings=self.settings();settings.validate()
            comparison=False
            if asynchronous and p.parameters.get('algorithm_version'):
                box=QMessageBox(self);box.setWindowTitle('Keep previous counting work');box.setText('Retain the current count before re-running detection.')
                replace=box.addButton('Save revision and rerun',QMessageBox.ButtonRole.AcceptRole)
                compare=box.addButton('New comparison layer',QMessageBox.ButtonRole.ActionRole);box.addButton(QMessageBox.StandardButton.Cancel);box.exec()
                if box.clickedButton() not in (replace,compare):return
                comparison=box.clickedButton()==compare
                p.retain_layer(self.actor.text(),'Before detector rerun');self.store.save(self.project)
            profile=copy.deepcopy(self.library.database()['media'].get(p.meta.medium))
            if not enumeration_scope(p.meta.test,p.meta.medium,profile):raise ValueError('Select a colony enumeration test')
            if not profile:raise ValueError('Select a medium with a counting profile')
            expected=p.fingerprint()
            result_mode='assisted' if self.mode.currentData()=='assisted' else 'automatic'
            rgb=self.rgb[p.image_id];boundary=copy.deepcopy(p.boundary);exclusions=copy.deepcopy(p.exclusions);medium=p.meta.medium;test=p.meta.test
            def compute(cancel,progress):
                progress(10,'Detecting colonies; cancellation takes effect after this plate operation')
                result=count_colonies(rgb,boundary,medium,settings,exclusions,profile_override=profile,keep_rim_candidates=True,test=test)
                progress(100,'Detection complete');return result
            def commit(result):
                try:
                    if p.fingerprint()!=expected or (self.current_id==p.id and (self.settings()!=settings or self.fields['test'].text().strip()!=test or self.medium.currentText()!=medium)):raise ValueError('Plate inputs changed while processing; result discarded. Run again with the current inputs.')
                    detections,quality,diagnostic=result
                    if comparison:
                        temporary=copy.deepcopy(p);temporary.automatic=copy.deepcopy(detections);temporary.detections=detections;temporary.quality=quality
                        temporary.parameters={**asdict(settings),'algorithm_version':ALGORITHM_VERSION,'profile_version':profile.get('local_revision',profiles()['version']),'counted_medium':medium,'profile_snapshot':profile};temporary.count_mode='automatic'
                        temporary.count_layers=[];temporary.retain_layer(self.actor.text(),'Detector comparison')
                        p.count_layers.extend(temporary.count_layers);self.dirty=True;self.statusBar().showMessage('New comparison retained; current markers unchanged. Use Compare / restore layer.');return
                    p.edits.append({'id':uid(),'time':timestamp(),'action':'automatic_detection','actor':self.actor.text().strip() or 'unassigned',
                        'reason':'Automatic detection',
                        'before':{'automatic':[asdict(d) for d in p.automatic],'current':[asdict(d) for d in p.detections],'parameters':p.parameters},
                        'after':{'automatic':[asdict(d) for d in detections],'parameters':asdict(settings)}})
                    p.automatic=copy.deepcopy(detections);p.detections=detections;p._undo.clear();p._redo.clear();p.quality=quality
                    p.count_mode=result_mode
                    p.parameters={**asdict(settings),'algorithm_version':ALGORITHM_VERSION,'profile_version':profile.get('local_revision',profiles()['version']),'counted_medium':medium,'profile_snapshot':profile}
                    p.invalidate_review();p.quality['settings_changed']=False;self.dirty=True
                    if self.current_id==p.id:
                        self.loading=True
                        self.mode.setCurrentIndex(self.mode.findData(result_mode))
                        self.loading=False
                        self.quick_mode.setChecked(result_mode=='assisted')
                        self.diagnostic=diagnostic;self.quality_ok.setChecked(False)
                        if quality.get('crowded_review'):self.numbered.setChecked(False)
                        self.refresh_images();self.show_quality()
                except Exception as e:self.error(e)
            if asynchronous:self.start_work(compute,commit)
            else:commit(compute(None,lambda *a:None))
        except Exception as e:self.error(e)

    def set_tool(self,tool):
        if isinstance(tool,bool):tool='Draw boundary'
        self.active_tool=tool
        for view in [self.original_view,self.counter_view]:view.set_tool(tool)
        self.pending_ids=[];self.split_points=[]
        messages={'Split':'Click the merged marker, then two replacement colony centres.','Merge':'Click markers belonging to one colony, then Apply merge.','Draw boundary':'Drag a new ellipse or use its centre, edge and rotation handles.'}
        self.statusBar().showMessage(messages.get(tool,'Wheel to zoom. Inspect tool drags to pan.'))
        if self.plate:self.redraw()

    def nearest(self,x,y):
        if not self.plate or not self.plate.detections:return None
        return min((d for d in self.plate.detections if not d.features.get('rim_excluded')
                    and math.hypot(d.x-x,d.y-y)<=max(9,min(17,d.radius*2))),
                   key=lambda d:(d.x-x)**2+(d.y-y)**2,default=None)

    def click(self,x,y):
        p=self.plate
        if p is None:return
        action=self.active_tool;actor=self.actor.text();reason=f'{action} by analyst';d=self.nearest(x,y)
        try:
            if action=='Add':p.edit('add',[],actor,reason,[(x,y)],'presumptive')
            elif action=='Remove' and d:p.edit('remove',[d.id],actor,reason)
            elif action=='Reclassify' and d:p.edit('reclassify',[d.id],actor,reason,category='presumptive')
            elif action=='Merge' and d:
                if d.id not in self.pending_ids:self.pending_ids.append(d.id)
            elif action=='Split':
                if not self.pending_ids:
                    if d:self.pending_ids=[d.id]
                else:
                    self.split_points.append((x,y))
                    if len(self.split_points)==2:
                        p.edit('split',self.pending_ids,actor,reason,self.split_points,'presumptive');self.set_tool('Inspect')
                        if self.focusing:self.focus_hint.setText('Counting pointer: click to add, Shift+click to remove · Right-click: plate actions')
            self.dirty=True;self.redraw()
        except Exception as e:self.error(e)

    def apply_merge(self):
        if not self.plate:return
        try:self.plate.edit('merge',self.pending_ids,self.actor.text(),'Duplicate detection',category='presumptive');self.pending_ids=[];self.dirty=True;self.redraw()
        except Exception as e:self.error(e)

    def draw(self,x1,y1,x2,y2):
        try:
            if x2-x1<5 or y2-y1<5:return
            tool=self.active_tool
            if tool=='Draw boundary':
                b=Boundary((x1+x2)/2,(y1+y2)/2,(x2-x1)/2,(y2-y1)/2,source='manual')
                if self.plate:
                    self.commit_boundary(b);return
                else:
                    if not hasattr(self,'active_image_id'):raise ValueError('Open an image first')
                    choice=getattr(self,'pending_import_choice',{'test':'APC','medium':'PCA'})
                    self.project.plates.append(Plate(uid(),self.active_image_id,b,PlateMeta(test_id=uid()[:12],date=timestamp()[:10],test=choice['test'],medium=choice['medium'])))
                    self.refresh_plate_list();self.plate_list.setCurrentRow(len(self.project.plates)-1)
            elif self.plate:
                kind={'Artefact area':'artefact','Spreading area':'spreading','Unsuitable area':'unsuitable'}[tool]
                self.plate.add_exclusion((x1,y1,x2,y2),kind,self.actor.text(),f'Marked {kind} area')
            self.dirty=True;self.refresh_images()
        except Exception as e:self.error(e)

    def edit_boundary(self):
        if not self.plate:return
        b=self.plate.boundary
        text,ok=QInputDialog.getText(self,'Boundary coordinates','cx, cy, radius x, radius y, rotation degrees',text=f'{b.cx:.2f}, {b.cy:.2f}, {b.rx:.2f}, {b.ry:.2f}, {b.angle:.2f}')
        if ok:
            try:
                vals=[float(t.strip()) for t in text.split(',')]
                if len(vals)!=5 or not all(math.isfinite(v) for v in vals) or min(vals[2:4])<=0:raise ValueError('Enter five finite values with positive radii')
                updated=Boundary(*vals,source='manual')
                self.commit_boundary(updated)
            except Exception as e:self.error(e)

    def commit_boundary(self,boundary):
        p=self.plate
        if not p:return
        try:
            p._record('boundary',self.actor.text(),'Adjust agar ellipse',asdict(p.boundary),asdict(boundary))
            p.boundary=boundary;p.boundary_reviewed=False;p.quality['settings_changed']=p.count_mode!='manual';self.boundary_ok.setChecked(False);self.quality_ok.setChecked(False)
            self.dirty=True;self.refresh_images()
        except Exception as e:self.error(e)

    def remove_plate(self):
        if not self.plate:return
        try:
            if not self.actor.text().strip():raise ValueError('Enter the analyst name')
            p=self.plate;p._record('exclude_plate',self.actor.text(),'Marked plate unsuitable',p.id,'excluded')
            # Keep the record as an unusable observation rather than erasing original data.
            self.observation.setCurrentText('unsuitable');self.apply_meta(silent=True);p.invalidate_review();self.dirty=True;self.redraw()
        except Exception as e:self.error(e)

    def history(self,redo):
        if self.plate:
            try:
                fn=self.plate.redo if redo else self.plate.undo
                fn(self.actor.text(),'Undo/redo correction');self.dirty=True;self.redraw()
            except Exception as e:self.error(e)

    def review(self):
        if not self.plate:return
        try:
            self.apply_meta(silent=True)
            if self.plate.quality.get('settings_changed') and self.plate.meta.observation in ('numeric','estimated','zero'):raise ValueError('Detection settings or boundary changed; re-run detection')
            self.plate.boundary_reviewed=self.boundary_ok.isChecked();self.plate.quality_acknowledged=self.quality_ok.isChecked()
            self.plate.mark_reviewed(self.actor.text());self.dirty=True;self.save_project();self.redraw();self.refresh_results()
        except Exception as e:self.error(e)

    def save_project(self):
        try:
            if self.plate:self.apply_meta(silent=True)
            self.project.laboratory=self.lab.text().strip();rev=self.store.save(self.project);self.dirty=False
            self.statusBar().showMessage(f'Saved local session revision {rev}');return True
        except Exception as e:self.error(e);return False

    def open_saved(self):
        if self.task:self.error('Wait for or cancel the active task before switching sessions');return
        records=self.store.list_projects()
        if not records:self.error('No saved local sessions');return
        labels=[f'{r[2][:19]}   {r[0]}   revision {r[1]}' for r in records]
        choice,ok=QInputDialog.getItem(self,'Saved sessions','Choose session',labels,0,False)
        if ok:
            try:
                if self.dirty and not self.save_project():return
                self.project=self.store.load(records[labels.index(choice)][0]);self.rgb={};self.current_id=None;self.refresh_plate_list();self.lab.setText(self.project.laboratory)
                if self.project.plates:self.plate_list.setCurrentRow(0);self.select_plate(0)
                self.refresh_results();self.refresh_imports();self.dirty=False
            except Exception as e:self.error(e)

    def schedule_results(self):
        if hasattr(self,'live_timer') and not self.loading:self.live_timer.start()

    def refresh_results(self):
        p=self.plate;observation=None;current=None;display='—'
        try:
            m=self.read_meta() if p else None
            if p:
                observation=plate_observation(p,m);observation.validate()
            self.calculated=sample_results(self.project,{p.id:m} if p else None)
            if p:
                current=next(r for r in self.calculated if p.id in r['plates'])
                if len(current['plates'])>1:current=sample_results(Project(plates=[p]),{p.id:m})[0]
                display=current['display']
        except (ValueError,ZeroDivisionError) as e:
            self.calculated=[];display=str(e)
        for card in (self.count_result,self.data_result):card.update_result(observation,current['result'] if current else None,display)
        prior=self.results_table.currentRow();self.results_table.blockSignals(True)
        self.results_table.setRowCount(len(self.calculated))
        for i,r in enumerate(self.calculated):
            vals=[r['sample_id'],r['test'],r['display'],r['result']['qualifier'].replace('_',' ').capitalize() if r['result']['value'] is not None else '—']
            for j,v in enumerate(vals):self.results_table.setItem(i,j,QTableWidgetItem(str(v)))
        self.results_table.blockSignals(False)
        if self.calculated:
            row=prior if 0<=prior<len(self.calculated) else 0
            self.results_table.setCurrentCell(row,0);self.result_details(row)
        else:self.calculation_detail.clear()

    def result_details(self,row,*args):
        if hasattr(self,'calculated') and 0<=row<len(self.calculated):
            item=self.calculated[row];r=item['result']
            details=[]
            if r['formula']:details.append('Calculation: '+r['formula'])
            if r['value'] is not None and r['warnings']:details.append(r['warnings'][0])
            self.calculation_detail.setPlainText('\n'.join(details))

    def show_medium(self,key):
        db=profiles();entry=copy.deepcopy(db['media'][key]);entry['source_documents']=[db['sources'][s] for s in entry['sources']]
        self.media_text.setPlainText(json.dumps(entry,indent=2,ensure_ascii=False))

    def export(self,destination=None):
        if not destination or isinstance(destination,bool):destination=QFileDialog.getExistingDirectory(self,'Choose export folder')
        if not destination:return
        try:
            if not self.save_project():return
            dest=Path(destination)/('FoodMicro_export_'+timestamp()[:19].replace(':','-'))
            info=export_bundle(self.project,self.store,dest);self.statusBar().showMessage(f'Exported {info["plates"]} plate records to {dest}')
            return info
        except Exception as e:self.error(e)

    def backup(self):
        parent=QFileDialog.getExistingDirectory(self,'Choose backup parent folder')
        if parent:
            try:
                if not self.save_project():return
                dest=Path(parent)/('FoodMicro_backup_'+timestamp()[:19].replace(':','-'));self.store.backup(dest);self.statusBar().showMessage(f'Backup saved to {dest}')
            except Exception as e:self.error(e)

    def closeEvent(self,event):
        if self.task:self.cancel_work();event.ignore();return
        if self.dirty and not self.save_project():event.ignore();return
        event.accept()


def smoke_test():
    """Exercise the packaged Qt, image, vision, database and spreadsheet stack."""
    from openpyxl import load_workbook

    app = QApplication.instance() or QApplication([])
    demo = ICON_PATH.with_name('synthetic_plates.png')
    with TemporaryDirectory() as directory:
        root = Path(directory)
        window = MainWindow(root / 'records')
        errors = []
        window.error = lambda error: errors.append(str(error))
        window.show()
        app.processEvents()
        try:
            window.actor.setText('Windows smoke test')
            window.open_image(str(demo))
            if errors or len(window.project.plates) != 2:
                raise RuntimeError(f'Expected two demonstration plates: {errors}')
            counts = []
            for i in range(2):
                window.plate_list.setCurrentRow(i)
                window.run_count()
                counts.append(window.plate.count)
            if errors or sorted(counts) != [18, 44]:
                raise RuntimeError(f'Unexpected demonstration counts {counts}: {errors}')
            if not window.save_project():
                raise RuntimeError(f'Could not save smoke session: {errors}')
            reloaded = window.store.load(window.project.id)
            if sorted(plate.count for plate in reloaded.plates) != [18, 44]:
                raise RuntimeError('Saved plate counts did not round-trip')
            destination = root / 'export'
            export_bundle(reloaded, window.store, destination)
            workbook = load_workbook(destination / 'FoodMicro_Count_results.xlsx', read_only=True)
            try:
                if workbook['Plate Observations'].max_row != 3:
                    raise RuntimeError('Workbook is missing exported plates')
            finally:
                workbook.close()
            if len(list((destination / 'annotated').glob('*.png'))) != 2:
                raise RuntimeError('Annotated images are missing')
        finally:
            window.dirty = False
            window.close()
    print(f'FoodMicro Count {__version__}: Qt startup, counting, save/reopen and export passed')


def main():
    parser=argparse.ArgumentParser(description='FoodMicro Count offline desktop')
    default=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'FoodMicroCount'
    parser.add_argument('--data-dir',default=str(default));parser.add_argument('--image')
    parser.add_argument('--smoke-test',action='store_true',help='Check the packaged demo and image loader, then exit')
    args=parser.parse_args()
    if args.smoke_test:smoke_test();return
    app=QApplication(sys.argv[:1]);app.setApplicationName('FoodMicro Count');app.setWindowIcon(QIcon(str(ICON_PATH)))
    window=MainWindow(args.data_dir);window.show()
    if args.image:window.open_image(args.image)
    sys.exit(app.exec())


if __name__=='__main__':main()

