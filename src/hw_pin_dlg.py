#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Bertrand256
# Created on: 2017-03
from typing import Optional

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QDialog, QLayout, QWidget
from ui import ui_hw_pin_dlg
from wnd_utils import WndUtils


class HardwareWalletPinDlg(QDialog, ui_hw_pin_dlg.Ui_HardwareWalletPinDlg, WndUtils):
    def __init__(self, message, hide_numbers=True, window_title: str = None, max_length=12,
                 button_heights: Optional[int] = None, parent_window: Optional[QWidget] = None,
                 columns: int = 3):
        QDialog.__init__(self)
        ui_hw_pin_dlg.Ui_HardwareWalletPinDlg.__init__(self)
        WndUtils.__init__(self, app_config=None)
        self.pin = ''
        self.message = message
        self.hide_numbers = hide_numbers
        self.window_title = window_title if window_title else 'Hardware wallet PIN'
        self.max_length = max_length
        self.button_heights = button_heights
        self.columns = columns
        if columns not in (2, 3):
            raise Exception('Invalid number of matrix columns')
        self.setupUi(self)

    def new_key(self, new_key):
        self.pin += new_key
        self.edtPin.setText('*' * len(self.pin))
        if self.max_length == 1:
            self.accept()

    def setupUi(self, dialog: QDialog):
        ui_hw_pin_dlg.Ui_HardwareWalletPinDlg.setupUi(self, self)
        styleSheet = """QPushButton {padding: 1px 1px 1 1px; border: 1px solid lightgray;
                          border-radius:5px}
                        QPushButton:enabled {background-color: white}
                        QPushButton:pressed {background-color: rgb(39,123,234); color:white}
                        QPushButton:default {background-color: rgb(39,123,234); color:white}"""
        self.wdgPinButtons.setStyleSheet(styleSheet)

        if self.button_heights:
            self.btnPin1.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin1.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin2.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin2.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin3.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin3.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin4.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin4.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin5.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin5.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin6.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin6.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin7.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin7.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin8.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin8.setMaximumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin9.setMinimumSize(QtCore.QSize(90, self.button_heights))
            self.btnPin9.setMaximumSize(QtCore.QSize(90, self.button_heights))

        self.btnDelete.clicked.connect(self.btnDeleteClick)
        self.btnPin1.clicked.connect(lambda: self.new_key('1'))
        self.btnPin2.clicked.connect(lambda: self.new_key('2'))
        self.btnPin3.clicked.connect(lambda: self.new_key('3'))
        self.btnPin4.clicked.connect(lambda: self.new_key('4'))
        self.btnPin5.clicked.connect(lambda: self.new_key('5'))
        self.btnPin6.clicked.connect(lambda: self.new_key('6'))
        self.btnPin7.clicked.connect(lambda: self.new_key('7'))
        self.btnPin8.clicked.connect(lambda: self.new_key('8'))
        self.btnPin9.clicked.connect(lambda: self.new_key('9'))
        self.btnEnterPin.clicked.connect(self.btnEnterClick)
        star = '\u26ab'
        if self.hide_numbers:
            self.btnPin1.setText(star)
            self.btnPin2.setText(star)
            self.btnPin3.setText(star)
            self.btnPin4.setText(star)
            self.btnPin5.setText(star)
            self.btnPin6.setText(star)
            self.btnPin7.setText(star)
            self.btnPin8.setText(star)
            self.btnPin9.setText(star)
        else:
            self.btnPin1.setText('1')
            self.btnPin2.setText('2')
            self.btnPin3.setText('3')
            self.btnPin4.setText('4')
            self.btnPin5.setText('5')
            self.btnPin6.setText('6')
            self.btnPin7.setText('7')
            self.btnPin8.setText('8')
            self.btnPin9.setText('9')

        if self.columns == 2:
            self.btnPin3.hide()
            self.btnPin6.hide()
            self.btnPin9.hide()
        self.btnDelete.setText('\u232b')
        self.lblMessage.setText(self.message)
        self.setWindowTitle(self.window_title)
        if self.max_length == 1:
            self.btnEnterPin.hide()
            self.edtPin.hide()
            self.btnDelete.hide()
        else:
            self.btnEnterPin.show()
            self.edtPin.show()
            self.btnDelete.show()

    def showEvent(self, _):
        def set():
            self.setFixedSize(self.sizeHint())
        QTimer.singleShot(100, set)

    def btnDeleteClick(self):
        self.pin = self.pin[:-1]
        self.edtPin.setText('*' * len(self.pin))

    def btnEnterClick(self):
        if self.pin:
            if len(self.pin) > 9:
                self.error_msg('The PIN exceeds 9-character limit.')
            else:
                self.accept()
        else:
            self.error_msg('Empty PIN!')
