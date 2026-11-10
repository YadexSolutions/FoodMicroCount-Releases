"""Preview outbound crops, then give the analyst control of each suggestion."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from .ai_review import MODEL, SUGGESTIONS


def _plain(text):
    label=QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _thumbnail(crop, size):
    pix=QPixmap()
    pix.loadFromData(crop.png,'PNG')
    label=QLabel()
    label.setPixmap(pix.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class SendReview(QDialog):
    def __init__(self, parent, crops):
        super().__init__(parent)
        self.setWindowTitle('AI Assist — preview crops')
        layout=QVBoxLayout(self)
        layout.addWidget(_plain('The mark at the centre of each crop will be reviewed. Only the images below and the built-in medium name are sent to OpenAI. '
                                'Visible writing inside a crop is part of that image. Send only crops you are allowed to share.'))
        grid=QGridLayout()
        layout.addLayout(grid)
        for i,crop in enumerate(crops):
            box=QVBoxLayout()
            box.addWidget(_thumbnail(crop,136))
            label=_plain(crop.label)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label)
            grid.addLayout(box,i//3,i%3)
        layout.addWidget(_plain(f'Model: {MODEL}. Internet and an OpenAI API key are required. Usage is billed to the API account.'))
        self.key=QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('OpenAI API key (used for this request only)')
        self.env_key=os.environ.get('OPENAI_API_KEY','').strip()
        if not self.env_key:layout.addWidget(self.key)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        send=buttons.addButton('Send selected crops',QDialogButtonBox.ButtonRole.AcceptRole)
        send.setEnabled(bool(self.env_key))
        self.key.textChanged.connect(lambda text:send.setEnabled(bool(text.strip())))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(570, self.sizeHint().height())

    def api_key(self):
        return self.env_key or self.key.text().strip()


class ReviewSuggestions(QDialog):
    def __init__(self, parent, crops, suggestions, detections):
        super().__init__(parent)
        self.setWindowTitle('AI Assist — suggestions')
        layout=QVBoxLayout(self)
        layout.addWidget(_plain('Check the image and choose what to count. Suggestions do not change your count.'))
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        panel=QWidget()
        rows=QVBoxLayout(panel)
        self.choices=[]
        for crop,suggestion in zip(crops,suggestions):
            row=QHBoxLayout()
            rows.addLayout(row)
            row.addWidget(_thumbnail(crop,96))
            form=QVBoxLayout()
            row.addLayout(form,1)
            form.addWidget(_plain(f'{crop.label} · {SUGGESTIONS[suggestion.appearance]}\n{suggestion.reason}'))
            detection=detections.get(crop.detection_id)
            state='Unmarked spot' if detection is None else 'Currently counted' if detection.included else 'Currently held out'
            form.addWidget(_plain(state))
            choice=QComboBox()
            choice.addItem('Leave unchanged','keep')
            if detection is None or not detection.included:choice.addItem('Count this mark','include')
            if detection is not None and detection.included:choice.addItem('Exclude this mark','exclude')
            form.addWidget(choice)
            self.choices.append(choice)
        rows.addStretch()
        scroll.setWidget(panel)
        layout.addWidget(scroll)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        apply=buttons.addButton('Apply my choices',QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setEnabled(False)
        for choice in self.choices:
            choice.currentIndexChanged.connect(lambda _index:apply.setEnabled(any(c.currentIndex() for c in self.choices)))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(610,min(680,160+124*len(crops)))
