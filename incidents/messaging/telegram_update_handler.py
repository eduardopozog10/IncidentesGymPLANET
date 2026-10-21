from django.utils import timezone

from incidents.messaging.factory import get_messaging_provider
from incidents.models import ChannelMessage
from incidents.services.incident_processor import IncidentProcessor
from incidents.services.incident_draft_service import (
    IncidentDraftService,
)
from incidents.services.incoming_message_service import (
    ChannelNotAuthorizedError,
    IncomingMessageService,
)


class TelegramUpdateHandler:
    PROVIDER = "telegram"

    PROCESSING_NOTICE = (
        "El procesamiento está tomando un poco más de lo habitual. "
        "Sigo procesando tu mensaje; no es necesario que lo envíes "
        "nuevamente."
    )

    def __init__(
        self,
        messaging_provider=None,
        incoming_service=None,
        draft_service=None,
    ):
        self.messaging_provider = (
            messaging_provider
            or get_messaging_provider(self.PROVIDER)
        )
        self.draft_service = (
            draft_service
            or IncidentDraftService()
        )
        self.incoming_service = (
            incoming_service
            or IncomingMessageService(incident_processor=IncidentProcessor(draft_service=self.draft_service))
        )

    def handle(self, update: dict) -> dict:
        telegram_message = update.get("message")

        if telegram_message is None:
            return {
                "action": "ignored",
                "reason": "La actualización no contiene un mensaje.",
            }

        sender = telegram_message.get("from", {})
        chat = telegram_message.get("chat", {})

        external_user_id = str(sender.get("id", "")).strip()
        external_chat_id = str(chat.get("id", "")).strip()
        # Only private messages from a user are accepted, never group/sender_chat posts.
        if chat.get("type") != "private" or sender.get("is_bot") or telegram_message.get("sender_chat"):
            return {"action": "ignored", "reason": "Se requiere una cuenta en un chat privado."}
        telegram_message_id = telegram_message.get("message_id")
        text = telegram_message.get("text")

        if (
            not external_user_id
            or not external_chat_id
            or telegram_message_id is None
        ):
            return {
                "action": "ignored",
                "reason": "El mensaje no contiene identificadores válidos.",
            }

        if not isinstance(text, str) or not text.strip():
            attachment = telegram_message.get('document')
            photos = telegram_message.get('photo')
            if attachment or photos:
                from incidents.services.incoming_file_service import IncomingFileService
                if not attachment:
                    attachment = max(photos,key=lambda p:p.get('width',0)*p.get('height',0))
                    attachment = dict(attachment,file_name='formulario.jpg',mime_type='image/jpeg')
                try:
                    result = IncomingFileService(self.incoming_service.incident_processor).process(
                        provider=self.PROVIDER,external_user_id=external_user_id,external_chat_id=external_chat_id,
                        external_message_id=f'{external_chat_id}:{telegram_message_id}',filename=attachment.get('file_name','archivo'),
                        media_type=attachment.get('mime_type'),declared_size=attachment.get('file_size'),
                        download=lambda:self.messaging_provider.download_file(attachment.get('file_id'),declared_size=attachment.get('file_size')),
                        caption=telegram_message.get('caption',''),verified_transport=True)
                except ChannelNotAuthorizedError:
                    self.messaging_provider.send_text(external_chat_id,'Tu cuenta no está autorizada para enviar solicitudes.')
                    return {'action':'unauthorized_user'}
                except Exception:
                    self.messaging_provider.send_text(external_chat_id,'No fue posible procesar el documento. Puedes volver a enviarlo o usar /cancelar.')
                    return {'action':'processing_failed'}
                return self._reply_to_result(result,external_chat_id)
            self.messaging_provider.send_text(
                external_chat_id,
                (
                    "Por ahora solo puedo recibir reportes "
                    "escritos en texto."
                ),
            )

            return {
                "action": "unsupported_message",
            }

        external_message_id = (
            f"{external_chat_id}:{telegram_message_id}"
        )

        try:
            result = self.incoming_service.process_text(
                provider=self.PROVIDER,
                external_user_id=external_user_id,
                external_chat_id=external_chat_id,
                external_message_id=external_message_id,
                text=text,
                raw_payload=update,
                verified_transport=True,
            )
        except ChannelNotAuthorizedError:
            self.messaging_provider.send_text(
                external_chat_id,
                (
                    "Tu cuenta de Telegram no está autorizada "
                    "para registrar incidencias y solicitudes."
                ),
            )

            return {
                "action": "unauthorized_user",
            }
        except Exception:
            self.messaging_provider.send_text(
                external_chat_id,
                (
                    "No fue posible procesar el reporte en este "
                    "momento. Inténtalo nuevamente más tarde."
                ),
            )

            return {
                "action": "processing_failed",
            }

        return self._reply_to_result(result,external_chat_id)

    def _reply_to_result(self, result, external_chat_id):
        incoming_message = result["message"]

        if result["duplicate"]:
            return {
                "action": "duplicate_message",
                "message_id": incoming_message.pk,
            }

        processing_result = result["processing"]
        reply_text = processing_result["reply"]

        remaining=reply_text
        chunks=[]
        while len(remaining.encode('utf-16-le'))//2>3900:
            units=0
            limit=0
            for char in remaining:
                units+=2 if ord(char)>0xffff else 1
                if units>3900: break
                limit+=1
            split=remaining.rfind('\n',0,limit)
            if split<1: split=limit
            chunks.append(remaining[:split])
            remaining=remaining[split:].lstrip('\n')
        chunks.append(remaining)
        sent_ids=[]
        try:
            for chunk in chunks:
                telegram_response=self.messaging_provider.send_text(external_chat_id,chunk)
                outgoing_message=self._record_sent_reply(incoming_message=incoming_message,reply_text=chunk,telegram_response=telegram_response)
                sent_ids.append(outgoing_message.pk)
        except Exception as error:
            self._record_failed_reply(
                incoming_message=incoming_message,
                reply_text=reply_text,
                error=error,
            )

            return {
                "action": "reply_failed",
                "message_id": incoming_message.pk,
            }

        return {
            "action": processing_result["action"],
            "incoming_message_id": incoming_message.pk,
            "outgoing_message_id": outgoing_message.pk,
            "outgoing_message_ids":sent_ids,
            "incident_created": processing_result[
                "incident_created"
            ],
        }

    def send_processing_notice(
        self,
        update: dict,
    ) -> dict:
        telegram_message = update.get("message")

        if telegram_message is None:
            return {
                "action": "processing_notice_ignored",
            }

        chat = telegram_message.get("chat", {})

        external_chat_id = str(
            chat.get("id", "")
        ).strip()

        telegram_message_id = telegram_message.get(
            "message_id"
        )

        text = telegram_message.get("text")

        if (
            not external_chat_id
            or telegram_message_id is None
            or not isinstance(text, str)
            or not text.strip()
            or text.strip().split("@", 1)[0].lower() in {"/start", "/cancelar"}
        ):
            return {
                "action": "processing_notice_ignored",
            }

        external_message_id = (
            f"{external_chat_id}:{telegram_message_id}"
        )

        incoming_message = (
            ChannelMessage.objects.select_related(
                "channel_account",
                "technician",
                "incident",
                "draft",
            )
            .filter(
                provider=self.PROVIDER,
                external_message_id=external_message_id,
                direction=ChannelMessage.Direction.INBOUND,
            )
            .first()
        )

        if incoming_message is None:
            return {
                "action": "processing_notice_ignored",
            }

        if incoming_message.status in {
            ChannelMessage.Status.PROCESSED,
            ChannelMessage.Status.FAILED,
        }:
            return {
                "action": "processing_notice_ignored",
            }

        reply_text = self.PROCESSING_NOTICE

        try:
            telegram_response = (
                self.messaging_provider.send_text(
                    external_chat_id,
                    reply_text,
                )
            )
        except Exception as error:
            self._record_failed_reply(
                incoming_message=incoming_message,
                reply_text=reply_text,
                error=error,
            )

            return {
                "action": "processing_notice_failed",
                "message_id": incoming_message.pk,
            }

        outgoing_message = self._record_sent_reply(
            incoming_message=incoming_message,
            reply_text=reply_text,
            telegram_response=telegram_response,
        )

        return {
            "action": "processing_notice_sent",
            "incoming_message_id": incoming_message.pk,
            "outgoing_message_id": outgoing_message.pk,
        }

    def _record_sent_reply(
        self,
        *,
        incoming_message: ChannelMessage,
        reply_text: str,
        telegram_response: dict,
    ) -> ChannelMessage:
        telegram_result = telegram_response["result"]
        telegram_message_id = telegram_result["message_id"]

        external_message_id = (
            f"{incoming_message.external_chat_id}:"
            f"{telegram_message_id}"
        )

        return ChannelMessage.objects.create(
            provider=self.PROVIDER,
            external_message_id=external_message_id,
            external_sender_id=str(
                telegram_result.get("from", {}).get(
                    "id",
                    "bot",
                )
            ),
            external_chat_id=(
                incoming_message.external_chat_id
            ),
            channel_account=(
                incoming_message.channel_account
            ),
            technician=incoming_message.technician,
            incident=incoming_message.incident,
            draft=incoming_message.draft,
            direction=ChannelMessage.Direction.OUTBOUND,
            message_type=ChannelMessage.MessageType.TEXT,
            content=reply_text,
            raw_payload=telegram_response,
            status=ChannelMessage.Status.SENT,
            processed_at=timezone.now(),
        )

    def _record_failed_reply(
        self,
        *,
        incoming_message: ChannelMessage,
        reply_text: str,
        error: Exception,
    ) -> ChannelMessage:
        return ChannelMessage.objects.create(
            provider=self.PROVIDER,
            external_message_id=None,
            external_sender_id="bot",
            external_chat_id=(
                incoming_message.external_chat_id
            ),
            channel_account=(
                incoming_message.channel_account
            ),
            technician=incoming_message.technician,
            incident=incoming_message.incident,
            draft=incoming_message.draft,
            direction=ChannelMessage.Direction.OUTBOUND,
            message_type=ChannelMessage.MessageType.TEXT,
            content=reply_text,
            raw_payload={},
            status=ChannelMessage.Status.FAILED,
            error_message=str(error),
            processed_at=timezone.now(),
        )
