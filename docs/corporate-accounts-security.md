# Cuentas corporativas y acceso seguro

## Identidad y sucursal

`TechnicianChannel` representa una cuenta de mensajería corporativa. Su relación
opcional `branch` autoriza exactamente una sucursal. La unicidad existente de
`(provider, external_user_id)` impide asignar la misma identidad a dos cuentas.
Se conserva `Technician` como agrupador histórico de reportantes, sin asumir que
la cuenta identifica a una persona: varios trabajadores pueden compartirla.
Una misma agrupación puede tener varias cuentas, con borradores independientes.

En Telegram se usa `message.from.id` de actualizaciones obtenidas del transporte
Telegram; el nombre, username, teléfono y texto no prueban la identidad. Solo se
aceptan conversaciones privadas, no grupos, bots ni mensajes `sender_chat`.
Telegram no entrega automáticamente el teléfono del remitente.
El espacio de identidades por proveedor admite en el futuro un remitente de
WhatsApp verificado por su adaptador; esta entrega no implementa esa integración.

`IncomingMessageService` rechaza por defecto IDs no verificados. Solo un adaptador
de transporte autorizado debe pasar `verified_transport=True`; ese argumento es
una frontera interna de confianza, no un mecanismo de autenticación ni un campo
aceptado por la API. El handler de Telegram queda detrás del polling directo de
Telegram o del webhook autenticado por secreto. No se aceptan IDs HTTP como
prueba de identidad del remitente.

Una cuenta sin asociación, desactivada, con reportante inactivo o con sucursal
inactiva no puede reportar, aunque escriba el nombre de una sede válida. El
autorregistro opcional de desarrollo conserva solo una cuenta pendiente, sin
sucursal ni autorización para reportar; sigue desactivado por defecto.

La sucursal del reporte viene de la cuenta, nunca de Mock, Gemini o el cliente.
No se pregunta la sucursal a una cuenta autorizada. Una mención de otra sede
bloquea la creación; si ya había un borrador, conserva sus datos y requiere
resolver la discrepancia mencionando la sede autorizada y revisar nuevamente el
resumen antes de confirmar. Una cuenta reasignada no reutiliza el borrador de su
sede anterior: debe cancelarlo y comenzar otro; el historial queda conservado.

Antes de guardar se vuelven a comprobar cuenta, reportante, asociación y sede
activos, con bloqueos transaccionales. Los borradores se seleccionan y cancelan
por cuenta, con una restricción única para un borrador activo por cuenta.
Los IDs repetidos de otra cuenta se rechazan sin exponer su mensaje. Persisten
las preguntas progresivas, correcciones, prioridad explícita, confirmación final
y recuperación ante errores de IA.

## Vinculación manual en Django Admin

Realizar estos pasos **después** de revisar y autorizar las migraciones 0016 y
0017 en la base de desarrollo. No se cargan sedes ni cuentas automáticamente.

1. Iniciar sesión con un superusuario Django activo. Crear o verificar primero
   la sucursal real aprobada y mantenerla activa.
2. Crear un reportante con una etiqueta corporativa de la sucursal, sin atribuir
   la cuenta a un trabajador individual. El teléfono heredado puede quedar vacío.
3. Cuando se autorice conectar un bot real, comprobar el ID corporativo a partir
   de `message.from.id` de una actualización recibida directamente de Telegram
   mediante el transporte autorizado. Verificar que corresponde a la cuenta
   corporativa prevista y a un chat privado. No confiar en IDs escritos en un
   mensaje o enviados a un endpoint HTTP, ni inferirlos desde un teléfono.
4. En **Canales de reportantes**, agregar una cuenta con el reportante corporativo,
   proveedor Telegram, identificador externo verificado y sucursal autorizada.
   El ID de chat, si se registra, procede de `message.chat.id`; username es solo
   descriptivo. Activar la cuenta y guardar. También existe el formulario inline
   en el reportante. No usar números o identificadores de demostración en la base
   principal.
5. Verificar la asociación en la lista del Admin. La aplicación rechaza duplicados
   del mismo proveedor y valida que la sucursal esté activa.

Para revocar acceso, desactivar la cuenta o el reportante; no borrar el historial.
Una identidad con mensajes no puede cambiar de proveedor, ID o reportante desde
formularios administrativos. Las relaciones históricas están protegidas contra
eliminación. Mensajes y borradores son consultables sin edición ni eliminación
en Admin. Cambiar una asociación de sucursal exige revisar los borradores pendientes.

## API, dashboard y transportes

Política temporal: **solo superusuarios activos** pueden consultar incidencias,
estadísticas y dashboard, o cambiar estados. Los usuarios regulares y staff sin
ese rol no acceden a registros de ninguna sede, incluso con permisos de modelo
asignados. Django Admin mantiene el acceso de superusuarios; sus modelos operativos
también aplican esta política. Se usan las autenticaciones reales de Django/DRF;
no hay credenciales permanentes de demostración ni autenticación por IDs del cliente.

El dashboard anónimo redirige al login de Admin. Las consultas de API no autenticadas
se rechazan. Las sesiones autenticadas mantienen la protección CSRF de Django/DRF.

El endpoint `messages/test/` requiere superusuario y está cerrado por defecto.
Aun con `DEBUG=True` y `ENABLE_TEST_MESSAGE_ENDPOINT=True`, rechaza la suplantación
de remitentes mediante HTTP; usar pruebas automatizadas con proveedores simulados
para validar conversaciones. No sirve como puerta alternativa al transporte.

El webhook está deshabilitado por defecto:

- `TELEGRAM_WEBHOOK_ENABLED=False`.
- `TELEGRAM_WEBHOOK_SECRET` vacío: incluso habilitado, no procesa peticiones.
- Al habilitarlo posteriormente, exige un secreto exclusivo configurado tanto en
  el servidor como en `setWebhook(secret_token=...)`; verifica en tiempo constante
  `X-Telegram-Bot-Api-Secret-Token` antes de construir el handler.
- El secreto del webhook es independiente del token del bot y no debe ir en URLs,
  repositorio, chat o historial de comandos. Configurarlo privadamente cuando se
  autorice la conexión real. HTTPS y protección del host corresponden al despliegue.
- Para long polling dejar el webhook deshabilitado; no operar ambas modalidades
  simultáneamente. Esta entrega no configura ni inicia ninguna de ellas.

Queda pendiente diseñar membresías y permisos de supervisores por sucursal,
acceso de lectura frente a cambios de estado, y auditoría de reasignaciones. Hasta
entonces no ampliar el acceso de staff. WhatsApp y OpenAI quedan fuera de esta entrega.

## Migraciones y verificación local

Se verificó `main` en `0edb6f5`, origin `eduardopozog10/IncidentesGymPLANET`, con
Git limpio antes de editar. La conexión efectiva comprobada fue exclusivamente
`incidentes_gymplanet_db`, `incidentes_gymplanet_app`, `localhost:5432`.

La principal tenía 32 migraciones, 22 tablas y cero registros operativos.
Tras inspeccionar `migrate --plan` y `sqlmigrate incidents 0015`, se aplicó únicamente
`0015_operational_request_fields`, conforme a la autorización condicional.
Resultado: **33 migraciones, 22 tablas**, sin datos operativos.

Migraciones nuevas de esta entrega, **solo aplicadas en pruebas**:

- `0016_corporate_account_branch`: asociación opcional cuenta/sucursal, cuenta de
  origen del borrador, sustitución de la unicidad por reportante por unicidad de
  borrador activo por cuenta. Vincula borradores históricos únicamente cuando su
  mensaje inicial prueba la cuenta y coincide con el reportante; deja los demás
  sin atribución y sin retomarlos desde cuentas nuevas. No asigna sucursales.
- `0017_protect_messaging_history`: protege reportantes con cuentas y cuentas con
  mensajes contra eliminación mediante ORM; conserva todas las filas existentes.

La base de pruebas es exactamente `test_incidentes_gymplanet_db`, distinta de la
principal, con **35 migraciones y 22 tablas** después de los tests. La comparación
antes/después incluye contenido, estructura, restricciones y secuencias de la
principal; se mantiene idéntica durante las pruebas. Ambas bases quedan sin datos
operativos de prueba.

Validación:

```powershell
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test incidents --keepdb
git diff --check
```

Las pruebas conservan las 220 regresiones adaptadas al nuevo contrato de identidad
y agregan 46 casos de cuentas, borradores, suplantación, permisos y webhook:
**266 pruebas aprobadas**, sin servicios externos. La configuración privada y las
dependencias no cambian; no hay commit ni push. Las migraciones 0016/0017 necesitan
revisión y autorización antes de usar esta versión del código en la base principal.


## Archivos modificados en esta entrega

27 archivos para revisión en GitHub Desktop, sin commit:

- Configuración y acceso: `config/settings.py`, `incidents/views.py`,
  `incidents/permissions.py`, `incidents/dashboard_views.py`, `incidents/admin.py`.
- Modelo y migraciones: `incidents/models.py`,
  `incidents/migrations/0016_corporate_account_branch.py`,
  `incidents/migrations/0017_protect_messaging_history.py`.
- Identidad y conversación: `incidents/services/corporate_identity.py`,
  `incidents/services/incoming_message_service.py`,
  `incidents/services/incident_processor.py`,
  `incidents/services/incident_draft_service.py`,
  `incidents/services/gymplanet_conversation.py`,
  `incidents/messaging/telegram_update_handler.py`.
- Pruebas: `incidents/test_corporate_identity.py`, `incidents/tests.py`,
  `incidents/test_gymplanet_conversation.py`, `incidents/test_ai_timeout_flow.py`,
  `incidents/test_conversation_data_quality.py`, `incidents/test_guided_clarification.py`,
  `incidents/test_priority_flow.py`, `incidents/test_telegram_auto_registration.py`,
  `incidents/messaging/test_telegram_auto_registration.py`.
- Documentación: `README.md`, `docs/gymplanet-conversation.md`,
  `docs/inventory-maintenance.md` y este archivo.

No se modificaron migraciones 0001–0015, el comando de carga de sucursales,
plantillas/estilos del dashboard, dependencias ni configuración privada.
