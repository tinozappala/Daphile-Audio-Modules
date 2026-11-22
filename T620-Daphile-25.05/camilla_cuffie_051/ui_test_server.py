from pathlib import Path
import json,sys,tempfile
here=Path(__file__).resolve().parent
sys.path.insert(0,str(here/'DaphileCamillaDSP/backend'))
from peq.server import Application,make_server
class IdleAudio:
 enabled=False
 def attach(self,app):self.app=app
 def status(self):return dict(enabled=False,active=False,mode='native-bypass',message='Fixture',recovery_required=False,rate=None,processing=False,dsd_policy='native')
 def set_enabled(self,value):self.enabled=value;return self.status()
with tempfile.TemporaryDirectory(dir=here,prefix='ui-test-') as tmp:
 app=Application(Path(tmp),audio=IdleAudio())
 server=make_server(app,'127.0.0.1',8879)
 print('HEADPHONE_FIXTURE_READY',flush=True)
 try:server.serve_forever(poll_interval=.1)
 finally:server.server_close()
