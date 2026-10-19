"""Bounded checks for reporter evidence; no guesses about machines or locations."""
import re

from incidents.services.gym_language import plain


def words(text):
    return set(re.findall(r"\w+", plain(text)))


def ground_locations(data, previous, text, areas):
    known = {"cardio", "musculacion", "peso libre", "camarines", "recepcion", "salas de clases"}
    known.update(plain(value) for area in areas for value in (area.name, area.code))
    pending = previous.get("_pending_messages") or []
    source = words("\n".join([text]+[part for part in pending if isinstance(part,str)]))
    grammar = {"area", "zona", "sala", "salas", "sector", "de", "del", "la", "el", "los", "las"}
    for field in ("area", "location_context", "unit"):
        value = data.get(field)
        if not value or value == previous.get(field):
            continue
        normalized = plain(value)
        if any(re.search(r"\b"+re.escape(term)+r"\b", normalized) for term in known):
            if not (words(value)-grammar).issubset(source):
                data[field] = previous.get(field)


STATE = re.compile(
    r"\b(?:fuera de servicio|fuera de uso|sin uso|aislad[oa]s?|retirad[oa]s? de uso|"
    r"(?:no|nadie) (?:la |lo |se )?puede[n]? (?:usar|utilizar)|"
    r"(?:dejamos|dejo|dejaron) de (?:usar|utilizar)\w*|"
    r"(?:suspendimos|suspendio|suspendieron) (?:su|el) uso|"
    r"(?:retiramos|retiraron|retiro|aislamos|aislaron|aislo|cerramos|cerraron|cerro) "
    r"(?:el |la |ese |esa |este |esta )?(?:equipo|maquina|ducha|zona))\b"
)


def state_clause(text):
    for sentence in re.split(r"(?<=[.!?])\s+|\n", str(text or "")):
        normalized = plain(sentence)
        match = STATE.search(normalized)
        if not match or re.search(
            r"\b(?:quizas|tal vez|podria|no (?:se )?(?:dejamos|retir\w*|aisl\w*|cerr\w*))\b", normalized
        ):
            continue
        return sentence.strip()
    return None


def retain_operational_state(data, text):
    reported = state_clause(text)
    if reported and not state_clause((data.get("description") or "")+". "+(data.get("operational_impact") or "")):
        impact = data.get("operational_impact") or ""
        data["operational_impact"] = reported if plain(impact).strip(" .") in plain(reported) else impact.rstrip(" .")+". "+reported


def request_reason(text):
    return bool(re.search(
        r"\b(?:porque|ya que|debido|se (?:esta )?(?:acabo|agot\w*)|no (?:hay|podemos|alcanza\w*)|"
        r"faltan?|falta de|dificult\w*|impide\w*|deteriorad\w*|rot[oa]s?|"
        r"para (?:que|poder|prevenir|evitar|mantener|completar|cubrir|asegurar|mejorar|cumplir|realizar)|"
        r"para (?:las |los |la |el )?(?:clases|entrenamientos|limpieza|aseo|higiene|operacion)|"
        r"compartimos|insuficient\w*|poc[oa]s?)\b", plain(text)))


def retain_request_materials(data, text):
    pairs = re.findall(r"\b\d+(?:[.,]\d+)?\s+[^\W\d_]+", plain(text))
    description = data.get("description") or ""
    if pairs and not all(pair in plain(description) for pair in pairs):
        data["description"] = (description.rstrip(" .")+". "+text.strip()).lstrip(". ")
