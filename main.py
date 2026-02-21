import sys
from PySide6.QtWidgets import QApplication
from ui.localization.string_loader import load_language
from ui.main_window import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Default language
    load_language("en")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())