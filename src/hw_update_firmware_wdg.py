import binascii
import logging
import os
import re
import ssl
import threading
import time
import urllib, urllib.request, urllib.parse
from enum import Enum
from io import BytesIO
from typing import Callable, Optional, List, Dict

import simplejson
from PyQt5 import QtGui, QtCore
from PyQt5.QtCore import pyqtSlot, QItemSelection, QItemSelectionModel, Qt
from PyQt5.QtWidgets import QWidget, QMessageBox, QTableWidgetItem

import app_defs
import hw_intf
from app_config import AppConfig
from app_defs import get_note_url
from common import CancelException
from hw_common import HWDevice, HWType, HWFirmwareWebLocation
from thread_fun_dlg import CtrlObject
from ui.ui_hw_update_firmware_wdg import Ui_WdgHwUpdateFirmware
from wallet_tools_common import ActionPageBase
from hw_intf import HWDevices
from wnd_utils import WndUtils, ReadOnlyTableCellDelegate


class Step(Enum):
    STEP_SELECT_FIRMWARE_SOURCE = 1
    STEP_PREPARE_FIRMWARE_DATA = 2
    STEP_UPLOADING_FIRMWARE = 3
    STEP_FINISHED_UPDATE = 4


class FirmwareSource(Enum):
    INTERNET = 1
    LOCAL_FILE = 2


class Pages(Enum):
    PAGE_FIRMWARE_SOURCE = 0
    PAGE_PREPARE_FIRMWARE = 1
    PAGE_UPLOAD_FIRMWARE = 2
    PAGE_MESSAGE = 3


class WdgHwUpdateFirmware(QWidget, Ui_WdgHwUpdateFirmware, ActionPageBase):
    def __init__(self, parent, hw_devices: HWDevices):
        QWidget.__init__(self, parent=parent)
        Ui_WdgHwUpdateFirmware.__init__(self)
        ActionPageBase.__init__(self, parent, parent.app_config, hw_devices)

        self.cur_hw_device: Optional[HWDevice] = self.hw_devices.get_selected_device()
        self.current_step: Step = Step.STEP_SELECT_FIRMWARE_SOURCE
        self.hw_firmware_source_type: FirmwareSource = FirmwareSource.INTERNET
        self.hw_firmware_web_sources_all: List[HWFirmwareWebLocation] = []
        # subset of self.hw_firmware_web_sources dedicated to current hardware wallet type:
        self.hw_firmware_web_sources_cur_hw: List[HWFirmwareWebLocation] = []
        self.selected_firmware_source_file: str = ''
        self.selected_firmware_source_web: Optional[HWFirmwareWebLocation] = None
        self.firmware_data: [bytearray] = None
        self.wait_for_bootloader_mode_event: threading.Event = threading.Event()
        self.finishing = False
        self.load_remote_firmware_thread_obj = None
        self.upload_firmware_thread_obj = None
        self.setupUi(self)

    def setupUi(self, dlg):
        Ui_WdgHwUpdateFirmware.setupUi(self, self)
        WndUtils.change_widget_font_attrs(self.lblMessage, point_size_diff=3, bold=True)
        self.tabFirmwareWebSources.horizontalHeader().setVisible(True)
        self.tabFirmwareWebSources.verticalHeader().setDefaultSectionSize(
            self.tabFirmwareWebSources.verticalHeader().fontMetrics().height() + 3)
        self.tabFirmwareWebSources.setItemDelegate(ReadOnlyTableCellDelegate(self.tabFirmwareWebSources))
        self.pages.setCurrentIndex(Pages.PAGE_FIRMWARE_SOURCE.value)

    def initialize(self):
        self.set_title()
        self.set_btn_cancel_visible(True)
        self.set_btn_back_visible(True)
        self.set_btn_back_text('Back')
        self.set_btn_continue_visible(True)
        self.set_btn_cancel_text('Close')
        self.set_hw_panel_visible(True)
        if not self.hw_firmware_web_sources_all:
            self.load_remote_firmware_list()
        self.set_controls_initial_state_for_step(False)
        self.update_ui()
        hw_changed = False
        if not self.cur_hw_device:
            self.hw_devices.select_device(self.parent())
            hw_changed = True
        if self.cur_hw_device and not self.cur_hw_device.hw_client:
            self.hw_devices.open_hw_session(self.cur_hw_device)
            hw_changed = True
        if hw_changed:
            self.update_ui()
            self.display_firmware_list()

    def on_close(self):
        self.finishing = True
        self.wait_for_bootloader_mode_event.set()

    def on_current_hw_device_changed(self, cur_hw_device: HWDevice):
        if cur_hw_device:
            if cur_hw_device.hw_type == HWType.ledger_nano:
                # If the wallet type is not Trezor or Keepkey we can't use this page
                self.cur_hw_device = None
                self.update_ui()
                WndUtils.warn_msg('This feature is not available for Ledger devices.')
            else:
                self.cur_hw_device = self.hw_devices.get_selected_device()
                if not self.cur_hw_device.hw_client:
                    self.hw_devices.open_hw_session(self.cur_hw_device)
                self.update_ui()
                self.display_firmware_list()

    def set_title(self, subtitle: str = None):
        title = 'Update hardware wallet firmware'
        if subtitle:
            title += ' - ' + subtitle
        self.set_action_title(f'<b>{title}</b>')

    def on_btn_continue_clicked(self):
        self.set_next_step()

    def on_btn_back_clicked(self):
        self.set_prev_step()

    def set_next_step(self):
        if self.current_step == Step.STEP_SELECT_FIRMWARE_SOURCE:
            if self.hw_firmware_source_type == FirmwareSource.INTERNET:
                if not self.selected_firmware_source_web:
                    WndUtils.error_msg('No firmware selected.')
                    return
            elif self.hw_firmware_source_type == FirmwareSource.LOCAL_FILE:
                if not os.path.isfile(self.selected_firmware_source_file):
                    WndUtils.error_msg('You must enter the name and path to the firmware file to be uploaded to the '
                                       'device.')
                    return

            self.current_step = Step.STEP_PREPARE_FIRMWARE_DATA
            self.set_controls_initial_state_for_step(False)
            self.update_ui()
            WndUtils.run_thread(self, self.prepare_firmware_upload_thread, ())

        elif self.current_step == Step.STEP_PREPARE_FIRMWARE_DATA:
            # reconnect hardware wallet device to check if it in bootloader mode
            self.hw_devices.open_hw_session(self.cur_hw_device, force_reconnect=True)
            if self.cur_hw_device and self.cur_hw_device.hw_client:
                if self.cur_hw_device.bootloader_mode:
                    if self.upload_firmware_thread_obj is None:
                        self.current_step = Step.STEP_UPLOADING_FIRMWARE
                        self.set_controls_initial_state_for_step(False)
                        self.update_ui()
                        self.upload_firmware_thread_obj = WndUtils.run_thread(self, self.upload_firmware_thread, ())
                    else:
                        logging.error('Thread upload_firmware_thread is already running')
                else:
                    WndUtils.error_msg("Enter your hardware wallet into bootloader mode.")
            else:
                WndUtils.error_msg("Your hardware wallet doesn't seem to be connected.")

        elif self.current_step == Step.STEP_UPLOADING_FIRMWARE:
            self.current_step = Step.STEP_FINISHED_UPDATE
            self.set_controls_initial_state_for_step(False)
            self.update_ui()

    def set_prev_step(self):
        if self.current_step == Step.STEP_SELECT_FIRMWARE_SOURCE:
            self.exit_page()
            return
        elif self.current_step == Step.STEP_PREPARE_FIRMWARE_DATA:
            self.current_step = Step.STEP_SELECT_FIRMWARE_SOURCE
        elif self.current_step == Step.STEP_UPLOADING_FIRMWARE:
            self.current_step = Step.STEP_PREPARE_FIRMWARE_DATA
        elif self.current_step == Step.STEP_FINISHED_UPDATE:
            self.current_step = Step.STEP_PREPARE_FIRMWARE_DATA
        else:
            raise Exception('Invalid step')
        self.set_controls_initial_state_for_step(True)
        self.update_ui()

    def set_controls_initial_state_for_step(self, moving_back: bool):
        """
        Sets the initial state (enable/disable/visible) for the wizard controls (mostly buttons).
        This relates only to the initial state of the step, so it can be changed later by a background thread
        related to the particular step.
        """
        if self.current_step == Step.STEP_SELECT_FIRMWARE_SOURCE:
            self.set_btn_cancel_enabled(True)
            self.set_btn_back_enabled(True)
            self.set_btn_continue_enabled(True)
            self.set_hw_change_enabled(True)
        elif self.current_step == Step.STEP_PREPARE_FIRMWARE_DATA:
            self.set_hw_change_enabled(False)
            if not moving_back:
                self.lblPrepareFirmwareMessage.setText('')
                self.set_btn_cancel_enabled(False)
                self.set_btn_back_enabled(False)
                self.set_btn_continue_enabled(False)
            else:
                self.set_btn_cancel_enabled(True)
                self.set_btn_back_enabled(True)
                self.set_btn_continue_enabled(True)
        elif self.current_step == Step.STEP_UPLOADING_FIRMWARE:
            # we can't really move back to this step, so we are resetting the state
            self.lblUploadFirmwareMessage.setText('')
            self.set_btn_cancel_enabled(False)
            self.set_btn_back_enabled(False)
            self.set_btn_continue_enabled(False)
            self.set_hw_change_enabled(False)
        elif self.current_step == Step.STEP_FINISHED_UPDATE:
            self.set_btn_cancel_enabled(True)
            self.set_btn_back_enabled(True)
            self.set_btn_continue_enabled(False)
            self.set_hw_change_enabled(False)

    @pyqtSlot(bool)
    def on_rbFirmwareSourceInternet_toggled(self, checked):
        if checked:
            self.hw_firmware_source_type = FirmwareSource.INTERNET
            self.update_ui()

    @pyqtSlot(bool)
    def on_rbFirmwareSourceLocalFile_toggled(self, checked):
        if checked:
            self.hw_firmware_source_type = FirmwareSource.LOCAL_FILE
            self.update_ui()

    @pyqtSlot(bool)
    def on_btnChooseFirmwareFile_clicked(self):
        file_name = WndUtils.open_file_query(
            self.parent_dialog,
            self.app_config,
            message='Enter the firmware file name.',
            directory=None,
            filter='All Files (*.*)',
            initial_filter='All Files (*.*)')

        if file_name:
            self.selected_firmware_source_file = file_name
            self.edtFirmwareFilePath.setText(file_name)

    def update_ui(self):
        try:
            if self.cur_hw_device and self.cur_hw_device.hw_client:

                if self.current_step == Step.STEP_SELECT_FIRMWARE_SOURCE:
                    self.set_title('select the firmware source')

                    if not self.cur_hw_device.bootloader_mode:
                        self.lblCurrentFirmwareVersion.setText('Your current firmware version: ' +
                                                               self.cur_hw_device.firmware_version)
                    else:
                        self.lblCurrentFirmwareVersion.setText('Your current bootloader version: ' +
                                                               self.cur_hw_device.firmware_version)

                    self.pages.setCurrentIndex(Pages.PAGE_FIRMWARE_SOURCE.value)
                    if self.hw_firmware_source_type == FirmwareSource.INTERNET:
                        self.lblFileLabel.setVisible(False)
                        self.edtFirmwareFilePath.setVisible(False)
                        self.btnChooseFirmwareFile.setVisible(False)
                        self.tabFirmwareWebSources.setVisible(True)
                        self.edtFirmwareNotes.setVisible(True)
                    elif self.hw_firmware_source_type == FirmwareSource.LOCAL_FILE:
                        self.lblFileLabel.setVisible(True)
                        self.edtFirmwareFilePath.setVisible(True)
                        self.btnChooseFirmwareFile.setVisible(True)
                        self.tabFirmwareWebSources.setVisible(False)
                        self.edtFirmwareNotes.setVisible(False)

                elif self.current_step == Step.STEP_PREPARE_FIRMWARE_DATA:
                    self.set_title('preparing firmware data')
                    self.pages.setCurrentIndex(Pages.PAGE_PREPARE_FIRMWARE.value)

                elif self.current_step == Step.STEP_UPLOADING_FIRMWARE:
                    self.set_title('uploading firmware')
                    self.pages.setCurrentIndex(Pages.PAGE_UPLOAD_FIRMWARE.value)

                elif self.current_step == Step.STEP_FINISHED_UPDATE:
                    self.set_title('update finished')
                    self.pages.setCurrentIndex(Pages.PAGE_MESSAGE.value)
                    self.lblMessage.setText('<b>Firmware update has been completed successfully.<br>Now you can '
                                            'restart your hardware wallet in normal mode.</b>')

            else:
                self.lblMessage.setText('<b>Connect your hardware wallet device to continue</b>')
                self.pages.setCurrentIndex(Pages.PAGE_MESSAGE.value)

        except Exception as e:
            WndUtils.error_msg(str(e), True)

    def load_remote_firmware_list(self):
        if not self.load_remote_firmware_thread_obj:
            self.load_remote_firmware_thread_obj = WndUtils.run_thread(self, self.load_remote_firmware_list_thread, (),
                                                                       on_thread_finish=self.display_firmware_list)
        else:
            logging.warning('Thread load_remote_firmware_list_thread is already running')

    def load_remote_firmware_list_thread(self, ctrl: CtrlObject):
        try:
            self.hw_firmware_web_sources_all.clear()
            self.hw_firmware_web_sources_all = hw_intf.get_hw_firmware_web_sources((HWType.trezor, HWType.keepkey))
        except Exception as e:
            logging.error(str(e))
        finally:
            self.load_remote_firmware_thread_obj = None

    def display_firmware_list(self):
        """Display list of firmwares available for the currently selected hw type."""

        def item(value):
            i = QTableWidgetItem(value)
            i.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            return i

        self.selected_firmware_source_web = None
        self.hw_firmware_web_sources_cur_hw.clear()

        if self.cur_hw_device:
            for f in self.hw_firmware_web_sources_all:
                if f.device == self.cur_hw_device.hw_type and \
                        (self.cur_hw_device.hw_type != HWType.trezor or
                         self.cur_hw_device.device_model == f.model):
                    self.hw_firmware_web_sources_cur_hw.append(f)

        self.tabFirmwareWebSources.setRowCount(len(self.hw_firmware_web_sources_cur_hw))
        for row, f in enumerate(self.hw_firmware_web_sources_cur_hw):
            if f.testnet_support:
                testnet = 'Yes'
            else:
                testnet = 'No'
            if f.official:
                official = 'Yes'
            else:
                official = 'Custom/Unofficial'

            if f.device == HWType.trezor:
                if f.model == '1':
                    model = 'Trezor One'
                else:
                    model = 'Trezor T'
            else:
                model = str(f.model)

            self.tabFirmwareWebSources.setItem(row, 0, item(f.version))
            self.tabFirmwareWebSources.setItem(row, 1, item(model))
            self.tabFirmwareWebSources.setItem(row, 2, item(official))
            self.tabFirmwareWebSources.setItem(row, 3, item(testnet))

        self.tabFirmwareWebSources.resizeColumnsToContents()
        if len(self.hw_firmware_web_sources_cur_hw) > 0:
            self.tabFirmwareWebSources.selectRow(0)
            # self.on_tabFirmwareWebSources_itemSelectionChanged isn't always fired up if there was previously
            # selected row, so we need to force selecting new row:
            self.select_firmware(0)
        else:
            sm = self.tabFirmwareWebSources.selectionModel()
            s = QItemSelection()
            sm.select(s, QItemSelectionModel.Clear | QItemSelectionModel.Rows)
            # force deselect firmware:
            self.select_firmware(-1)

        # max_col_width = 230
        # for idx in range(self.tabFirmwareWebSources.columnCount()):
        #     w = self.tabFirmwareWebSources.columnWidth(idx)
        #     if w > max_col_width:
        #         self.tabFirmwareWebSources.setColumnWidth(idx, max_col_width)

    def on_tabFirmwareWebSources_itemSelectionChanged(self):
        try:
            idx = self.tabFirmwareWebSources.currentIndex()
            row_index = -1
            if idx:
                row_index = idx.row()
            self.select_firmware(row_index)
        except Exception as e:
            WndUtils.error_msg(str(e))

    def select_firmware(self, row_index):
        if row_index >= 0:
            item = self.tabFirmwareWebSources.item(row_index, 0)
            if item:
                idx = self.tabFirmwareWebSources.indexFromItem(item)
                if idx:
                    row = idx.row()
                    if 0 <= row <= len(self.hw_firmware_web_sources_cur_hw):
                        details = ''
                        cfg = self.hw_firmware_web_sources_cur_hw[row]
                        self.selected_firmware_source_web = cfg
                        notes = cfg.notes
                        fingerprint = cfg.fingerprint
                        changelog = cfg.changelog
                        details += f'<b>URL:</b> <a href="{cfg.url}">{cfg.url}</a><br>'
                        if changelog:
                            if details:
                                details += '<br>'
                            details += '<b>Changelog:</b>' + changelog + '<br>'
                        if fingerprint:
                            if details:
                                details += '<br>'
                            details += '<b>Fingerprint:</b> ' + fingerprint + '<br>'
                        if notes:
                            if details:
                                details += '<br>'
                            details += '<b>Notes:</b> '
                            if re.match('\s*http(s)?://', notes, re.IGNORECASE):
                                details += f'<a href={notes}>{notes}</a>'
                            else:
                                details += notes

                        self.edtFirmwareNotes.setText(details)
                        return
        self.selected_firmware_source_web = None
        self.edtFirmwareNotes.clear()

    def prepare_firmware_upload_thread(self, ctrl: CtrlObject):
        """
        Thread that performs the steps of preparing the firmware for upload and making sure that
        the device is in the bootloader mode.
        """
        def add_info(msg: str):
            def append_message_mainth(msg_: str):
                t = self.lblPrepareFirmwareMessage.text()
                t += f'<div>{msg}</div>'
                self.lblPrepareFirmwareMessage.setText(t)
            WndUtils.call_in_main_thread(append_message_mainth, msg)

        def operation_succeeded():
            self.set_btn_continue_enabled(True)
            self.set_btn_back_enabled(True)
            self.set_btn_cancel_enabled(True)

        def operation_failed(message: str):
            WndUtils.error_msg(message)
            self.set_btn_continue_enabled(False)
            self.set_btn_back_enabled(True)
            self.set_btn_cancel_enabled(True)

        try:
            firmware_fingerprint = ''
            if self.hw_firmware_source_type == FirmwareSource.INTERNET:
                if not self.selected_firmware_source_web:
                    raise Exception('Firmware source not available.')

                url = self.selected_firmware_source_web.url
                firmware_fingerprint = self.selected_firmware_source_web.fingerprint
                file_name = os.path.basename(urllib.parse.urlparse(url).path)
                f_, ext_ = os.path.splitext(file_name)
                if f_ and not re.match('.*\d+\.\d+\.\d+.*', f_):
                    # add version string to the name of the file being downloaded
                    file_name = f_ + '-' + self.selected_firmware_source_web.version + ext_
                local_file_path = os.path.join(self.app_config.cache_dir, file_name)

                add_info(f' * Downloading firmware from <a href="{url}">{url}</a>')

                try:
                    response = urllib.request.Request(url, data=None, headers={'User-Agent': app_defs.BROWSER_USER_AGENT})
                    f = urllib.request.urlopen(response)
                    self.firmware_data = f.read()
                except Exception as e:
                    raise Exception('Could not download firmware file ' + url + ': ' + str(e))

                try:
                    add_info(' * Saving firmware to a temp file ' + local_file_path)
                    with open(local_file_path, 'wb') as out_fptr:
                        out_fptr.write(self.firmware_data)
                except Exception as e:
                    pass
            else:
                add_info(f' * Reading firmware from ' + self.selected_firmware_source_file)
                with open(self.selected_firmware_source_file, 'rb') as fptr:
                    self.firmware_data = fptr.read()

            add_info(' * Verifying firmware')

            if self.cur_hw_device.hw_type == HWType.trezor:
                if self.firmware_data[:8] == b'54525a52' or self.firmware_data[:8] == b'54525a56':
                    data = binascii.unhexlify(self.firmware_data)
                else:
                    data = self.firmware_data
                self.cur_hw_device.hw_client.validate_firmware(firmware_fingerprint, data)

            elif self.cur_hw_device.hw_type == HWType.keepkey:
                if self.firmware_data[:8] == b'4b504b59':
                    data = binascii.unhexlify(self.firmware_data)
                else:
                    data = self.firmware_data
                self.cur_hw_device.hw_client.validate_firmware(firmware_fingerprint, data)

            add_info('<br><b>1. Make sure you have your backup seed on hand as some updates may erase data from your '
                     'device.</b><br><br>'
                     '<b>2. Put the device into bootloader mode and press the &lt;Continue&gt; button to upload the '
                     'new firmware.</b>')
            WndUtils.call_in_main_thread(operation_succeeded)

        except Exception as e:
            WndUtils.call_in_main_thread(operation_failed, str(e))

    def upload_firmware_thread(self, ctrl: CtrlObject):
        """
        Thread that performs upload of the selected firmware to the hardware wallet device. The device has to be in
        bootloader mode.
        """
        def add_info(msg: str):
            def append_message_mainth(msg_: str):
                t = self.lblUploadFirmwareMessage.text()
                t += f'<div>{msg}</div>'
                self.lblUploadFirmwareMessage.setText(t)
            WndUtils.call_in_main_thread(append_message_mainth, msg)

        update_ok = False
        try:
            add_info('Uploading new firmware.<br><br><b>Click the confirmation button on your hardware wallet device if '
                     'necessary.</b>')
            if self.cur_hw_device.hw_type == HWType.trezor:
                update_ok = self.cur_hw_device.hw_client.firmware_update(self.selected_firmware_source_web.fingerprint,
                                                                         self.firmware_data)
            elif self.cur_hw_device.hw_type == HWType.keepkey:
                update_ok = self.cur_hw_device.hw_client.firmware_update(BytesIO(self.firmware_data))
            else:
                WndUtils.call_in_main_thread(self.set_prev_step)
                raise Exception('Invalid hardware wallet type')
            if not update_ok:
                WndUtils.error_msg('Operation failed. Look into the log file for details.')
        except CancelException:
            pass
        except Exception as e:
            WndUtils.error_msg('Operation failed with the following error: ' + str(e))
        finally:
            self.upload_firmware_thread_obj = None
            if update_ok:
                WndUtils.call_in_main_thread(self.set_next_step)
            else:
                WndUtils.call_in_main_thread(self.set_prev_step)
