import unittest
from scripts import uninstall

class UninstallBackendTests(unittest.TestCase):
    def test_reuses_install_backend_mapping(self):
        self.assertEqual(uninstall.backend_for("Darwin"), "launchd")
        self.assertEqual(uninstall.backend_for("Linux"), "systemd")
        self.assertEqual(uninstall.backend_for("Windows"), "schtasks")

if __name__ == "__main__":
    unittest.main()
