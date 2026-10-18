# Lote 8 — Gestión operativa

## Alcance y permisos

Partida: `main`, commit `58cc026`, Git limpio. Nuevo permiso
`incidents.manage_operations`, asignado al grupo `operations_manager` mediante
0020. Nombre funcional: Encargado General de Operaciones. Incluye todas las
sucursales actuales y futuras sin listas regionales. No depende del nombre de una
persona. Usuario activo y permiso son obligatorios tanto en vistas como servicios
y API de reportes. Los superusuarios mantienen acceso, con las mismas validaciones de
estado. El endpoint técnico de simulación de mensajes sigue restringido a
superusuarios. El rol no recibe permisos de usuarios, integraciones o Admin.

Acceso propio: `/operations/login/`, sin requerir `is_staff`. Logout por POST con
CSRF. Las operaciones de asignación, comentarios y estado son POST autenticados y
con CSRF; la API existente de estados comparte el servicio. La API no permite
usar IDs del formulario para elegir al actor. No se agregan notificaciones.

## Asignación, seguimiento y estados

`ReportAssignment` almacena oficio/responsable obligatorio, persona y notas
opcionales, autor y fecha automáticos. Cada reasignación crea una fila nueva;
la última es la asignación actual. No se crean cuentas para proveedores.

`ReportActivity` conserva creación, asignación/reasignación, comentarios, solución,
resolución, cierre, cancelación y reapertura con autor, fecha y valores anteriores
y nuevos cuando corresponden. La creación por mensajería se registra como sistema,
sin atribuirla falsamente a un usuario Django; el reporte conserva su reportante.
No se fabrican eventos para los registros anteriores a estas migraciones.

Todas las escrituras operativas pasan por un servicio transaccional con bloqueo
del reporte. El historial impide edición y eliminación en modelo y con triggers
PostgreSQL, incluso mediante actualizaciones de QuerySet. Sus FK protegen reporte
y autor. Las correcciones se documentan con una nueva entrada, nunca editando la
anterior. Son controles de aplicación, no una defensa frente al propietario de la
base que puede alterar SQL o deshabilitar triggers.

Regla vigente: el usuario autorizado puede seleccionar cualquier estado existente
sin depender del estado actual. Los cinco estados operativos Reportado, En revisión,
En atención, Resuelto y Cerrado están disponibles; se conserva Cancelado por
compatibilidad con el modelo anterior. No se exigen pasos intermedios ni motivo
obligatorio al reabrir. El motivo u observación queda opcional y se audita si se
proporciona.

Resolver exige una solución no vacía (máximo 4000 caracteres) y registra solución,
autor y fecha, incluso al pasar directamente de Reportado o Cerrado a Resuelto.
Cerrar no exige haber resuelto; conserva cualquier solución y fecha fiable ya
existentes y no inventa una fecha de resolución si no la había. Al volver a un
estado abierto se limpia la fecha de resolución actual, conservando todas las
soluciones y fechas anteriores en el historial. Cada cambio real registra estado
anterior, nuevo, usuario y fecha, sin modificar responsables.
Seleccionar el mismo estado es una operación sin efecto: no altera fechas,
soluciones ni crea eventos duplicados. Un estado desconocido sigue siendo inválido.
El estado de Admin sigue de solo lectura; la gestión se realiza desde el dashboard.

## Interfaz y estadísticas

El detalle conserva la información original, suma asignación/reasignación,
seguimiento, solución, observación opcional y actividad cronológica paginada de 30
en 30. Escapado HTML, formularios con límites y errores comprensibles. El diseño,
logo, colores y navegación existentes se conservan; Admin técnico no aparece para
el encargado general.

Centro de Control agrega contadores reportados, en revisión, en atención,
resueltos, cerrados y sin responsable (todos los estados), y promedio del tiempo
creación→resolución solo si hay fechas válidas, sin estimaciones. Conserva filtros
por sucursal, tipo, estado, prioridad y fechas, y métricas previas. Reabiertos sin
fecha actual no entran en el promedio; cerrados con fecha conservada sí.

## Migraciones e impacto

- `0019_operational_management`: permiso Meta de Incident y dos tablas nuevas
  `report_assignments`, `report_activity`. No cambia códigos ni columnas de los
  reportes previos; no inserta eventos retrospectivos.
- `0020_operations_role_history`: grupo y permiso operativo, más función y triggers
  de protección. No crea usuarios ni modifica usuarios existentes. Al revertirla
  se retiran triggers/función pero se conserva el grupo/permiso para no eliminar
  asignaciones administrativas de permisos.

Las migraciones históricas 0001–0018 no se modifican. Solo se aplican las nuevas
en `test_incidentes_gymplanet_db`. **No iniciar esta versión contra la principal
hasta revisar y autorizar sus migraciones**: las vistas y el registro de actividad
necesitan las tablas nuevas. No ejecutar `migrate` por cuenta propia en la
principal como parte de la revisión. No se carga ningún catálogo ni cuenta real.

## Crear la cuenta de Bastián de forma segura, después de autorizar migraciones

1. Un administrador técnico verifica identidad de base y aplica las migraciones
   aprobadas en una tarea posterior. Comprobar grupo `operations_manager`, con
   exclusivamente `Gestionar operaciones de todas las sucursales`.
2. En Django Admin → Usuarios → Añadir, elegir un nombre de usuario personal
   exclusivo y establecer una contraseña individual mediante el formulario local,
   respetando los validadores. No ponerla en chat, comandos o archivos.
3. Nombre: Bastián. Apellido: Bretti. Dejar correo vacío hasta recibir uno real.
   Marcar Activo, desmarcar Staff y Superusuario. Asignar solo el grupo operativo;
   no otorgar permisos individuales técnicos. No modificar usuarios existentes.
4. Entregar el acceso por un canal privado y abrir `/operations/login/`.
   No se requiere cuenta de usuario para cada responsable asignado.

No se ha creado esta cuenta ni se inventaron correo o contraseña. El rol funciona
igual para una persona distinta en el futuro.

## Pruebas y revisión

`incidents/test_operations.py` cubre rol, cobertura futura, denegaciones, asignación
sin/con persona, reasignación, historial, transiciones, solución obligatoria,
cierre/reapertura, CSRF, actor autenticado, incidencias/solicitudes y estadísticas.
Las pruebas de estados se adaptan a la selección libre; no se
eliminan casos. La prueba de migración de códigos restaura el esquema más reciente
antes de usar modelos actuales, manteniendo la comprobación de SC/GP.

Comandos de validación: `uv run python manage.py check`,
`uv run python manage.py makemigrations --check --dry-run`,
`uv run python manage.py test incidents --keepdb`, `git diff --check`.
Se ejecutan con Mock y transportes desactivados por variables del proceso, sin
modificar `.env`, exclusivamente en la base de pruebas. No se usa Gemini real.

Pendiente de revisión: aprobación funcional del flujo y autorización de 0019/0020
en la principal; creación manual de la cuenta personal; prueba de aceptación con
usuarios reales después de esa autorización. No hay commit ni push.

## Archivos de la entrega

- `docs/lot8-operations.md`
- `incidents/migrations/0019_operational_management.py`
- `incidents/migrations/0020_operations_role_history.py`
- `incidents/operational_signals.py`
- `incidents/services/operations.py`
- `incidents/templates/incidents/operations_login.html`
- `incidents/test_operations.py`
- `README.md`
- `config/urls.py`
- `incidents/admin.py`
- `incidents/apps.py`
- `incidents/dashboard_views.py`
- `incidents/models.py`
- `incidents/permissions.py`
- `incidents/services/report_status.py`
- `incidents/static/incidents/dashboard.css`
- `incidents/templates/incidents/dashboard.html`
- `incidents/templates/incidents/operational_base.html`
- `incidents/templates/incidents/report_detail.html`
- `incidents/test_corporate_identity.py`
- `incidents/test_dashboard_http.py`
- `incidents/test_operational_dashboard.py`
- `incidents/tests.py`
- `incidents/views.py`

La revisión visual se realizó con un servidor HTTP real y PostgreSQL en pruebas,
login normal con una cuenta sintética sin Staff/Superusuario, asignación previa,
comentario y transición desde el formulario con CSRF. Escritorio y móvil de
390 px, sin desbordamiento de página ni errores de consola. Capturas ignoradas:
`env/lot8-desktop.png` y `env/lot8-mobile.png`. El test temporal terminó y retiró
los datos y sesiones sintéticos. No se creó una cuenta real.


## Resultado final

**373 pruebas aprobadas: 349 regresiones conservadas y 24 nuevas; 0 fallos,
0 errores, 54,164 segundos.** `check`, `makemigrations --check --dry-run` y
`git diff --check` correctos. En el primer cierre se adaptaron expectativas de transiciones antiguas
a las reglas entonces vigentes y el destino del login operativo; no se eliminaron pruebas.

Principal: 36 migraciones, 22 tablas y tres reportes SC-000001, SC-000002 y
GP-000003. Huella de contenido/esquema/restricciones/secuencias idéntica antes y
después de pruebas y revisión visual. Pruebas: 38 migraciones, 24 tablas, sin
reportes, asignaciones, actividad, cuentas o catálogos operativos al finalizar.
Django limpia los grupos sintéticos al terminar las pruebas transaccionales;
no se dejan usuarios de demostración. No se modificaron credenciales ni .env.

## Cómo revisar después de aprobar las migraciones

Antes de aprobarlas, revisar el diff, este informe y las capturas sintéticas.
Después de su aplicación autorizada, crear la cuenta con las instrucciones
anteriores y, desde una terminal temporal de PowerShell:

```powershell
$env:AI_PROVIDER = 'mock'
$env:TELEGRAM_WEBHOOK_ENABLED = 'False'
$env:TELEGRAM_AUTO_REGISTER = 'False'
$env:EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
uv run python manage.py runserver 127.0.0.1:8000
```

Abrir `http://127.0.0.1:8000/operations/login/` y luego `/dashboard/`.
Las asignaciones, comentarios y cambios de estado allí serían escrituras reales:
no utilizar los tres reportes preservados para pruebas de aceptación sin una
nueva autorización. Las pruebas automatizadas siguen usando exclusivamente la
base `test_incidentes_gymplanet_db`. No iniciar el bot para revisar el dashboard.
No hay migraciones aplicadas a la principal, commit ni push en esta entrega.


## Ajuste posterior: cambio libre de estados

Se elimina el mapa de transiciones obligatorias del servicio central. El selector
ya incluía los estados del modelo y los sigue ofreciendo; se actualizan las
instrucciones del formulario y la etiqueta del motivo opcional. API y dashboard
comparten la nueva regla, con permisos y CSRF intactos.

Archivos de este ajuste: `incidents/services/operations.py`,
`incidents/dashboard_views.py`, `incidents/templates/incidents/report_detail.html`,
`incidents/test_operations.py` y este documento. No cambia ningún modelo ni
migración. Este ajuste no aplica migraciones a la principal.
Las pruebas nuevas incluyen las 50 combinaciones de los cinco estados en ambos
tipos de reporte, cierre directo, solución requerida, conservación de asignación,
historial, fecha y solución, operaciones repetidas sin efecto y denegación sin rol.


Validación del ajuste: **378 pruebas aprobadas, 0 fallos y 0 errores, en 57,295
segundos**. Correctos `check`, `makemigrations --check --dry-run` y
`git diff --check`. Las pruebas se ejecutaron exclusivamente en la base de
pruebas con Mock, sin servicios externos. No se requieren migraciones adicionales.
La lectura previa de esta ejecución ya mostró 38 migraciones y 24 tablas en la
principal (estado posterior al informe original del lote 8). Su huella permaneció
idéntica durante el ajuste, incluidos los tres reportes protegidos. No se aplicó
ninguna migración ni se crearon usuarios o registros en la principal durante esta
ejecución. La base de pruebas terminó sin registros operativos. Los apartados de
resultado y autorización originales del lote 8 describen su cierre anterior.
