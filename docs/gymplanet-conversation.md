# Flujo conversacional de Gym Planet

## Catálogo y sucursal

Todo reporte nuevo usa la sucursal activa asociada a la identidad corporativa
verificada del remitente. No se pregunta ni se asigna desde texto, IA o IDs HTTP.
La cuenta sin asociación autorizada no puede reportar. Una mención de otra sede
bloquea la creación y exige resolver la discrepancia antes de confirmar.

Los nombres y códigos se comparan sin distinguir tildes o mayúsculas únicamente
para comprobar coherencia con la asociación, nunca para cambiarla. No se crean
sucursales desde conversaciones ni se ejecuta su carga inicial automáticamente.

La vinculación manual, el aislamiento de borradores, la política de acceso y el
estado de migraciones se detallan en [cuentas corporativas y seguridad](corporate-accounts-security.md).

## Incidencias y solicitudes

Una incidencia recopila sucursal, área **o** ubicación, equipo o instalación,
síntoma, descripción, impacto y prioridad explícita. Las referencias a áreas
y equipos del inventario son opcionales: se resuelven solo dentro de la sede
elegida y se muestran como propuestas en el resumen final.

Una solicitud recopila sucursal, tipo o categoría, descripción de lo solicitado,
justificación o impacto y prioridad explícita. Área y equipo son opcionales.
Solicitar mancuernas o insumos no obliga a seleccionar una máquina averiada,
a inventar un síntoma ni a elegir un activo entre varios similares.

`Incident.request_kind` conserva el tipo libre de requerimiento cuando no hay
una categoría registrada. `request_category` se vincula únicamente con entradas
activas del catálogo. No se crean categorías desde la conversación.

En solicitudes sin equipo o falla, `equipment` y `failure_type` quedan vacíos.
`unit` también puede quedar vacío si no se informó ubicación. La migración
`0015_operational_request_fields` admite esto y mantiene obligatorios equipo y
síntoma para incidencias mediante validación de modelo y restricción SQL.
No se rellena esos campos con textos técnicos inventados. Los valores históricos
`equipment`, `area`, `location_context`, `unit` y `asset_category` se conservan.

## Catálogos y confirmación

Se normalizan nombres y códigos; los IDs de base de datos nunca proceden de la
IA. Un área o equipo ambiguo requiere aclaración. Si un equipo especifica área
y el reporte también, ambas deben coincidir. Equipos y áreas de otra sede no se
asocian. Si no existe inventario se permite describir el equipo mediante texto,
sin inventar códigos, series, marcas o modelos.

Los vínculos propuestos se vuelven a comprobar al confirmar. Una desactivación,
traslado o cambio de catálogo invalida una propuesta anterior y obliga a revisarla.
Se bloquean las filas relacionadas antes de guardar para evitar carreras con
modificaciones concurrentes de la configuración.

Antes de crear un reporte se muestra tipo, sede, área/contexto, equipo o solicitud,
problema/descripción, justificación/impacto y prioridad. Solo una respuesta afirmativa
explícita ante ese resumen permite guardar. Una corrección cambia el borrador y
requiere un nuevo resumen y otra confirmación. Las incidencias históricas sin sede
siguen siendo consultables; no se les asigna una automáticamente.

## Prioridad, errores y cancelación

La prioridad es elegida por el reportante: Baja, Media, Alta o Crítica. El servidor
comprueba la elección textual y conserva su procedencia en el borrador. La IA no
puede asignarla definitivamente, aunque devuelva una prioridad en su extracción.
Urgencia, riesgo de lesiones o palabras como «planta alta» no son una elección.
Una respuesta con varias prioridades exige aclaración.

Los timeouts y respuestas de IA inválidas conservan el borrador y el mensaje
pendiente, incluso al comenzar un reporte. Se puede responder `continuar` sin
repetir los datos. Las preguntas progresivas y las correcciones naturales de los
mensajes heredados se conservan, con ejemplos de gimnasios.

`/cancelar` cancela el borrador sin borrar mensajes ni reportes. `/start` presenta
al asistente de incidencias y solicitudes operativas de Gym Planet. Ambos pasan
por la autorización del reportante y la deduplicación por canal e identificador
externo, igual que los demás mensajes. El acceso automático de desarrollo sigue
controlado por la configuración existente; no se activa en esta entrega.

La creación de borradores y la cancelación se serializan y aíslan por cuenta. La
deduplicación usa además la restricción única de mensajes de PostgreSQL.
Los códigos de reporte históricos se conservan por compatibilidad; este lote
no renumera ni reescribe registros existentes.

## Proveedores y despliegue local

El contrato de extracción agrega `report_type`, `branch`, `request_kind` y
`request_category` como texto, manteniendo los campos anteriores. El proveedor
mock entiende vocabulario de gimnasios y solicitudes. El prompt de Gemini usa
Gym Planet y prohíbe inventar ubicaciones, equipos, diagnósticos o prioridades.
La validación del servidor es independiente del proveedor.

Las pruebas usan mock o proveedores simulados; nunca invocan Gemini ni Telegram
reales y el correo de Django usa memoria durante los tests. El handler Telegram
se prueba mediante un proveedor que solo registra los mensajes en memoria.

Las migraciones 0013, 0014 y 0015 se aplicaron con autorización a la principal:
33 migraciones, 22 tablas y ningún dato operativo. Las nuevas 0016/0017 se validan
solo en `test_incidentes_gymplanet_db` y requieren autorización antes de aplicarse
a la principal. Las sucursales y cuentas reales requieren su propia carga manual.

## Antecedente: entrega conversacional anterior

- Modelo y administración: `incidents/models.py`, `incidents/admin.py`.
- Contrato y proveedores: `incidents/ai/schemas.py`, `incidents/ai/mock_provider.py`,
  `incidents/ai/gemini_provider.py`.
- Flujo: `incidents/services/gymplanet_conversation.py`,
  `incidents/services/incident_processor.py`, `incidents/services/incident_draft_service.py`,
  `incidents/services/incoming_message_service.py`,
  `incidents/services/incident_notification_service.py`.
- Telegram: `incidents/messaging/telegram_update_handler.py`.
- Migración nueva: `incidents/migrations/0015_operational_request_fields.py`.
- Pruebas nuevas y soporte: `incidents/test_gymplanet_conversation.py`,
  `incidents/conversation_test_support.py`.
- Regresiones adaptadas con evidencia explícita de sede: `incidents/tests.py`,
  `incidents/test_ai_timeout_flow.py`, `incidents/test_conversation_data_quality.py`,
  `incidents/test_guided_clarification.py`, `incidents/test_priority_flow.py`,
  `incidents/test_telegram_auto_registration.py`,
  `incidents/messaging/test_telegram_auto_registration.py`.
- Documentación: este archivo, `docs/inventory-maintenance.md` y `README.md`.

La entrega anterior aprobó 220 pruebas. La entrega de identidad corporativa
conserva esas regresiones adaptadas y agrega 46 casos: **266 pruebas aprobadas**.
`check`, `makemigrations --check --dry-run` y `git diff --check` también se validan.
La principal conserva 33 migraciones y 22 tablas; pruebas tiene 35 migraciones y
22 tablas. Ninguna contiene registros operativos creados por las pruebas.
