from typing import Any

import httpx
import re
import time
import logging
from django.conf import settings

from .base import MessagingProvider


class TelegramTokenFilter(logging.Filter):
    def __init__(self,token):
        super().__init__()
        self.token=token

    def filter(self,record):
        message=record.getMessage()
        if self.token and self.token in message:
            record.msg=message.replace(self.token,'[REDACTED]')
            record.args=()
        return True


class TelegramProvider(MessagingProvider):
    provider_name = "telegram"

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 15.0,
    ):
        self.token = (
            token
            or settings.TELEGRAM_BOT_TOKEN
        ).strip()
        self.timeout = timeout

        if not self.token:
            raise ValueError(
                "El token de Telegram no está configurado."
            )

        self.api_url = (
            f"https://api.telegram.org/bot{self.token}"
        )
        redactor=TelegramTokenFilter(self.token)
        for name in ['httpx',*[n for n in logging.Logger.manager.loggerDict if n.startswith('httpcore')]]:
            logger=logging.getLogger(name)
            if not any(isinstance(f,TelegramTokenFilter) and f.token==self.token for f in logger.filters):
                logger.addFilter(redactor)

    def send_text(
        self,
        recipient_id: str,
        text: str,
    ) -> dict[str, Any]:
        recipient_id = str(recipient_id).strip()
        text = text.strip()

        if not recipient_id:
            raise ValueError(
                "El identificador del destinatario es obligatorio."
            )

        if not text:
            raise ValueError(
                "El mensaje de Telegram no puede estar vacío."
            )

        if len(text) > 4096:
            raise ValueError(
                "El mensaje supera el máximo de 4096 caracteres."
            )

        try:
            response = httpx.post(
                f"{self.api_url}/sendMessage",
                json={
                    "chat_id": recipient_id,
                    "text": text,
                },
                timeout=self.timeout,
            )
        except httpx.RequestError:
            raise RuntimeError(
                "No fue posible conectar con Telegram."
            ) from None

        try:
            response_data = response.json()
        except ValueError:
            raise RuntimeError(
                "Telegram devolvió una respuesta inválida."
            ) from None

        if response.status_code >= 400 or not response_data.get("ok"):
            error_description = response_data.get(
                "description",
                "Error desconocido de Telegram.",
            )

            raise RuntimeError(
                f"Telegram rechazó el mensaje: {error_description}"
            )

        return response_data

    def download_file(self, file_id, *, declared_size=None):
        """No caller-provided URLs, redirects, unbounded bodies, or token-bearing errors."""
        limit = min(settings.REQUEST_MAX_FILE_BYTES,5*1024*1024)
        if not isinstance(file_id,str) or not re.fullmatch(r'[\w-]{1,512}',file_id):
            raise ValueError('Identificador de archivo inválido.')
        if declared_size is not None and (not isinstance(declared_size,int) or declared_size <= 0 or declared_size > limit):
            raise ValueError('El archivo supera el límite de 5 MB o está vacío.')
        try:
            with httpx.Client(timeout=self.timeout,follow_redirects=False) as client:
                response = client.post(f'{self.api_url}/getFile',json={'file_id':file_id})
                meta = response.json()
                if response.status_code!=200 or not meta.get('ok'):
                    raise ValueError()
                info = meta['result']
                path = info['file_path']
                size = info.get('file_size',declared_size)
                if (not re.fullmatch(r'[A-Za-z0-9_./-]+',path) or path.startswith('/') or '..' in path.split('/')
                    or (size is not None and (not isinstance(size,int) or size<=0 or size>limit))):
                    raise ValueError()
                start = time.monotonic()
                with client.stream('GET',f'https://api.telegram.org/file/bot{self.token}/{path}') as stream:
                    if stream.status_code!=200 or int(stream.headers.get('Content-Length','0'))>limit:
                        raise ValueError()
                    body=bytearray()
                    for part in stream.iter_bytes(chunk_size=65536):
                        body.extend(part)
                        if len(body)>limit or time.monotonic()-start>20:
                            raise ValueError()
                    if not body or (size is not None and len(body)!=size):
                        raise ValueError()
                    return bytes(body)
        except Exception:
            raise RuntimeError('No se pudo descargar el archivo de Telegram de forma segura; vuelve a enviarlo con un tamaño de hasta 5 MB.') from None
