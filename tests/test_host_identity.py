"""Device labels use platform identity and never reveal its full value."""
import subprocess
import unittest
from unittest import mock

from codex_reset_watch import host_identity


class DeviceLabelTests(unittest.TestCase):
    def test_mac_label_uses_masked_serial_instead_of_network_address(self):
        with mock.patch.object(host_identity.sys, "platform", "darwin"), \
                mock.patch.object(host_identity, "host_name", return_value="Test MacBook Pro"), \
                mock.patch.object(host_identity.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, '"IOPlatformSerialNumber" = "TEST123456"\nAppleSmartBattery')), \
                mock.patch.object(host_identity.uuid, "getnode", return_value=0x020000000001):
            host_identity._mac_machine_kind.cache_clear()
            try:
                self.assertEqual(host_identity.device_label(), "💻 Test MacBook Pro · TEST******")
            finally:
                host_identity._mac_machine_kind.cache_clear()

    def test_unavailable_mac_identity_is_unknown(self):
        for result in (subprocess.CompletedProcess([], 0, ""),
                       subprocess.CompletedProcess([], 1, '"IOPlatformSerialNumber" = "TEST123456"')):
            with self.subTest(result=result), mock.patch.object(host_identity.sys, "platform", "darwin"), \
                    mock.patch.object(host_identity.subprocess, "run", return_value=result):
                self.assertEqual(host_identity.masked_device_code(), "unknown")
