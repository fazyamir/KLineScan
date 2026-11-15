import sys

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from klinescan import __version__
from klinescan.app_logging import install_crash_handler, setup_logging


class _CrashNotifier(QObject):
    """Created on the GUI thread; emitting from any thread queues the dialog onto it."""

    message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.message.connect(lambda text: QMessageBox.critical(None, "KLineScan error", text))


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("KLineScan")
    app.setApplicationVersion(__version__)

    notifier = _CrashNotifier()
    install_crash_handler(notifier.message.emit)

    from klinescan.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
