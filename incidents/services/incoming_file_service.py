"""Verified transport adapter: one file per inbound message, same authorized draft."""
from django.db import transaction
from incidents.models import Technician, TechnicianChannel, ChannelMessage, IncidentDraft, RequestDocument
from incidents.services.incoming_message_service import ChannelNotAuthorizedError
from incidents.services.gymplanet_conversation import GymPlanetContext, finish, normalize, request_kind, generic_branch_reference
from incidents.services.request_items import product_intent
import re
from incidents.services.request_documents import analyze_original, store_original, safe_name, DocumentInputError
from incidents.services.product_conversation import refresh_request
from incidents.services.gym_language import priority_choice
from incidents.services.conversation_evidence import request_justification


class IncomingFileService:
    def __init__(self, processor):
        self.processor = processor

    @transaction.atomic
    def process(self, *, provider, external_user_id, external_chat_id, external_message_id,
                filename, media_type, declared_size, download, caption='', verified_transport=False):
        if not verified_transport:
            raise ChannelNotAuthorizedError('Se requiere transporte verificado.')
        account = TechnicianChannel.objects.select_related('branch','technician').filter(provider=provider,
            external_user_id=str(external_user_id),is_active=True,technician__is_active=True,branch__is_active=True).first()
        if account is None or account.external_chat_id not in ('',str(external_chat_id)):
            raise ChannelNotAuthorizedError('Cuenta no autorizada.')
        Technician.objects.select_for_update().get(pk=account.technician_id)
        # Recheck authorization after waiting for the account's serial lock.
        account.refresh_from_db()
        if not account.is_active or not account.branch.is_active or not account.technician.is_active:
            raise ChannelNotAuthorizedError('Cuenta no autorizada.')
        message,created = ChannelMessage.objects.get_or_create(provider=provider,external_message_id=external_message_id,
            defaults=dict(external_sender_id=str(external_user_id),external_chat_id=str(external_chat_id),
                channel_account=account,technician=account.technician,direction='inbound',message_type='document',content=caption[:3000]))
        if message.channel_account_id != account.pk:
            raise ChannelNotAuthorizedError('Mensaje de otra cuenta.')
        if not created:
            return dict(message=message,duplicate=True,processing=None)
        draft = IncidentDraft.objects.select_for_update().filter(channel_account=account,status__in=['ready','collecting']).order_by('-updated_at').first()
        def response(reply,action='ask_clarification'):
            if draft and action in ('document_rejected','branch_mismatch') and draft.extracted_data.get('report_type')=='request':
                draft.status='collecting'
                draft.last_question=reply
                draft.save(update_fields=['status','last_question','updated_at'])
            return dict(message=message,duplicate=False,processing=finish(message,draft=draft,reply=reply,action=action))
        if draft and draft.extracted_data.get('report_type','incident') != 'request':
            return response('Hay una incidencia pendiente. Usa /cancelar antes de iniciar una solicitud con documento.')
        if draft and (draft.extracted_data.get('branch_id')!=account.branch_id or draft.extracted_data.get('_branch_conflict')):
            return response('Resuelve la discrepancia de sucursal o cancela el borrador antes de enviar el documento.')
        try:
            filename = safe_name(filename)
            if declared_size is not None and (declared_size<=0 or declared_size>5*1024*1024):
                raise DocumentInputError('El archivo debe tener un tamaño de hasta 5 MB y no estar vacío.')
            data = download()
            previous=dict(draft.extracted_data or {}) if draft else {}
            service_text=' '.join(str(previous.get(k) or '') for k in ('request_kind','description'))+' '+caption
            allow_service=bool(not previous.get('_products_required') and re.search(r'\b(?:servicio|mantencion|mantenimiento|reparacion|proveedor externo)\b',normalize(service_text)) and not product_intent(service_text))
            analysis = analyze_original(data,filename,media_type,allow_service=allow_service)
            claimed = analysis['metadata'].get('sucursal')
            if claimed and not generic_branch_reference(claimed) and normalize(claimed) not in (normalize(account.branch.name),normalize(account.branch.code)):
                return response('La sucursal del formulario no coincide con la cuenta autorizada. Corrige el documento; no cambiaré tu sucursal.','branch_mismatch')
            context = GymPlanetContext(caption,draft,branch=account.branch)
            context.kind = 'request'
            if context.branch_issue:
                return response(context.branch_issue,'branch_mismatch')
            if draft and draft.request_documents.count()>=5:
                raise DocumentInputError('El borrador ya tiene cinco originales. Cancela o finaliza antes de enviar más archivos.')
        except DocumentInputError as error:
            return response(str(error)+' El borrador anterior se conserva. Puedes reenviarlo o escribir la lista.','document_rejected')
        except (ValueError,RuntimeError):
            return response('No pude procesar el archivo de forma segura. Revisa formato, tamaño y cantidades; puedes reenviarlo o escribir la lista. El borrador anterior se conserva.','document_rejected')
        if draft is None:
            draft = IncidentDraft.objects.create(technician=account.technician,channel_account=account,initial_message=message)
        document = store_original(message,draft,account.branch,data,analysis)
        draft.request_documents.exclude(pk=document.pk).filter(is_current=True).update(is_current=False)
        values = dict(draft.extracted_data or {})
        values.update(report_type='request',branch_id=account.branch_id,_products_required=not analysis['service'],
            _request_items=analysis['items'],_request_document_id=document.pk,_request_document_name=document.original_name,
            request_kind=values.get('request_kind') or request_kind(caption) or ('Servicio o apoyo externo' if analysis['service'] else 'Materiales e implementos'),
            description=(analysis['service_description'] or previous.get('description') or caption) if analysis['service'] else 'Solicitud de artículos mediante formulario.')
        for key in ('_pending_product_name','_product_target','_description_explicit','_request_correction','_request_target'):
            values.pop(key,None)
        values.pop('_item_issue',None)
        values.pop('_document_review',None)
        if analysis['needs_review']:
            values['_document_review']=True
        selected = priority_choice(caption)
        if selected:
            values.update(priority=selected,_priority_explicit=True)
        if request_justification(caption):
            values['operational_impact']=request_justification(caption)
        elif not values.get('operational_impact'):
            reason=analysis['metadata'].get('motivo') or analysis['metadata'].get('justificacion') or analysis['metadata'].get('justificación')
            if reason:
                values['operational_impact']=reason
        notice = ('Original de la solicitud de servicio guardado. Revisa los datos; las imágenes no se interpretan automáticamente en este flujo.' if analysis['service'] else
                 'Imagen original guardada sin lectura automática. Escribe los artículos y sus cantidades.' if analysis['manual'] else
                  'Leí los artículos seleccionados. La lista del nuevo documento sustituye la anterior; revisa los datos antes de confirmar.')
        result = refresh_request(self.processor,message,draft,context,values,notice)
        return dict(message=message,duplicate=False,processing=result)
