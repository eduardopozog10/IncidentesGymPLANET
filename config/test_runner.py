"""Only the named Gym Planet test database and loopback networking are allowed."""
from contextlib import ExitStack
from ipaddress import ip_address
from tempfile import TemporaryDirectory
from unittest.mock import patch
import socket
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.test.runner import DiscoverRunner


def allow_loopback(host):
    if host is None or str(host).lower().rstrip('.')=='localhost':
        return
    try:
        value=ip_address(host)
        if value.is_loopback or getattr(value,'ipv4_mapped',None) and value.ipv4_mapped.is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError('External network is disabled in automated tests; use a mocked transport.')


class GymPlanetTestRunner(DiscoverRunner):
    def setup_test_environment(self,**kwargs):
        cfg=settings.DATABASES['default']
        if (cfg['ENGINE']!='django.db.backends.postgresql' or cfg['NAME']!='incidentes_gymplanet_db'
            or cfg.get('TEST',{}).get('NAME')!='test_incidentes_gymplanet_db'
            or cfg.get('HOST') not in ('localhost','127.0.0.1','::1') or str(cfg.get('PORT'))!='5432'):
            raise ImproperlyConfigured('Tests require the isolated Gym Planet PostgreSQL configuration on localhost:5432.')
        self.sandbox=ExitStack()
        try:
            folder=self.sandbox.enter_context(TemporaryDirectory(prefix='gymplanet-tests-',ignore_cleanup_errors=True))
            self.sandbox.enter_context(override_settings(REQUEST_PRIVATE_ROOT=folder))
            original_connect,original_connect_ex,original_resolve=socket.socket.connect,socket.socket.connect_ex,socket.getaddrinfo
            def connect(sock,address):
                if isinstance(address,tuple): allow_loopback(address[0])
                return original_connect(sock,address)
            def connect_ex(sock,address):
                if isinstance(address,tuple): allow_loopback(address[0])
                return original_connect_ex(sock,address)
            def resolve(host,*args,**options):
                allow_loopback(host)
                return original_resolve(host,*args,**options)
            self.sandbox.enter_context(patch.object(socket.socket,'connect',connect))
            self.sandbox.enter_context(patch.object(socket.socket,'connect_ex',connect_ex))
            self.sandbox.enter_context(patch.object(socket,'getaddrinfo',resolve))
            return super().setup_test_environment(**kwargs)
        except Exception:
            self.sandbox.close()
            raise

    def teardown_test_environment(self,**kwargs):
        try:
            return super().teardown_test_environment(**kwargs)
        finally:
            self.sandbox.close()
