import unittest

import main as agent_cli
import ui.main_window as public_main_window
from ui.window_components.main_window import MainWindow as WindowComponentsMainWindow


class MainWindowFacadeTests(unittest.TestCase):
    def test_public_facade_reexports_window_symbols(self):
        self.assertIs(public_main_window.MainWindow, WindowComponentsMainWindow)
        self.assertIs(agent_cli.MainWindow, WindowComponentsMainWindow)
        self.assertIs(agent_cli.ModelSettingsDialog, public_main_window.ModelSettingsDialog)
        self.assertIs(agent_cli.QFileDialog, public_main_window.QFileDialog)
        self.assertIs(agent_cli.QMessageBox, public_main_window.QMessageBox)


if __name__ == "__main__":
    unittest.main()
