from PySide6.QtWidgets import (
    QMainWindow, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QWidget, QComboBox, QMenuBar, QStatusBar, QMessageBox
)
from PySide6.QtCore import Qt

from ui.localization.string_loader import get_string, load_language


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self._create_widgets()
        self._create_layout()
        self._create_menus()
        self._connect_signals()
        self.retranslate_ui()

    def _create_widgets(self):
        self.heading = QLabel()
        self.heading.setAlignment(Qt.AlignCenter)

        self.paragraph1 = QLabel()
        self.paragraph2 = QLabel()

        self.label_name = QLabel()
        self.input_name = QLineEdit()

        self.label_email = QLabel()
        self.input_email = QLineEdit()

        self.dropdown_label = QLabel()
        self.dropdown = QComboBox()

        self.btn_submit = QPushButton()
        self.btn_clear = QPushButton()
        self.btn_dummy = QPushButton()

        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _create_layout(self):
        layout = QVBoxLayout()

        layout.addWidget(self.heading)
        layout.addWidget(self.paragraph1)
        layout.addWidget(self.paragraph2)
        layout.addWidget(self.label_name)
        layout.addWidget(self.input_name)
        layout.addWidget(self.label_email)
        layout.addWidget(self.input_email)
        layout.addWidget(self.dropdown_label)
        layout.addWidget(self.dropdown)
        layout.addWidget(self.btn_submit)
        layout.addWidget(self.btn_clear)
        layout.addWidget(self.btn_dummy)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def _create_menus(self):
        menubar = self.menuBar()

        self.menu_file = menubar.addMenu("")
        self.menu_settings = menubar.addMenu("")
        self.menu_help = menubar.addMenu("")

        self.action_open = self.menu_file.addAction("")
        self.action_save = self.menu_file.addAction("")
        self.action_exit = self.menu_file.addAction("")

        self.action_switch_en = self.menu_settings.addAction("")
        self.action_switch_hi = self.menu_settings.addAction("")

        self.action_about = self.menu_help.addAction("")

    def _connect_signals(self):
        self.btn_submit.clicked.connect(self.submit_action)
        self.btn_clear.clicked.connect(self.clear_action)
        self.btn_dummy.clicked.connect(self.dummy_action)

        self.action_exit.triggered.connect(self.close)
        self.action_switch_en.triggered.connect(lambda: self.switch_language("en"))
        self.action_switch_hi.triggered.connect(lambda: self.switch_language("hi"))
        self.action_about.triggered.connect(self.show_about)

    def retranslate_ui(self):
        """
        Called whenever language changes.
        Updates ALL UI text dynamically.
        """
        self.setWindowTitle(get_string("window_title"))

        self.heading.setText(get_string("heading_title"))
        self.paragraph1.setText(get_string("paragraph_1"))
        self.paragraph2.setText(get_string("paragraph_2"))

        self.label_name.setText(get_string("label_name"))
        self.label_email.setText(get_string("label_email"))
        self.dropdown_label.setText(get_string("dropdown_label"))

        self.dropdown.clear()
        self.dropdown.addItems([
            get_string("dropdown_option_1"),
            get_string("dropdown_option_2"),
            get_string("dropdown_option_3"),
        ])

        self.btn_submit.setText(get_string("btn_submit"))
        self.btn_clear.setText(get_string("btn_clear"))
        self.btn_dummy.setText(get_string("btn_dummy"))

        self.status.showMessage(get_string("status_ready"))

        self.menu_file.setTitle(get_string("menu_file"))
        self.menu_settings.setTitle(get_string("menu_settings"))
        self.menu_help.setTitle(get_string("menu_help"))

        self.action_open.setText(get_string("menu_open"))
        self.action_save.setText(get_string("menu_save"))
        self.action_exit.setText(get_string("menu_exit"))

        self.action_switch_en.setText(get_string("menu_switch_en"))
        self.action_switch_hi.setText(get_string("menu_switch_hi"))

        self.action_about.setText(get_string("menu_about"))

    def switch_language(self, lang_code):
        load_language(lang_code)
        self.retranslate_ui()

    def submit_action(self):
        self.status.showMessage(get_string("status_submitted"))

    def clear_action(self):
        self.input_name.clear()
        self.input_email.clear()
        self.status.showMessage(get_string("status_ready"))

    def dummy_action(self):
        self.status.showMessage("...")
    
    def show_about(self):
        QMessageBox.information(self, get_string("menu_about"),
                                get_string("about_message"))