"""Small, factual Spanish rules shared by Mock and the server. No diagnoses."""
import re
import unicodedata


def plain(text):
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def failure(text):
    text = plain(text)
    rules = (
        (r"\b(?:no (?:estan|esta) funcionando|no funcionan?|dejo de funcionar|dejaron de funcionar|se echaron? a perder)\b", "No funciona"),
        (r"\b(?:mete un ruido raro|ruido raro|ruido inusual)\b", "Ruido inusual"),
        (r"\b(?:funciona a ratos|funciona intermitentemente)\b", "Funcionamiento intermitente"),
        (r"\bno deja pasar(?: a los socios)?\b", "No permite el acceso"),
        (r"\bno (?:esta )?enfriando\b", "No enfría"),
        (r"\b(?:hay una )?fuga de agua\b", "Fuga de agua"),
        (r"\b(?:quedo parado|esta detenido)\b", "Equipo detenido"),
    )
    for pattern, value in rules:
        match = re.search(pattern, text)
        if match and not re.search(r"\b(?:no|nunca)\s+(?:esta|quedo|hay|tiene)?\s*$", text[:match.start()]):
            if not re.search(r"\b(?:quizas|tal vez|podria|parece que)\b", text[:match.end()]):
                return value
    return None


def priority_choice(text):
    text = plain(text)
    if re.search(r"\b(?:quizas|tal vez|podria|no estoy segur[oa]|no se (?:la|que) prioridad)\b", text):
        return None
    choices = {"baja": "low", "media": "medium", "alta": "high", "critica": "critical", "low": "low", "medium": "medium", "high": "high", "critical": "critical"}
    if text.strip(" .!") in choices:
        return choices[text.strip(" .!")]
    pattern = r"\bprioridad\s*(?:(?:correcta|definitiva|elegida)\s*)?(?:(?:es|era|seria)\s*(?:la\s+)?|:\s*)?(baja|media|alta|critica|low|medium|high|critical)\b"
    candidates = []
    for match in re.finditer(pattern, text):
        if re.search(r"\b(?:no|sin)\s+(?:(?:es|era|sea|confirmo|quiero)\s+)?$", text[:match.start()]):
            continue
        if re.search(r"\b(?:o|u)\s+(?:baja|media|alta|critica|low|medium|high|critical)\b", text[match.end():]):
            return None
        candidates.append(choices[match[1]])
    return candidates[0] if len(set(candidates)) == 1 else None


def availability(text):
    original = " ".join(str(text).split())
    text = plain(original)
    if re.search(r"\b(?:quizas|tal vez|podria|parece que)\b", text):
        return None
    match = re.search(r"\b(?:(?:no )?(?:esta|quedo) (?:fuera de servicio|detenid[oa])|(?:no )?se puede (?:utilizar|usar)|(?:no )?hay riesgo)\b", text)
    return original[match.start():match.end()].capitalize() if match else None


def request_intent(text):
    text = plain(text)
    if failure(text) or re.search(r"\b(?:no enciende|se detiene|dejo de funcionar)\b", text):
        return False
    return bool(re.search(r"\b(?:se nos acabo|se acabo|nos faltan|necesitamos mas|necesito mas)\b", text) or
                re.search(r"\b(?:necesito|necesitamos|requiero)\b.*\b(?:mantencion|mantenimiento|servicio|limpieza|reparacion)\b", text))


def explicit_gym_equipment(text):
    """Single literal gym noun phrase, without diagnoses or invented identifiers."""
    normalized = plain(text)
    pattern = r"\b(?:trotadora|bicicleta(?: estatica)?|eliptica|maquina de pecho|polea alta|rack de sentadillas)(?: \d+)?\b"
    matches = list(re.finditer(pattern, normalized))
    if len(matches) != 1 or re.search(r"\b(?:quizas|tal vez|no se si)\b", normalized):
        return None
    return matches[0].group().capitalize()
