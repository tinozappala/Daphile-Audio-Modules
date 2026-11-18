from pathlib import Path
import os,tempfile,unittest
from boot_logs import trim

class BootLogTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os,'fchown'),'requires native file ownership calls')
    def test_bounds_processing_and_keeps_complete_legacy(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);logs=root/'logs';logs.mkdir();archive=root/'archive'
            original=b'old line\n'*100+b'last line\n'*20
            path=logs/'server.log';path.write_bytes(original)
            self.assertEqual(trim(logs,archive,limit=100),['server.log'])
            self.assertEqual((archive/'legacy-server.log').read_bytes(),original)
            self.assertTrue(path.read_bytes().endswith(b'last line\n'))
            self.assertLessEqual(path.stat().st_size,100)
            self.assertEqual(trim(logs,archive,limit=100),[])

if __name__=='__main__':unittest.main()
