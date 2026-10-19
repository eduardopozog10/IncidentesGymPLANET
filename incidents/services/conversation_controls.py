"""Deterministic, account-scoped corrections and recovery around the existing flow."""
import re

from incidents.models import IncidentDraft
from incidents.services.gym_language import failure, plain, priority_choice


def equipment_name(value):
    """Remove a grammatical article only before a lowercase common noun."""
    return re.sub(r"^(?:[Uu]na?|[Ee]l|[Ll]a)\s+(?=[a-záéíóúñ])", "", value.strip())


def replace_equipment(description, old, new):
    """Edit one identified noun phrase, never globally rewrite factual text."""
    old = equipment_name(old or "")
    if not old:
        return description
    pattern = re.compile(r"(?<!\w)(?P<article>(?:una?|el|la)\s+)?" + re.escape(old) + r"(?!\w)", re.I)
    matches = list(pattern.finditer(description or ""))
    if len(matches) != 1:
        return description
    match = matches[0]
    value = new.strip()
    if equipment_name(value) == value:
        value = (match["article"] or "") + value
    elif match.start() == 0:
        value = value[0].upper() + value[1:]
    return description[:match.start()] + value + description[match.end():]


def resummarize(processor, message, draft, context, changes, acknowledgment=None):
    from incidents.services.gymplanet_conversation import finish
    before = dict(draft.extracted_data)
    data = dict(before)
    data.update(changes)
    for key in ("_pending_change", "_target_field", "_pending_messages", "_request_correction", "_request_target"):
        data.pop(key, None)
    context.previous = dict(data)
    context.enrich(data, processor, draft)
    required = ("request_kind", "description", "operational_impact", "priority") if data.get("report_type") == "request" else processor.REQUIRED_FIELDS
    missing = context.missing(data, required)
    reply = context.question(missing, processor.FALLBACK_QUESTIONS.get(missing[0], "Completa el dato pendiente.")) if missing else processor._build_confirmation_reply(data)
    draft.extracted_data, draft.missing_fields, draft.last_question = data, missing, reply
    draft.status = IncidentDraft.Status.COLLECTING if missing else IncidentDraft.Status.READY
    draft.save(update_fields=["extracted_data", "missing_fields", "last_question", "status", "updated_at"])
    labels = {"equipment": "el equipo", "area": "el área", "location_context": "la ubicación", "failure_type": "el síntoma", "description": "la descripción", "operational_impact": "el impacto operacional", "priority": "la prioridad"}
    changed = [key for key in changes if key in labels and data.get(key) and data.get(key) != before.get(key)]
    if changed and not context.catalog_issue:
        key = changed[0]
        value = {"low":"Baja", "medium":"Media", "high":"Alta", "critical":"Crítica"}.get(data[key], data[key])
        notice = acknowledgment or (f"Entendido, cambié el equipo a {equipment_name(value)}." if key == "equipment" else f"Entendido, actualicé {labels[key]}.")
        reply = notice + "\n\n" + reply
    return finish(message, draft=draft, data=data, missing=missing, reply=reply)


def handle_controls(processor, message, draft, context):
    from incidents.services.gymplanet_conversation import finish, report_type
    if draft is None:
        return None
    text, normalized = message.content.strip(), plain(message.content)
    previous = dict(draft.extracted_data)
    if previous.get("_pending_change"):
        if normalized in processor.CONFIRMATION_POSITIVE:
            return resummarize(processor, message, draft, context, previous["_pending_change"])
        if normalized in processor.CONFIRMATION_NEGATIVE:
            return resummarize(processor, message, draft, context, {})
        return finish(message, draft=draft, reply="¿Quieres corregir ese dato? Responde Sí para revisarlo o No para conservar el anterior. Después revisarás el resumen antes de guardar.")
    correcting = bool(re.search(r"\b(?:me equivoque|corrige|corregir|correccion|cantidad correcta|prioridad correcta)\b", normalized))
    previous_kind = previous.get("report_type", "incident")
    switching = re.search(r"\b(?:es una incidencia|es una solicitud|tipo incidencia|tipo solicitud)\b", normalized) and report_type(normalized) != previous_kind
    new_request = previous_kind == "incident" and report_type(normalized) == "request" and re.search(r"\b(?:solicito|necesitamos|nos faltan)\b", normalized)
    if re.search(r"\b(?:nuev[oa] (?:reporte|incidencia|solicitud)|otra (?:incidencia|solicitud))\b", normalized) or ((switching or new_request) and not correcting):
        return finish(message, action="pending_report", draft=draft, reply="Hay un reporte pendiente. Puedes continuar o corregirlo, o usar /cancelar antes de iniciar otro. Conservaré sus datos sin mezclar reportes.")
    if normalized in {"repite", "repite la pregunta", "repetir", "no entendi la pregunta"}:
        return finish(message, draft=draft, reply=draft.last_question or "Cuéntame el dato que quieres completar.")
    if normalized in {"sin asociar un activo", "continuar sin activo", "sin equipo registrado"}:
        return resummarize(processor, message, draft, context, {"_free_asset": True, "gym_asset_id": None})
    target = previous.get("_target_field")
    if target:
        if len(text) > (100 if target == "failure_type" else 150):
            return finish(message, draft=draft, reply="Resume ese dato en una frase más breve; conservé el mensaje completo en el historial.")
        return resummarize(processor, message, draft, context, {target: text})
    if re.fullmatch(r"(?:corrige|corregir|corrigir)(?: el)? (?:sintoma|falla)", normalized):
        draft.extracted_data["_target_field"] = "failure_type"
        draft.status = IncidentDraft.Status.COLLECTING
        draft.last_question = "¿Qué ocurre exactamente cuando intentan utilizar el equipo?"
        draft.save(update_fields=["extracted_data", "status", "last_question", "updated_at"])
        return finish(message, draft=draft, reply=draft.last_question)
    choice = priority_choice(text)
    quantity = re.search(r"(?:me equivoqu[eé](?: en la cantidad)?|corrige la cantidad|la cantidad correcta(?: es)?)[,;:]?\s*(?:necesitamos|necesito|eran?|son)?\s*(\d+)\s+(.+?)(?=\s+y\s+(?:la\s+)?prioridad\b|[,;.!?]|$)", text, re.I)
    if quantity and previous.get("report_type") == "request":
        item = quantity[2].strip()
        pattern = r"\b(?:\d+|un[oa]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+" + re.escape(item) + r"\b"
        replacement, count = re.subn(pattern, lambda _: quantity[1]+" "+item, previous.get("description") or "", flags=re.I)
        if count != 1:
            return finish(message, draft=draft, reply="Indica la descripción completa corregida para evitar cambiar una cantidad equivocada.")
        changes = {"description": replacement}
        notice = "Entendido, actualicé la cantidad de la solicitud."
        if choice:
            changes.update(priority=choice, _priority_explicit=True)
            notice = "Entendido, actualicé la cantidad y la prioridad de la solicitud."
        return resummarize(processor, message, draft, context, changes, acknowledgment=notice)
    if choice and ("prioridad" in normalized or normalized in {"baja", "media", "alta", "critica", "low", "medium", "high", "critical"}):
        return resummarize(processor, message, draft, context, {"priority": choice, "_priority_explicit": True})
    equipment = re.search(r"\bno (?:era|es) .+?[,;]?\s+(?:era|es)\s+(.+)", text, re.I)
    if equipment and not re.match(r"(?:la de|el de|otra|otro)\b", plain(equipment[1])):
        value = equipment[1].strip(" .")
        if re.search(r"\b(?:o|quizas|tal vez)\b", plain(value)):
            return finish(message, draft=draft, reply="Indica un único equipo para corregirlo; conservé el dato anterior.")
        changes = {"equipment": equipment_name(value), "gym_asset_id": None}
        old = previous.get("equipment") or ""
        if old:
            changes["description"] = replace_equipment(previous.get("description") or "", old, value)
        return resummarize(processor, message, draft, context, changes)
    if re.search(r"\bno (?:esta|quedo|se encuentra) fuera de servicio\b", normalized):
        return resummarize(processor, message, draft, context, {"operational_impact": text})
    observed = failure(text)
    if observed and previous.get("failure_type") and plain(previous["failure_type"]) != plain(observed):
        draft.extracted_data["_pending_change"] = {"failure_type": observed}
        draft.status = IncidentDraft.Status.COLLECTING
        draft.save(update_fields=["extracted_data", "status", "updated_at"])
        return finish(message, draft=draft, reply="El síntoma difiere del que habíamos anotado. ¿Quieres corregirlo? Responde Sí o No; después revisarás el resumen.")
    return None
