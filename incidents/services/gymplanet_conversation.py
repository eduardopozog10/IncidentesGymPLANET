"""Catalog resolution and request flow; AI output never supplies database IDs."""
import re
import unicodedata
from incidents.services.gym_language import failure, request_intent, priority_choice, availability

from django.utils import timezone

from incidents.models import Branch, BranchArea, ChannelMessage, GymAsset, IncidentDraft, RequestCategory


WELCOME = (
    "Hola. Soy el asistente de incidencias y solicitudes operativas de Gym Planet Chile.\n\n"
    "Tu cuenta determina la sucursal autorizada. Cuéntame qué ocurre o qué necesitas. Puedo registrar fallas "
    "en máquinas e instalaciones, o solicitudes de materiales, implementos y servicios. "
    "Te preguntaré los datos faltantes y la prioridad que tú elijas: Baja, Media, Alta o Crítica. "
    "Antes de guardar revisarás un resumen y deberás confirmarlo. Usa /cancelar para cancelar."
)


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def matches(records, text):
    """Exact codes win; short codes require an explicit label to avoid articles."""
    text = normalize(text)
    if not text:
        return []
    records = list(records)
    exact = [r for r in records if text == normalize(r.code)]
    exact = exact or [r for r in records if text == normalize(r.name)]
    if exact:
        return exact
    coded = [r for r in records if len(normalize(r.code)) >= 3 and re.search(r"\b" + re.escape(normalize(r.code)) + r"\b", text)]
    if coded:
        return coded
    return [r for r in records if any(
        re.search(r"\b" + re.escape(term) + r"\b", text)
        for term in (normalize(r.name), normalize(r.code))
        if len(term) >= 3
    )]


def report_type(text, previous=None):
    text = normalize(text)
    if previous and not re.search(r"\b(?:es una incidencia|tipo incidencia|es una solicitud|tipo solicitud)\b", text):
        return previous
    if failure(text):
        return "incident"
    if request_intent(text):
        return "request"
    if re.search(r"\b(es una incidencia|tipo incidencia|reportar una falla|reportar un incidente)\b", text):
        return "incident"
    if re.search(r"\b(solicit\w*|reponer|reposicion|comprar|compra|requerimiento|nos faltan|faltan implementos|necesito materiales|necesitamos materiales)\b", text):
        return "request"
    if re.search(r"\b(pido|pedimos|necesito|necesitamos|requiero|requerimos)\b", text) and re.search(r"\b(toallas|mancuernas|discos|implementos|materiales|insumos|accesorios|aseo|limpieza)\b", text):
        return "request"
    return previous or "incident"


def request_kind(text):
    text = normalize(text)
    for pattern, kind in (
        (r"\b(?:mantencion|mantenimiento) preventiv[ao]\b", "Mantención preventiva"),
        (r"\b(reponer|reposicion|se nos acabo|se acabo|nos faltan)\b", "Reposición"),
        (r"\b(comprar|compra)\b", "Compra"),
        (r"\b(limpieza|aseo)\b", "Aseo y limpieza"),
        (r"\b(mantenimiento|mantencion)(?: preventiv[ao])?\b", "Mantenimiento"),
        (r"\breparacion\b", "Reparación"),
        (r"\b(implementos|materiales|insumos|accesorios|mancuernas|colchonetas|papel higienico)\b", "Materiales e implementos"),
    ):
        if re.search(pattern, text):
            return kind
    return None


def explicit_operational_impact(text):
    normalized = normalize(text)
    if re.search(r"\b(no|nunca) (?:esta|quedo|se encuentra) fuera de servicio\b", normalized):
        return None
    match = re.search(r"\b(fuera de servicio|no se puede (?:utilizar|usar)|no puede (?:utilizarse|usarse)|no permite entrenar|no esta disponible)\b", normalized)
    return match.group(0).capitalize() if match else None


class GymPlanetContext:
    def __init__(self, text, draft=None, *, branch=None):
        self.text = text
        self.previous = dict(draft.extracted_data or {}) if draft else {}
        self.branch_issue = None
        self.catalog_issue = None
        self.active = list(Branch.objects.filter(is_active=True).order_by("name", "code"))
        self.kind = report_type(text, self.previous.get("report_type"))
        found = matches(Branch.objects.all(), text)
        # An explicit branch label corroborates identity; it never authorizes it.
        selection = re.search(r"\bsucursal\s*:?\s*([^;,\n]+)[;,\n]", text, re.I)
        if selection:
            found = matches(Branch.objects.all(), selection.group(1))
        self.branch = branch
        self.authorized_mention = bool(branch and len(found) == 1 and found[0].pk == branch.pk)
        # Text may corroborate an association but can never grant/change it.
        all_mentions = [item for item in Branch.objects.all() if matches([item], text)]
        if branch is None:
            self.branch_issue = "La cuenta no tiene una sucursal activa autorizada. Contacta al administrador."
        elif any(item.pk != branch.pk for item in all_mentions) or (selection and (len(found) != 1 or found[0].pk != branch.pk)) or ("sucursal" in normalize(text).split() and not all_mentions):
            self.branch_issue = "La sucursal mencionada no coincide con la sucursal autorizada de esta cuenta. No se creará el reporte."
        self.parse_text = text
        if selection:
            self.parse_text = text[:selection.start()] + text[selection.end():]
        elif self.branch and normalize(text) in (normalize(self.branch.name), normalize(self.branch.code)):
            self.parse_text = ""
        self.parse_text = self.parse_text.strip()

    def branch_question(self):
        return "¿A qué sucursal corresponde? Indica nombre o código: " + ", ".join(f"{b.name} ({b.code})" for b in self.active)

    def enrich(self, data, processor, draft=None):
        if self.previous.get("_free_asset"):
            data["_free_asset"] = True
        data["operational_impact"] = data.get("operational_impact") or availability(self.parse_text) or explicit_operational_impact(self.parse_text)
        gym_areas = {"musculacion", "cardio", "peso libre", "camarines", "recepcion", "salas de clases"}
        if not data.get("location_context") and normalize(data.get("area")) in gym_areas:
            data["location_context"] = data["area"]
        if not data.get("area") and normalize(data.get("location_context")) in gym_areas:
            data["area"] = data["location_context"]
        data.update(branch_id=self.branch.pk if self.branch else None,
                    branch=f"{self.branch.name} ({self.branch.code})" if self.branch else None,
                    report_type=self.kind)
        # Only raw, explicit reporter choices confer priority provenance.
        normalized = normalize(self.parse_text)
        waiting = draft and (
            draft.missing_fields == ["priority"]
            or (draft.status == IncidentDraft.Status.COLLECTING and "prioridad" in normalize(draft.last_question)
                and not draft.last_question.startswith("Antes de crear"))
        )
        explicit = priority_choice(self.parse_text)
        # A labelled priority must satisfy the signed-choice rule. The legacy
        # parser only helps with natural replies to the pending priority question.
        if not explicit and "prioridad" not in normalized and waiting and not re.search(
            r"\b(?:quizas|tal vez|podria|parece|no|sin confirmar)\b", normalized
        ):
            explicit = processor._extract_explicit_priority(self.parse_text)
        if explicit:
            data["priority"], data["_priority_explicit"] = explicit, True
        elif "prioridad" in normalized and not processor._is_clarification_request(self.parse_text):
            data["priority"], data["_priority_explicit"] = None, False
        else:
            data["priority"] = self.previous.get("priority") if self.previous.get("_priority_explicit") else None
            data["_priority_explicit"] = bool(data["priority"])
        data.update(branch_area_id=None, gym_asset_id=None, request_category_id=None)
        if not self.branch:
            return data
        # Match only facts supplied in text, or text retained in the draft.
        scoped_areas = BranchArea.objects.filter(branch=self.branch)
        areas = matches(scoped_areas, self.parse_text) or matches(scoped_areas, data.get("area"))
        if len(areas) == 1 and areas[0].is_active:
            data["branch_area_id"] = areas[0].pk
            data["area"] = f"{areas[0].name} ({areas[0].code})"
        elif areas:
            self.catalog_issue = "El área es ambigua o está inactiva. Indica el código de un área activa de esta sucursal."
        elif data.get("area"):
            foreign_areas = matches(BranchArea.objects.exclude(branch=self.branch), data["area"])
            if any(normalize(data["area"]) == normalize(area.code) for area in foreign_areas):
                self.catalog_issue = "Ese código de área pertenece a otra sucursal. Corrige el área o la sucursal."
        scoped = GymAsset.objects.filter(branch=self.branch)
        asset_evidence = self.parse_text if self.kind == "incident" or data.get("equipment") or re.search(r"\b(equipo|activo|maquina)\b", normalize(self.parse_text)) else ""
        assets = [] if data.get("_free_asset") else matches(scoped, asset_evidence) or matches(scoped, data.get("equipment"))
        if len(assets) == 1 and assets[0].is_active:
            asset = assets[0]
            if data.get("branch_area_id") and asset.branch_area_id and data["branch_area_id"] != asset.branch_area_id:
                self.catalog_issue = "El equipo pertenece a otra área. Corrige el área o el equipo antes de confirmar."
            else:
                data["gym_asset_id"] = asset.pk
                data["equipment"] = f"{asset.name} ({asset.code})"
        elif assets:
            self.catalog_issue = "El equipo es ambiguo o está inactivo. Puedes indicar el código o responder «Continuar sin activo». Códigos activos de esta sucursal: " + ", ".join(a.code for a in assets)
        elif data.get("equipment") and not data.get("_free_asset"):
            foreign = matches(GymAsset.objects.exclude(branch=self.branch), data["equipment"])
            if foreign and any(normalize(data["equipment"]) == normalize(a.code) for a in foreign):
                self.catalog_issue = "Ese código de equipo pertenece a otra sucursal. Corrige la sucursal o describe el equipo sin asociarlo."
        if self.kind == "request":
            previous_category = data.get("request_category")
            data["request_category"] = None
            categories = matches(RequestCategory.objects.filter(is_active=True), self.parse_text)
            categories = categories or matches(RequestCategory.objects.filter(is_active=True), previous_category)
            categories = categories or matches(RequestCategory.objects.filter(is_active=True), data.get("request_kind"))
            if len(categories) == 1:
                data["request_category_id"] = categories[0].pk
                data["request_category"] = f"{categories[0].name} ({categories[0].code})"
                data["request_kind"] = data.get("request_kind") or categories[0].name
            elif len(categories) > 1:
                self.catalog_issue = "La categoría de solicitud es ambigua. Indica un código: " + ", ".join(c.code for c in categories)
        return data

    def missing(self, data, fields):
        missing = ([] if self.branch else ["branch"]) + [f for f in fields if not data.get(f)]
        if self.kind == "incident" and (data.get("area") or data.get("location_context")):
            missing = [f for f in missing if f not in ("area", "location_context")]
        if self.catalog_issue:
            missing.insert(0, "catalog")
        return missing

    def question(self, missing, fallback):
        if "branch" in missing:
            return self.branch_issue or self.branch_question()
        if self.catalog_issue:
            return self.catalog_issue
        if missing and missing[0] == "operational_impact" and (fallback or "").startswith("¿Qué impacto operacional") :
            return "¿Qué impacto tiene: sigue disponible para los socios o quedó fuera de servicio? Por ejemplo, pueden seguir entrenando."
        return fallback


def finish(message, *, reply, action="ask_clarification", draft=None, data=None, missing=None):
    message.draft = draft
    message.status = ChannelMessage.Status.PROCESSED
    message.processed_at = timezone.now()
    message.save(update_fields=["draft", "status", "processed_at"])
    return dict(action=action, incident_created=False, draft_id=draft.pk if draft else None,
                extracted_data=data or {}, missing_fields=missing or [], reply=reply)


def process_request(processor, message, draft, context, initial_extraction=None):
    previous = dict(draft.extracted_data or {}) if draft else {}
    previous["_authorized_branch"] = f"{context.branch.name} ({context.branch.code})"
    text = context.parse_text
    normalized = normalize(text)
    if draft and draft.status == IncidentDraft.Status.READY and normalized in processor.CONFIRMATION_POSITIVE:
        return processor._create_incident_from_draft(message, draft)
    questions = {
        "request_kind": "¿Qué tipo de requerimiento necesitas: reposición, compra, mantenimiento u otro?",
        "description": "Describe qué solicitas, con los detalles que conozcas. No necesitas identificar una máquina averiada.",
        "operational_impact": "¿Por qué se necesita o qué impacto tiene lo solicitado en la operación de la sede?",
        "priority": processor.PRIORITY_GUIDANCE,
    }
    correction_aliases = {"descripcion": "description", "impacto": "operational_impact", "justificacion": "operational_impact", "prioridad": "priority", "tipo": "request_kind"}
    if draft and normalized in processor.CONFIRMATION_NEGATIVE:
        previous["_request_correction"] = True
        draft.extracted_data = previous
        draft.status = IncidentDraft.Status.COLLECTING
        draft.last_question = "¿Qué dato quieres corregir: tipo, descripción, impacto o prioridad?"
        draft.save()
        return finish(message, draft=draft, data=previous, reply=draft.last_question)
    if previous.get("_request_correction"):
        target = next((field for alias, field in correction_aliases.items() if alias in normalized.split()), None)
        if target:
            previous["_request_target"] = target
            previous.pop("_request_correction", None)
            draft.extracted_data = previous
            draft.last_question = questions[target]
            draft.save()
            return finish(message, draft=draft, data=previous, reply=draft.last_question)
    data = dict(previous)
    target = previous.get("_request_target")
    waiting = target or (draft.missing_fields[0] if draft and draft.missing_fields else None)
    correction = processor._extract_explicit_field_correction(text)
    corrected = correction and correction[0] in ("description", "operational_impact", "priority")
    direct = waiting in questions and text and not processor._is_clarification_request(text) and normalized not in {"continuar", "si", "no"}
    if corrected:
        data[correction[0]] = correction[1]
        data.pop("_request_target", None)
    elif direct and waiting != "priority" and "prioridad" not in normalized:
        data[waiting] = text
        data.pop("_request_target", None)
    elif text and normalized not in processor.CONFIRMATION_POSITIVE:
        try:
            extraction = initial_extraction or processor.ai_provider.extract_incident(
                message=processor._build_ai_message(current_message=text, pending_messages=processor._get_pending_messages(previous)),
                previous_data=previous,
            )
        except TimeoutError:
            return processor._handle_ai_timeout(message, draft, previous, processor._get_pending_messages(previous))
        except (ValueError, RuntimeError):
            return processor._handle_ai_error(message, draft, previous, processor._get_pending_messages(previous))
        for field in ("area", "location_context", "description", "operational_impact", "request_kind"):
            candidate = getattr(extraction, field, None)
            if candidate and not data.get(field):
                data[field] = candidate
        data.pop(processor.PENDING_MESSAGES_KEY, None)
    data["request_kind"] = data.get("request_kind") or request_kind(text)
    # The original solicitation itself is a factual description, not an invented failure.
    if not data.get("description") and text and report_type(text) == "request":
        data["description"] = text
    context.enrich(data, processor, draft)
    if data.get("_request_target") == "priority" and data.get("_priority_explicit"):
        data.pop("_request_target", None)
    fields = ("request_kind", "description", "operational_impact", "priority")
    missing = context.missing(data, fields)
    reply = context.question(missing, questions.get(next((f for f in missing if f in questions), "priority"))) if missing else processor._build_confirmation_reply(data)
    if draft is None:
        draft = IncidentDraft.objects.create(technician=message.technician, channel_account=message.channel_account, initial_message=message)
    draft.extracted_data, draft.missing_fields, draft.last_question = data, missing, reply
    draft.status = IncidentDraft.Status.COLLECTING if missing else IncidentDraft.Status.READY
    draft.save()
    return finish(message, draft=draft, data=data, missing=missing, reply=reply)
