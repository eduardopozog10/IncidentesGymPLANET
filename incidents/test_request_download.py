"""Bot API transport boundaries without network access."""
import logging
from unittest.mock import patch
import httpx
from django.test import SimpleTestCase, override_settings
from incidents.messaging.telegram_provider import TelegramProvider, TelegramTokenFilter


@override_settings(REQUEST_MAX_FILE_BYTES=1024)
class RequestDownloadTests(SimpleTestCase):
    def http_client(self,handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_metadata_size_limit_prevents_download(self):
        provider=TelegramProvider(token='synthetic-token')
        with patch('incidents.messaging.telegram_provider.httpx.Client') as client:
            with self.assertRaises(ValueError): provider.download_file('file',declared_size=2048)
            client.assert_not_called()

    def test_valid_file_uses_telegram_only_and_checks_length(self):
        paths=[]
        def handler(request):
            paths.append(request.url.path)
            if request.url.path.endswith('getFile'):
                return httpx.Response(200,json={'ok':True,'result':{'file_path':'documents/file.docx','file_size':3}})
            return httpx.Response(200,content=b'abc')
        client=self.http_client(handler)
        with patch('incidents.messaging.telegram_provider.httpx.Client',return_value=client):
            self.assertEqual(TelegramProvider(token='synthetic-token').download_file('file',declared_size=3),b'abc')
        self.assertEqual(paths,['/botsynthetic-token/getFile','/file/botsynthetic-token/documents/file.docx'])

    def test_arbitrary_paths_redirects_and_stream_overflow_are_rejected(self):
        for path,status,body in [('../elsewhere',200,b'abc'),('https://evil.test',200,b'abc'),('documents/file',302,b'abc'),('documents/file',200,b'x'*1025)]:
            seen=[]
            def handler(request):
                seen.append(str(request.url.host))
                if request.url.path.endswith('getFile'): return httpx.Response(200,json={'ok':True,'result':{'file_path':path}})
                return httpx.Response(status,content=body,headers={'Location':'https://evil.test'})
            client=self.http_client(handler)
            with self.subTest(path=path,status=status),patch('incidents.messaging.telegram_provider.httpx.Client',return_value=client),self.assertRaises(RuntimeError):
                TelegramProvider(token='synthetic-token').download_file('file')
            self.assertTrue(all(host=='api.telegram.org' for host in seen))

    def test_transport_errors_and_logs_hide_token(self):
        token='synthetic-secret-only'
        with patch('incidents.messaging.telegram_provider.httpx.Client',side_effect=httpx.ConnectError('URL '+token)):
            with self.assertRaises(RuntimeError) as error: TelegramProvider(token=token).download_file('file')
        self.assertNotIn(token,str(error.exception))
        record=logging.LogRecord('httpx',logging.INFO,'',0,'GET bot%s/file',(token,),None)
        TelegramTokenFilter(token).filter(record)
        self.assertNotIn(token,record.getMessage())
