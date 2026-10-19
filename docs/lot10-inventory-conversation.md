# Lote 10 — Inventario, mantenimiento y conversación

## Punto de partida

Repositorio Gym Planet independiente, `main`, commit `6f4723a`, Git limpio.
Se inspeccionaron modelos, integridad SQL 0013/0014, formularios Admin, vistas,
rutas, servicios, conversación Mock/Gemini y pruebas. Ya existían GymAsset,
EquipmentCategory, MaintenanceRecord, AssetMovement, relación Incident.gym_asset,
filtros y fichas de consulta. Se reutilizan: no se crean catálogos paralelos.

El código de activo mantiene su unicidad existente **dentro de cada sucursal**;
la identidad global es su PK. No se impone unicidad global del texto del código
porque rompería el contrato y los traslados existentes. Código/nombre/categoría/
sucursal son los mínimos; serie, marca, modelo y fechas son opcionales. Las
categorías son administrables sin taxonomía precargada. No se cargaron equipos,
proveedores ni datos ficticios en la principal.

## Diagnóstico reproducido del bot

La expresión que separaba la mención de sucursal terminaba solo en coma, punto y
coma o salto de línea. En «sucursal Quilín. Una trotadora presenta una falla en la
pantalla, que no enciende», capturaba también la oración de la trotadora hasta la
coma. `_process_technical_message` recibía el texto después de esa eliminación:
el extractor no veía el equipo que sí estaba en el mensaje original.
No se atribuye el problema exclusivamente a Gemini ni se consultaron sus logs o
la conversación privada para inventar una respuesta real del proveedor.

Correcciones:

- El separador de sucursal reconoce también el punto, sin cambiar la autorización
  basada en la cuenta. El texto no puede conceder otra sucursal.
- Se aceptan áreas de gimnasio como Cardio y Musculación en la validación de
  respuesta directa. Antes Cardio se consideraba ubicación pero no área válida.
- Si en la primera extracción falta el equipo, una regla literal acotada recupera
  un único nombre de gimnasio explícito. No inventa identificadores ni resuelve
  alternativas o hipótesis; no sustituye equipos ya extraídos válidos.
- Se conserva la fusión de valores del borrador cuando una extracción es parcial.
  La respuesta breve de área se resuelve localmente sin volver a pedir el equipo.
- Se conserva la corrección de prioridad y se admite también «No es X, es Y».
- Varias coincidencias de inventario conservan el texto y dejan gym_asset vacío:
  ya no bloquean el reporte. Códigos explícitos de otra sede y conflictos de área
  siguen rechazándose. Un código inequívoco de la sede puede asociarse.

Prueba obligatoria: Gemini real como clase con transporte simulado devuelve el
equipo ausente; el mensaje original contiene la trotadora. Django la conserva,
pregunta área, acepta Cardio, conserva síntoma/impacto/prioridad y llega al resumen
con una sola llamada simulada. No guarda hasta Sí. Confirmación repetida produce
un reporte y una outbox. También se comprueba el mismo caso con Mock.

## Inventario operativo

Nuevas rutas, todas con autorización de operations_manager o superusuario activo:

| Ruta | Uso |
| --- | --- |
| `/dashboard/assets/new/` | Registro de activo |
| `/dashboard/assets/<id>/edit/` | Edición validada y auditada |
| `/dashboard/assets/categories/` | Crear categorías y consultar las existentes |
| `/dashboard/assets/categories/<id>/` | Editar categoría |
| `/dashboard/assets/<id>/maintenance/new/` | Registrar mantenimiento |
| `/dashboard/assets/<id>/maintenance/<record>/` | Actualizar el registro de ese activo |
| `/dashboard/maintenance/` | Planificación, recurrencia y mantenimientos recientes |

Se mantienen las rutas de inventario y detalle previas. La búsqueda añade marca,
modelo y serie al código, nombre y categoría; mantiene filtros de sede/área/estado.
La ficha agrega serie, incorporación, observaciones, último mantenimiento completado,
próxima fecha explícita, soluciones de incidencias y actividad administrativa.

Nuevos campos opcionales de GymAsset: acquired_on, next_maintenance_at y notes.
No se inventan fechas al migrar. El último mantenimiento se calcula desde
MaintenanceRecord.performed_at de registros completados, sin guardar un duplicado.
Los estados físicos existentes se mantienen: operativo, restringido, fuera de
servicio, mantenimiento, y el indicador is_active permite desactivar el inventario.
La baja se representa con desactivación, sin eliminar ni alterar históricos.
Resolver/cerrar un reporte o completar un mantenimiento no activa automáticamente
una máquina. Los cambios físicos son explícitos en el formulario de activo.

El formulario de activo valida áreas de la sede y reutiliza las restricciones SQL
y mecanismos de traslado. La edición y alta pasan por un servicio transaccional
con bloqueo. AssetActivity conserva antes/después, usuario y fecha; actualización
y borrado se bloquean en modelo y PostgreSQL. Los cambios administrativos técnicos
realizados fuera de estos servicios no se convierten automáticamente en eventos
AssetActivity; los traslados siguen teniendo AssetMovement protegido existente.

## Relación segura con incidencias

El detalle de incidencia permite asociar o desasociar un activo de su sucursal,
con CSRF, validación en servidor y ReportActivity antes/después/autor/fecha. Las
solicitudes no usan este formulario técnico. La operación no cambia descripción,
síntoma, prioridad ni estado físico del activo. Se respetan las reglas SQL sobre
áreas, sucursales y mantenimiento histórico. La falta de activo no bloquea crear
una incidencia. No se reescriben los vínculos históricos tras un traslado.

## Mantenimiento y planificación

Se conserva MaintenanceRecord y sus tipos preventivo/correctivo y estados propios.
Se añade resultado opcional. Los formularios permiten fecha programada, realización,
trabajo, proveedor/responsable libre, resultado y observaciones. Se validan fechas:
solo completados tienen realización y nunca en el futuro. No se crean cuentas
para proveedores. El activo de un registro y su sucursal histórica no se reasignan.
Actualizar un mantenimiento conserva un snapshot de sus valores anteriores y nuevos
en AssetActivity. La próxima fecha opcional indicada en ese formulario actualiza
la planificación explícita del activo; si se deja vacía no borra una fecha previa.
Para borrarla explícitamente se utiliza Editar activo.

La planificación toma la fecha más temprana entre next_maintenance_at del activo
y scheduled_at de mantenimientos pendientes (scheduled/in_progress). Así, completar
o cancelar un registro lo quita del cálculo de pendientes sin inventar frecuencias.
Una fecha manual del activo se conserva hasta que el operador la cambie/borre.
Filtros: vencidos (antes de ahora), próximos 30 días, sin planificación y recurrentes.
Son ventanas informativas, no periodicidades de mantenimiento impuestas al equipo.
No se implementan correos automáticos de mantenimiento.

Recurrencia: al menos dos incidencias del mismo ID de activo en los últimos 90 días,
independientemente de estado, sin confundir otras máquinas con igual nombre ni
incluir texto libre no vinculado. Cuenta reportes, no diagnostica una causa técnica.
El filtro se aplica a activos, no modifica los reportes. El historial muestra
prioridad, fechas, síntoma/descripción, estado y las soluciones auditadas existentes.

## Rendimiento y seguridad

Listados paginados de 20; mantenimiento y auditoría por activo paginados de 30,
con parámetros separados que conservan los demás filtros/páginas. Inventario carga
solo los últimos tres mantenimientos por activo; el detalle permite navegar todos.
Se usan select_related, prefetch y agregaciones. Count distinct evita multiplicar
incidencias al combinar con varios registros de mantenimiento. El conteo de fallas
se prueba expresamente con varios mantenimientos del mismo activo.

Se mantiene la cobertura global de sucursales futuras, la prohibición de acceso
sin rol, CSRF en escrituras, actor derivado de la sesión y validación ModelForm/
modelos/SQL. Las pruebas usan datos sintéticos exclusivamente en PostgreSQL de
pruebas, Mock y correo en memoria; no hay Telegram/Gemini/SMTP reales. No cambian
credenciales, .env, dependencias ni las cinco opciones definitivas de Incident.

## Migración y revisión

**0023_inventory_operations**: tres columnas opcionales en gym_assets, resultado
en maintenance_records, nueva tabla asset_activity y trigger de inmutabilidad que
reutiliza la función protegida de 0020. No modifica filas ni carga equipos/eventos
retrospectivos. No se edita ninguna migración 0019–0022 ni se aplica 0023 a la
principal. La reversión de 0023 eliminaría esas columnas/tabla: no hacerlo tras
cargar información sin un plan de respaldo y aprobación.

Antes de iniciar esta versión contra la principal se debe revisar y autorizar
0023 en otra tarea, comprobar la identidad de base y luego aplicarla. No ejecutar
migrate ahora. Después, acceder mediante `/operations/login/`, abrir Equipos y
activos, administrar categorías autorizadas, registrar un activo real verificado
y revisar detalle/mantenimiento. Las acciones son escrituras reales: no usar los
reportes protegidos para ensayos ni asociarlos sin autorización adicional.

La prueba manual del bot, en una fase posterior autorizada, debe repetir el mensaje
de Quilín y responder Cardio; revisar el resumen y cancelar si solo es un ensayo.
No activar proveedores reales durante la revisión de código. Las pruebas simulan
la clase Gemini y verifican también Mock, duplicados, cancelación y cuenta autorizada.

Pendiente para futuros lotes: carga validada de inventario real, importación masiva,
políticas de mantenimiento por fabricante, recordatorios de mantenimiento, garantía
universal para español libre y evaluación real con trabajadores. No se implementan
WhatsApp/OpenAI ni automatismos para reparar o dar de alta físicamente equipos.

## Reanudación y revisión visual

Se conservaron los cambios locales del lote al reanudar. La revisión visual se
realizó con Django, PostgreSQL de pruebas y autenticación de operaciones, no con
HTML aislado. Se comprobó inicio de sesión, inventario, ficha, formulario de edición
y guardado mediante POST con CSRF, incluyendo el nuevo evento de auditoría.
Se revisó escritorio y móvil de 390 px: sin desbordamiento horizontal de la página;
las tablas conservan desplazamiento horizontal para mantener legibles sus columnas.
No se observaron errores de consola en ese recorrido.

Las capturas locales ignoradas por Git son `env/lot10-asset-desktop.png` y
`env/lot10-asset-mobile.png`. Solo muestran datos sintéticos. Tras la interrupción
quedaron en la base de pruebas la cuenta `lot10-visual`, la sucursal `TEST-VISUAL`,
la categoría `TEST-CARDIO`, el activo `TEST-BICI`, un mantenimiento, tres eventos
y una sesión. Se verificaron sus identidades y que no había otros datos operativos
antes de realizar la limpieza estándar de Django exclusivamente en esa base.
No se deshabilitaron triggers ni se modificó la principal.

La revisión de consultas corrigió un error de prefetch limitado: el queryset
recortado se guarda ahora en `recent_maintenance` con `to_attr`, evitando que el
manager relacionado intente filtrar un queryset ya cortado. Se mantienen los tres
últimos registros en el listado y el historial completo paginado en la ficha.

## Archivos del lote

Modificados:

- README.md
- config/urls.py
- incidents/dashboard_views.py
- incidents/forms.py
- incidents/models.py
- incidents/services/conversation_controls.py
- incidents/services/gym_language.py
- incidents/services/gymplanet_conversation.py
- incidents/services/incident_processor.py
- incidents/services/operations.py
- incidents/static/incidents/dashboard.css
- incidents/templates/incidents/asset_detail.html
- incidents/templates/incidents/assets.html
- incidents/templates/incidents/report_detail.html
- incidents/test_gymplanet_conversation.py

Nuevos:

- docs/lot10-inventory-conversation.md
- incidents/inventory_views.py
- incidents/migrations/0023_inventory_operations.py
- incidents/services/inventory.py
- incidents/templates/incidents/inventory_form.html
- incidents/templates/incidents/maintenance_dashboard.html
- incidents/test_lot10.py

Son 22 archivos para revisión. Los scripts, logs y capturas auxiliares están en
`env/`, excluido de Git. No se modificaron `.env`, dependencias, settings ni las
migraciones anteriores. No se hizo commit ni push.

## Resultado final verificado

- `uv run python manage.py check`: sin incidencias.
- `uv run python manage.py makemigrations --check --dry-run`: sin cambios pendientes
  entre modelos y archivos de migración.
- `uv run python manage.py test incidents --keepdb`: **425 aprobadas**, cero fallos
  y cero errores, en **103,213 segundos** (108,019 incluyendo el arranque del comando).
  Son las 398 anteriores y 27 pruebas nuevas; se ajustó la expectativa anterior de
  inventario ambiguo para permitir texto libre, conservando el rechazo de áreas
  incompatibles. Gemini utiliza transporte simulado; correo en memoria.
- `git diff --check`: sin errores de espacios o parches.
- Principal: **40 migraciones, 25 tablas**, hasta 0022. La huella de filas, esquema,
  restricciones y secuencias coincide antes y después. Se conservan SC-000001,
  SC-000002, GP-000003 y GP-000004, seis actividades y una asignación.
- Pruebas: **41 migraciones, 26 tablas**, incluida 0023, sin datos operativos ni
  restos de la revisión visual. Se verificaron las cuatro columnas nuevas y el
  trigger de protección de asset_activity exclusivamente en esta base.

No hay bloqueo técnico encontrado en las verificaciones. Falta la revisión humana
y autorización para aplicar 0023 a la principal antes de utilizar allí las nuevas
pantallas. La aceptación con Telegram/Gemini reales queda para una prueba manual
posterior; esta ejecución no los activó ni envió correos reales.
