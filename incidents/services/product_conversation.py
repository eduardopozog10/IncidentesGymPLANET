"""Structured requests reuse the account-scoped draft and final creation transaction."""
import re
from incidents.models import IncidentDraft
from incidents.services.conversation_evidence import request_reason, request_justification
from incidents.services.gym_language import priority_choice
from incidents.services.gymplanet_conversation import finish, normalize, request_kind
from incidents.services.request_items import change_items, validate_items, display_quantity, make_item, merge_items, NUMBER, ItemInputError


PRODUCT_QUESTION = '¿Qué artículos necesitas y cuántos de cada uno? Puedes indicar varios productos y presentaciones o enviar el formulario de Gym Planet.'
PRIORITY_QUESTION = ('Selecciona la prioridad de la solicitud: Baja (puede esperar), Media (reposición planificada con alternativa), '
    'Alta (la falta afecta significativamente la operación) o Crítica (necesidad inmediata que compromete la continuidad o seguridad). La elección debe ser explícita.')


def contextual_stock_reason(text, draft, data):
    """Resolve references to the known items only while asking their justification."""
    if not (draft and draft.extracted_data.get('report_type') == 'request'
            and data.get('_request_items') and draft.missing_fields
            and draft.missing_fields[0] == 'operational_impact'):
        return False
    key = normalize(text)
    if re.search(r'\b(?:quizas|tal vez|no se|supongo|no necesitamos reponer)\b', key):
        return False
    return bool(re.search(
        r'\b(?:no (?:nos )?queda(?:n)?|(?:estamos )?sin stock|'
        r'no tenemos (?:stock|existencias|articulos|productos|materiales|insumos|de es[oa]s?)|'
        r'se (?:nos )?(?:acabo|acabaron|agotaron)|'
        r'(?:nos )?estamos quedando sin (?:articulos|productos|materiales|insumos)|'
        r'queda(?:n)? muy poc[oa]s?|se (?:estan|esta) (?:terminando|agotando)|'
        r'(?:necesitamos|necesito|es para) reponer|nos faltan (?:estos |esos )?(?:materiales|productos|articulos))\b', key))


def refresh_request(processor, message, draft, context, data, notice=''):
    context.previous = dict(data)
    context.enrich(data, processor, draft)
    missing = context.missing(data, ('request_kind','description','operational_impact','priority'))
    if data.get('_item_issue'):
        missing = ['request_items'] + [m for m in missing if m != 'request_items']
    if data.get('_document_review'):
        missing.insert(0,'document_review')
    if not missing:
        reply = processor._build_confirmation_reply(data)
    elif missing[0] == 'request_items':
        reply = data.get('_item_issue') or PRODUCT_QUESTION
        if not data.get('_item_issue') and not data.get('_request_items') and re.search(r'\bword\b', normalize(context.parse_text)):
            reply = 'Prepararemos una solicitud operativa de artículos. Adjunta el documento Word o indica los artículos y sus cantidades por texto; después revisaremos los datos faltantes.'
    elif missing[0] == 'document_review':
        reply = processor._build_confirmation_reply(data).split('¿Confirmas')[0] + 'Verifica la lectura visual y responde «Revisé el archivo»; después te pediré confirmar la creación.'
    elif missing[0] == 'priority':
        reply = PRIORITY_QUESTION
    else:
        reply = context.question(missing, {'description':PRODUCT_QUESTION,'request_kind':PRODUCT_QUESTION,'operational_impact':'¿Cuál es el motivo o impacto de la solicitud?'}.get(missing[0],PRODUCT_QUESTION))
    if draft is None:
        draft = IncidentDraft.objects.create(technician=message.technician,channel_account=message.channel_account,initial_message=message)
    draft.extracted_data, draft.missing_fields, draft.last_question = data, missing, reply
    draft.status = IncidentDraft.Status.COLLECTING if missing else IncidentDraft.Status.READY
    draft.save()
    return finish(message,draft=draft,data=data,missing=missing,reply=(notice+'\n\n' if notice else '')+reply)


def process_products(processor, message, draft, context):
    data = dict(draft.extracted_data or {}) if draft else {}
    text, key = context.parse_text, normalize(context.parse_text)
    data.update(report_type='request',_products_required=True)
    if draft and re.search(r'\b(?:nuev[oa] (?:reporte|incidencia|solicitud)|otra (?:incidencia|solicitud))\b',key):
        return finish(message,action='pending_report',draft=draft,data=data,reply='Hay una solicitud pendiente. Continúala o usa /cancelar antes de iniciar otro reporte.')
    data['request_kind'] = data.get('request_kind') or request_kind(text) or 'Materiales e implementos'
    notice = ''
    if key in ('descripcion','justificacion','motivo','impacto','prioridad') and draft:
        data['_product_target'] = key
        draft.extracted_data=data
        draft.status='collecting'
        draft.save(update_fields=['extracted_data','status','updated_at'])
        return finish(message,draft=draft,data=data,reply='Indica '+key+' corregida; conservaré los demás datos y volverás a revisar el resumen.')
    if draft and key in processor.CONFIRMATION_POSITIVE:
        if draft.status == 'ready' and not data.get('_document_review'):
            return processor._create_incident_from_draft(message,draft)
        return refresh_request(processor,message,draft,context,data)
    if key in processor.CONFIRMATION_NEGATIVE:
        if draft:
            draft.status = IncidentDraft.Status.COLLECTING
            draft.save(update_fields=['status','updated_at'])
        return finish(message,draft=draft,data=data,reply='Indica qué artículo, cantidad, motivo o prioridad deseas corregir. No guardaré la solicitud hasta una nueva confirmación.')
    if key == 'revise el archivo' and data.get('_document_review'):
        data.pop('_document_review',None)
        notice = 'Lectura revisada. Revisa el resumen final antes de guardar.'
    else:
        try:
            unit_reply = re.fullmatch(r'\s*('+NUMBER+r')\s+(\w+)[.!]?\s*',text,re.I) if data.get('_pending_product_name') else None
            if unit_reply:
                items=merge_items(data.get('_request_items',[]),[make_item(data['_pending_product_name'],unit_reply[1],unit_reply[2])])
                edited=False
                data.pop('_pending_product_name',None)
            else:
                items, edited = change_items(data.get('_request_items',[]),text)
            if items != data.get('_request_items',[]) or edited:
                data['_request_items'] = items
                data.pop('_item_issue',None)
                quantity_edit = edited and re.search(r'\b(?:cantidad|equivoque|cambia|corrige|son|eran)\b',key)
                notice = ('Entendido, actualicé la cantidad.' if quantity_edit else 'Entendido, actualicé la lista de artículos.') if draft else ''
                if data.get('_product_target') != 'descripcion':
                    data.pop('_description_explicit',None)
                if edited and not items:
                    data.pop('_pending_product_name',None)
            elif re.search(r'\b(?:agrega|anade|me falto agregar)\b',key):
                raise ItemInputError('Indica el artículo que deseas agregar con su cantidad y presentación.')
        except ItemInputError as error:
            data['_item_issue'] = str(error)
            if error.pending_name:
                data['_pending_product_name']=error.pending_name
    selected = priority_choice(text)
    if selected:
        data.update(priority=selected,_priority_explicit=True)
        if draft and selected != (draft.extracted_data or {}).get('priority'):
            notice = (notice+' También actualicé la prioridad.') if notice else 'Entendido, actualicé la prioridad.'
    elif 'prioridad' in key and key not in ('prioridad','que prioridad'):
        data.update(priority=None,_priority_explicit=False)
    reason_match = re.search(r'(?:justificaci[oó]n|motivo|impacto)(?: correcto)?\s*:\s*(.+)',text,re.I)
    if data.get('_product_target') in ('justificacion','motivo','impacto'):
        data['operational_impact']=text
        data.pop('_product_target',None)
        notice = 'Entendido, actualicé el motivo.'
    elif contextual_stock_reason(text, draft, data):
        data['operational_impact'] = text
    elif request_justification(text):
        data['operational_impact'] = request_justification(text)
    if data.get('_product_target') == 'descripcion':
        data['description']=text
        data['_description_explicit']=True
        data.pop('_product_target',None)
        notice = 'Entendido, actualicé la descripción.'
    if data.get('_request_items') and not data.get('_description_explicit'):
        # This is a display description; original wording survives in the draft/message and item names.
        data['description'] = 'Solicitud de artículos: ' + '; '.join(f"{display_quantity(r)} {r['original_name']}" for r in data['_request_items'])
    else:
        data['description'] = data.get('description') or text
    return refresh_request(processor,message,draft,context,data,notice)
