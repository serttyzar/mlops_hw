"""Regression checks for bootstrap commands; no Kubernetes access is needed."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.cluster import Lab, install_metrics_server


class ClusterCommandTests(unittest.TestCase):
    def test_metrics_server_install_uses_kube_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = Lab(Path(tmp))
            captured = []
            def command(args, **kwargs):
                captured.append([str(arg) for arg in args])
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            with patch.object(lab, 'command', side_effect=command):
                install_metrics_server(lab)
            apply = next(args for args in captured if 'apply' in args)
            self.assertEqual(apply[apply.index('-n')+1], 'kube-system')
            self.assertNotIn('hw11', apply)
            self.assertTrue(all(args[args.index('-n')+1]=='kube-system' for args in captured))

    def test_application_commands_keep_hw11_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = Lab(Path(tmp))
            with patch.object(lab, 'command') as command:
                lab.kube('get', 'pods')
            args = command.call_args.args[0]
            self.assertEqual(args[args.index('-n')+1], 'hw11')
