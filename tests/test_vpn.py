import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('vpn_manage', Path(__file__).resolve().parents[1] / 'vpn/manage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VPNTests(unittest.TestCase):
    def test_peer_identity_and_address_cannot_collide_or_escape(self):
        existing = {'mac': {'address': '10.77.0.2'}}
        for name, address in [('mac','10.77.0.3'), ('phone','10.77.0.2'), ('../secret','10.77.0.3'), ('phone','10.77.0.1'), ('phone','10.77.0.255'), ('phone','192.168.1.1')]:
            with self.subTest(name=name, address=address), self.assertRaises(ValueError):
                module.validate_peer(name, address, existing)
        module.validate_peer('phone', '10.77.0.3', existing)

    def test_client_routes_only_dashboard_and_server_limits_peer_source(self):
        client = module.client_config('client-secret', 'server-public', 'shared-secret', '10.77.0.2')
        self.assertIn('AllowedIPs = 10.77.0.1/32\n', client)
        self.assertNotIn('0.0.0.0/0', client)
        peers = {'mac': {'address':'10.77.0.2','public_key':'client-public','preshared_key':'shared-secret'}}
        server = module.server_config('server-secret', peers)
        self.assertIn('AllowedIPs = 10.77.0.2/32\n', server)
        self.assertNotIn('client-secret', server)
        self.assertNotIn('[Peer]', module.server_config('server-secret', {}))

    def test_private_files_stay_private_when_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.conf'
            path.write_text('old')
            path.chmod(0o644)
            module.private_write(path, 'new')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), 'new')
