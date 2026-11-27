import json
import unittest
import urllib.error

from kvm_isolated_boot import read_status


class Response:
    status=200
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*unused):pass
    def read(self,*unused):return self.data


class FakeOpener:
    def __init__(self,rme_code=200):self.rme_code=rme_code
    def open(self,url,timeout):
        if not isinstance(url,str):return Response(b'{"result":{"_version":"9.0.3"}}')
        if 'RMEPlugin' in url and self.rme_code!=200:
            raise urllib.error.HTTPError(url,self.rme_code,'fixture',{},None)
        if url.endswith('/sysinfo.cgi'):return Response(b'ok')
        if url.endswith('/extensions.xml'):
            return Response(b'<extensions><plugins><plugin name="DaphileCamillaDSP" version="0.4.72"/></plugins></extensions>')
        if url.endswith('/api/session'):return Response(b'{"authenticated":true}')
        if 'CamillaPlugin' in url:return Response(b'{"module_version":"0.4.72"}')
        return Response(b'{"status":"available"}')


class KvmStatus(unittest.TestCase):
    def test_missing_rme_is_not_ready(self):
        state=read_status(FakeOpener(404),80,9000)
        self.assertEqual(state['camilla'],'0.4.72');self.assertEqual(state['rme'],'HTTP_404')
    def test_both_authenticated_services_are_ready(self):
        state=read_status(FakeOpener(),80,9000)
        self.assertEqual(state,{'http':200,'lms':'9.0.3','catalog':'0.4.72','camilla':'0.4.72','rme':'READY'})


if __name__=='__main__':unittest.main()
