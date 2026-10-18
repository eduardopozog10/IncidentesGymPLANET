# Lote 6 — Interfaz operativa Gym Planet

## Resultado y alcance

Nueva interfaz propia, con dark mode, tratamiento tipográfico provisional Gym
Planet, navegación lateral, indicadores y gráficos hechos con HTML/CSS. No usa
logos oficiales inventados, fuentes remotas, imágenes externas, bibliotecas de
gráficos ni JavaScript. Las variables visuales están centralizadas en
`incidents/static/incidents/dashboard.css`.

Se mantienen el diseño de Django Admin, las rutas anteriores de API y dashboard,
la autorización exclusiva de superusuarios activos, las relaciones de integridad,
la protección CSRF y la confirmación final del asistente. Los supervisores por
sucursal siguen pendientes de otro lote; no se amplían permisos.

## Rutas operativas

| Ruta | Uso |
| --- | --- |
| `/dashboard/` | Centro de control, indicadores, distribuciones y actividad reciente |
| `/dashboard/incidents/` | Solo `report_type=incident` |
| `/dashboard/requests/` | Solo `report_type=request`, sin columnas técnicas vacías |
| `/dashboard/reports/` | Listado general, filtros y paginación |
| `/dashboard/reports/<id>/` | Detalle según tipo; POST autenticado y con CSRF para cambiar estado |
| `/dashboard/branches/` | Sedes y sus indicadores de reportes |
| `/dashboard/assets/` | Inventario y mantenimiento asociado, incluido estado vacío |

Las rutas `/api/v1/incidents/`, detalle, resumen y analítica se conservan. El
serializador agrega relaciones de gimnasio y tipo de reporte sin retirar campos
anteriores. La búsqueda admite códigos SC/GP y mantiene los demás filtros; IDs
HTTP no autentican remitentes de mensajería.

## Consultas e indicadores

Todos los valores se consultan en PostgreSQL. Los indicadores de reportes,
distribuciones, evolución y listados recientes respetan la selección de sucursal,
fechas, tipo, prioridad, estado, área, categoría y búsqueda. Los rangos inválidos
se explican mediante errores de formulario y no muestran resultados sin filtrar.

- **Incidencias abiertas** y **solicitudes pendientes**: estados `reported`,
  `under_review` e `in_progress`.
- **Alta o crítica**: incidencias abiertas con esas prioridades; la tarjeta
  explicita esta definición.
- **Sucursales activas** y **equipos fuera de servicio**: estado actual del
  catálogo dentro de la sucursal seleccionada, sin fingir una reconstrucción
  histórica para fechas anteriores. Los activos desactivados no se cuentan como
  inventario activo fuera de servicio.
- La actividad muestra la última actualización de cada reporte, sin inventar un
  registro de eventos intermedios.
- La evolución agrupa todos los días con reportes del período; no agrega puntos
  ficticios ni omite días por un límite arbitrario. Existe un listado textual de
  los valores del gráfico.
- Las sucursales se agrupan por identidad/código, incluso si comparten nombre.

Los filtros se ejecutan en el servidor. Los listados paginan de 20 en 20 y
conservan los parámetros al cambiar de página. En sucursales, los filtros de
reporte afectan sus indicadores. El inventario tiene filtros propios por sede,
categoría, estado operativo, área y búsqueda de activo; su contador de incidencias
asociadas no se pierde por buscar el código de una máquina.

## Detalles y estados

Incidencias muestran equipo, síntoma, impacto y mantenimiento relacionado.
Solicitudes muestran tipo, categoría, descripción y justificación. Cantidades y
materiales se conservan como texto informado: no se inventan campos estructurados.

La actualización de estados reutiliza `IncidentStatusUpdateSerializer` y un
servicio transaccional compartido entre API e interfaz. Solo se admiten los estados
existentes; `resolved_at` se fija al resolver y se limpia al volver a otro estado,
como en la implementación anterior. Los campos enviados adicionalmente no
reasignan sucursal, cuenta, descripción ni código del reporte.

## Códigos y migración 0018

`Incident.code_prefix` conserva el prefijo como dato del registro; `code` sigue
siendo una propiedad compatible con Telegram, Admin, serializador y plantillas.
La migración nueva `0018_gymplanet_report_codes` depende de 0017:

1. Agrega el campo con `SC` para los registros que ya existen.
2. Conserva `GP` como valor predeterminado de aplicación para creaciones futuras.

La numeración utiliza la clave primaria generada por la secuencia de PostgreSQL,
sin consultar `MAX()+1`, sin reiniciar secuencias y sin un contador en memoria.
La clave primaria impide reutilizar el mismo número bajo concurrencia, y el código
lo incorpora completo. No se renumeran reportes antiguos. Los saltos que puedan
dejar transacciones canceladas son normales; no se garantiza numeración sin huecos.

Se prueba tanto la migración real desde 0017 con registros históricos como doce
inserciones concurrentes en cuatro conexiones. Las operaciones de migración de
esas pruebas se ejecutan exclusivamente en `test_incidentes_gymplanet_db`.

**0018 no se aplicó a la principal.** No ejecutar esta versión contra la principal
hasta revisar y autorizar esa migración: las consultas del modelo nuevo necesitan
la columna nueva. No ejecutar `migrate` por cuenta propia como parte de la revisión.

## Asistente y Gemini

- Django incorpora la sucursal corporativa autorizada al contexto, incluso en el
  primer mensaje. El prompt prohíbe solicitarla o cambiarla.
- Las preguntas sobre campos faltantes las selecciona Django; una pregunta de IA
  no puede sustituirlas por una pregunta de sucursal o prioridades en inglés.
- Se preservan las aclaraciones específicas generadas por el procesador local.
- Las preguntas visibles presentan Baja, Media, Alta y Crítica. Los códigos
  internos continúan en inglés; urgencia o riesgo no eligen una prioridad.
- Expresiones explícitas como «fuera de servicio» o «no puede utilizarse» aportan
  impacto sin inferirlo únicamente desde una falla. Se comprueba también el caso
  negativo «no está fuera de servicio».
- Los resúmenes omiten familia heredada, campos de solicitud en incidencias,
  categorías ausentes y ubicaciones repetidas. No se recorta ni altera la
  descripción original para hacerla más breve.
- Se conservan correcciones, borradores por cuenta, historial, deduplicación y
  confirmación final antes de guardar. No se implementó OpenAI.

## Verificación y preservación

Se retomó `main`, commit `ab8099a`, con todos los avances locales conservados.
La validación final aprobó **310 pruebas** en 70,793 segundos, sin fallos ni errores: 266 regresiones adaptadas y 44
casos nuevos. Incluye un servidor HTTP real de Django con PostgreSQL, sesiones
autenticadas de prueba, acceso anónimo/staff rechazado, CSS servido, listados,
detalles, filtros y POST con CSRF. No se crearon credenciales de demostración
permanentes ni accesos alternativos para la aplicación.

```powershell
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test incidents --keepdb
git diff --check
```

Las pruebas sobrescriben únicamente variables de su proceso: Mock, claves externas
vacías y correo en memoria. El `.env` privado mantiene su configuración funcional
real sin modificaciones. No se activan Telegram, Gemini, WhatsApp u OpenAI durante
la validación automatizada.

La principal se compara con una huella previa de contenido, esquema, restricciones
y secuencias mediante conexiones de solo lectura. Conserva **35 migraciones y 22
tablas**, así como `SC-000001` (incidencia) y `SC-000002` (solicitud), sin cambios.
La base de pruebas conserva **36 migraciones y 22 tablas** y queda sin reportes
operativos. No se accede a bases de otros proyectos.

La revisión visual usa capturas de escritorio de 1440 px, una vista intermedia y
una vista móvil de 390 px. Los artefactos ignorados en `env/lot6-preview/` son
instantáneas con datos sintéticos de pruebas, no una aplicación alternativa.
Sus formularios/enlaces no constituyen una sesión operativa. Las funcionalidades
se verifican separadamente mediante Django Test Client y HTTP real.

Accesibilidad básica: idioma español, etiquetas de formulario, encabezados de
tabla, enlace para saltar al contenido, foco visible, navegación por teclado,
detalle nativo para filtros/gráficos y respeto de movimiento reducido. Las tablas
y la navegación móvil pueden desplazarse dentro de sus propios contenedores.
No hay JavaScript operativo ni recursos externos que deban descargarse.

Problemas corregidos al continuar: comprobación demasiado amplia que confundía
una palabra dentro de la descripción con una etiqueta duplicada, preguntas locales
específicas sustituidas por preguntas genéricas, filtros de inventario que
afectaban incorrectamente el contador de incidencias y búsquedas de códigos
numéricos excesivamente largos.

## Cómo revisar localmente

Por ahora revisar el diff, esta documentación y las capturas ignoradas, sin aplicar
migraciones a la principal. El diseño de Django Admin y las migraciones 0001–0017
se mantienen sin cambios.

Después de revisar y autorizar la aplicación de 0018 en una tarea posterior:

```powershell
uv run python manage.py check
uv run python manage.py runserver 127.0.0.1:8000
```

Iniciar sesión con el superusuario existente en `http://127.0.0.1:8000/admin/login/`
y abrir `http://127.0.0.1:8000/dashboard/`. No es necesario ejecutar el bot ni llamar
a Gemini para consultar los reportes. Revisar las secciones separadas de
incidencias y solicitudes, los filtros, ambos detalles y el inventario vacío.
Un cambio de estado en esta sesión sí modifica un reporte real: limitar esta
revisión inicial a consultas si aún no se ha autorizado esa modificación.

Pendiente operativo: revisión/autorización de 0018 y aceptación funcional con los
datos principales. No se hace commit ni push; no hay funcionalidades de código
del lote dejadas sin implementar.


## Archivos de la entrega

29 archivos para revisión, sin commit ni push:

- `README.md`
- `config/urls.py`
- `docs/lot6-operational-dashboard.md`
- `incidents/ai/gemini_provider.py`
- `incidents/ai/mock_provider.py`
- `incidents/dashboard_views.py`
- `incidents/migrations/0018_gymplanet_report_codes.py`
- `incidents/models.py`
- `incidents/report_filters.py`
- `incidents/serializers.py`
- `incidents/services/gymplanet_conversation.py`
- `incidents/services/incident_processor.py`
- `incidents/services/report_status.py`
- `incidents/static/incidents/dashboard.css`
- `incidents/templates/incidents/_distribution.html`
- `incidents/templates/incidents/_filters.html`
- `incidents/templates/incidents/_pagination.html`
- `incidents/templates/incidents/_recent.html`
- `incidents/templates/incidents/_report_table.html`
- `incidents/templates/incidents/assets.html`
- `incidents/templates/incidents/branches.html`
- `incidents/templates/incidents/dashboard.html`
- `incidents/templates/incidents/operational_base.html`
- `incidents/templates/incidents/report_detail.html`
- `incidents/templates/incidents/report_list.html`
- `incidents/test_dashboard_http.py`
- `incidents/test_operational_dashboard.py`
- `incidents/tests.py`
- `incidents/views.py`
