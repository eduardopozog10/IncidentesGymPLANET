"""Private originals are never exposed through MEDIA_URL or storage.url()."""
from pathlib import Path
import os
from uuid import uuid4
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class RequestStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(file_permissions_mode=0o600,
                         directory_permissions_mode=0o700)

    @property
    def base_location(self):
        return str(settings.REQUEST_PRIVATE_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Los documentos requieren una descarga autenticada.")


def request_upload_path(instance, filename):
    return f"originals/{uuid4().hex}{Path(filename).suffix.lower()}"


def private_storage():
    return RequestStorage()
