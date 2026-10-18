# Lote 7 — Marca, conversaciones e inventario

## Estado de la entrega

Trabajo retomado sobre `main`, commit `db4d971`, del repositorio independiente
`eduardopozog10/IncidentesGymPLANET`. Se conserva la estructura del lote 6,
el acceso exclusivo a superusuarios activos, CSRF, las transacciones, la sucursal
derivada de la cuenta autorizada y la confirmación final obligatoria.
No se modifica Django Admin, modelos, dependencias, credenciales ni migraciones.

## Identidad investigada y aplicación

Referencia exclusiva: [Gym Planet Chile](https://gymplanet.cl/), inspeccionada
el 18 de octubre de 2026, incluyendo HTML, recursos, CSS y apariencia real.
El [CSS publicado por el sitio](https://cdn.prod.website-files.com/69d954c1e45a1e93797fb0f7/css/gym-planet.webflow.shared.089a19125.css)
define `accent-primary: #ab4e3c` y `accent-primary-hover: #ff7e5f`.
También define blanco `#ffffff` y negro inverso `#0a0a0a`.
Estos valores se verificaron en sus declaraciones, no por estimación visual.

El sitio utiliza títulos grandes condensados, superficies blancas/crema y negras,
logotipo terracota, navegación oscura y acciones redondeadas. La interfaz
administrativa conserva su distribución, tablas y tarjetas: adapta esa identidad
a un dark mode para trabajo operativo, sin copiar el diseño de marketing.

| Token | Valor | Procedencia |
| --- | --- | --- |
| `brand-primary` | `#ab4e3c` | CSS oficial |
| `brand-primary-hover` | `#ff7e5f` | CSS oficial |
| `brand-primary-active` / `brand-primary-soft` | `#863d30` / `#32221f` | Adaptación administrativa |
| `brand-dark` | `#702f29` | Adaptación administrativa |
| `background-main` / `background-sidebar` | `#111216` / `#151619` | Adaptación administrativa |
| `background-card` / `background-elevated` | `#1b1d23` / `#24262d` | Adaptación administrativa |
| `text-primary` / `text-secondary` | `#f7f5f3` / `#b7b2ae` | Adaptación administrativa |
| `border-default` / `border-strong` | `#39343a` / `#605357` | Adaptación administrativa |
| `focus-ring` / `brand-link` | `#ffa68d` / `#f4aa97` | Adaptación para contraste |

Se centralizan los colores en `dashboard.css`. La navegación, botones, enlaces,
filtros, gráficos, tarjetas, foco y selección utilizan esos tokens. El botón
terracota tiene texto blanco; su hover coral usa texto oscuro para mantener
contraste. El verde se conserva solo como significado positivo, no corporativo.
Las prioridades Baja/Media/Alta/Crítica usan verde discreto/ámbar/naranja/rojo
rosado; los estados conservan etiquetas y colores independientes. No hay estados
nuevos. La verificación visual no equivale a una auditoría WCAG completa.

Logo y favicon oficiales locales, sin deformaciones, con procedencia y hashes en
[brand/README.md](../incidents/static/incidents/brand/README.md). El logo a color
descansa sobre blanco; se conserva también la variante blanca. El CSS oficial
declara Touche para cuerpo y Sol para títulos. No se descargan ni redistribuyen
esas fuentes sin confirmar su licencia: se usa `Segoe UI`, `system-ui`, sans-serif.

## Conversación y control de campos

- Nuevas reglas compartidas para expresiones chilenas literales: ruido raro,
  funcionamiento intermitente, duchas que no funcionan, torniquete bloqueado,
  climatización que no enfría, fuga de agua y equipo detenido. «Está mala» sigue
  requiriendo aclaración, sin inventar diagnóstico.
- Se distinguen negaciones e hipótesis en disponibilidad, riesgo y prioridad.
  Una necesidad de revisar una máquina averiada se clasifica como incidencia;
  faltantes, reposición y mantenimiento preventivo se reconocen como solicitudes.
  Responder una pregunta pendiente conserva el tipo del borrador.
- Correcciones determinísticas de prioridad, equipo, cantidad inequívoca e
  impacto negativo; «Corrige el síntoma» solicita el dato nuevo. Se conservan
  los demás campos, se recalculan requisitos y se exige confirmar otro resumen.
  Si la cantidad original es ambigua se pide una descripción completa.
- Un síntoma contradictorio requiere decidir si se corrige; esa decisión no
  crea el reporte. Se mantiene la confirmación final posterior.
- Empezar otro reporte con uno pendiente conserva el anterior y explica cómo
  continuar, corregir o cancelar. Repetir pregunta, confirmar, cancelar y varias
  correcciones claras se resuelven sin nuevas llamadas a Gemini.
- Los ejemplos visibles se orientan a gimnasios; se mantiene la taxonomía
  audiovisual heredada necesaria para compatibilidad, sin borrar históricos.

La sucursal nunca se cambia por texto ni por una salida del modelo. Django
resuelve los catálogos y determina los campos obligatorios; la IA no recibe
autorizaciones. Continúan válidos los controles y pruebas heredados de cuenta,
duplicados, mensajes rápidos, cancelación, prioridad y confirmación.

## Gemini y recuperación

Se conserva el límite existente de dos intentos, 30 segundos para el primero y
45 segundos totales. Se reconocen errores de red y códigos transitorios,
incluidos 429/500/503. Se validan tipos de campos, intención y tipo de reporte;
vacíos, JSON inválido y salidas incompatibles conservan el borrador.
Los errores del SDK se traducen a mensajes seguros; logs y trazas guardadas no
incluyen su cuerpo ni los mensajes privados. El contexto enviado se limita a
campos operativos y la sucursal autorizada, sin IDs ni metadatos internos.

Después de dos fallos de extracción y un «Continuar» explícito, se permite una
pasada de recuperación con Mock sobre los mensajes conservados. No se inventan
campos ni se guarda automáticamente. Si falta información, se vuelve a preguntar;
si está completa, se presenta el resumen para confirmación. No es una garantía
de comprensión de cualquier frase ni un reemplazo general del modelo real.

## Solicitudes e inventario

Nuevo comando `seed_gymplanet_request_categories`: propone diez categorías
adaptables de reposición, compra, aseo, prevención, reparación, infraestructura,
climatización, servicios, suministros y otros. Simula por defecto. `--apply`
requiere decisión explícita, usa transacción y cancela ante discrepancias sin
sobrescribir nombres o estados existentes; busca por código y detecta nombres
equivalentes normalizados. Se verifica idempotencia y reversión completa.

```powershell
# Solo simulación; no se ejecutó sobre la principal en esta entrega.
uv run python manage.py seed_gymplanet_request_categories
# Tras revisar el catálogo y autorizar su carga, en otra ejecución:
# uv run python manage.py seed_gymplanet_request_categories --apply
```

Tipo textual y categoría formal permanecen separados. El asistente solo enlaza
categorías activas existentes compatibles; preserva el texto si no las hay.

El inventario conserva filtros por sucursal, estado, categoría, área y búsqueda.
Agrega `/dashboard/assets/<id>/` con detalle, incidencias paginadas y mantenimiento
registrado. Los enlaces funcionan desde nombre y contador del activo. La nueva
vista es de solo lectura y conserva la autorización del dashboard.
Reportar equipo libre sigue siendo válido; ante coincidencias ambiguas se puede
indicar el código o «Continuar sin activo». No se asocia arbitrariamente ni se
actualiza automáticamente el estado de un activo por mencionar una falla.

## Verificación e integridad

Las pruebas se ejecutan exclusivamente en `test_incidentes_gymplanet_db`, con
Mock, claves externas vacías, webhook desactivado y correo en memoria mediante
variables solo del proceso. No se modifica `.env` ni se envían mensajes reales.
Se conservan las 310 pruebas previas y se agregan casos de lenguaje, correcciones,
recuperación, catálogo, marca, inventario y confirmación simultánea desde dos
conexiones: esta última debe producir exactamente un reporte.

Comprobaciones: `check`, `makemigrations --check --dry-run`, suite completa con
`--keepdb` y `git diff --check`. Resultado final: **345 pruebas aprobadas (310
existentes y 35 nuevas), 0 fallos y 0 errores, en 73,011 segundos**. `check` no
detecta problemas; `makemigrations` no detecta cambios y `git diff --check` pasa.
No se generan migraciones: 0001–0018 quedan intactas.

Revisión visual contra servidor HTTP real Django/PostgreSQL, autenticación normal
con usuario temporal sintético y recursos estáticos locales. Se revisaron centro
de control, solicitudes, inventario y detalle de activo en escritorio y móvil de
390 px. Sin errores de consola ni desbordamiento horizontal de página; tablas y
navegación móvil desplazan dentro de su contenedor. Las capturas ignoradas están
en `env/lot7-desktop.png`, `env/lot7-mobile.png` y `env/lot7-requests-mobile.png`.
Los datos y la sesión temporales se retiraron exclusivamente de la base de pruebas.

La principal conserva 36 migraciones, 22 tablas y los tres reportes `SC-000001`,
`SC-000002` y `GP-000003`. Se comparan contenido, esquema, restricciones y
secuencias mediante conexiones de solo lectura antes/después de las pruebas.
La prueba queda independiente con 36 migraciones y 22 tablas, sin reportes ni
catálogos operativos al terminar. No se consultan bases de otros proyectos.

## Revisión local y pendientes

Desde la raíz, con el entorno virtual y PostgreSQL existentes, usar variables
temporales de PowerShell para evitar proveedores externos, sin editar `.env`:

```powershell
$env:AI_PROVIDER = 'mock'
$env:TELEGRAM_WEBHOOK_ENABLED = 'False'
$env:TELEGRAM_AUTO_REGISTER = 'False'
$env:EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
uv run python manage.py check
uv run python manage.py runserver 127.0.0.1:8000
```

Iniciar sesión con el superusuario existente en `/admin/login/` y abrir
`http://127.0.0.1:8000/dashboard/`. Ver incidencias, solicitudes, detalles y filtros;
el inventario principal muestra vacío hasta que se registren equipos autorizados.
No iniciar el bot. No es necesario migrar ni cargar categorías para revisar esta
entrega. Los cambios de estado de reportes son escrituras reales: la inspección
inicial puede limitarse a navegar. Cerrar esa terminal retira sus variables.

Pendiente: aceptar la marca y condiciones de uso de sus activos, revisar y
autorizar el catálogo, y validar conversaciones libres con usuarios de gimnasio.
Las llamadas reales a Gemini/Telegram quedan para una prueba funcional autorizada;
este lote valida su contrato mediante dobles. Las reglas locales son deliberadamente
acotadas: no cubren todas las contradicciones ni todas las formulaciones del español.
Permisos de supervisores y modificación operativa del inventario no se amplían.
No se implementa OpenAI ni se realiza commit/push.

## Archivos para revisión

23 archivos (13 modificados y 10 nuevos):

- Documentación: `README.md`, `docs/lot7-brand-conversation.md` y
  `incidents/static/incidents/brand/README.md`.
- Rutas y vistas: `config/urls.py`, `incidents/dashboard_views.py`.
- Proveedores: `incidents/ai/gemini_provider.py`, `incidents/ai/mock_provider.py`.
- Servicios: `incidents/services/gymplanet_conversation.py`,
  `incidents/services/incident_processor.py`,
  `incidents/services/incoming_message_service.py`,
  `incidents/services/conversation_controls.py`,
  `incidents/services/gym_language.py`.
- Estilos y marca: `incidents/static/incidents/dashboard.css`,
  `incidents/static/incidents/brand/gymplanet-logo-color.avif`,
  `incidents/static/incidents/brand/gymplanet-logo-white.avif`,
  `incidents/static/incidents/brand/gymplanet-favicon.png`.
- Plantillas en `incidents/templates/incidents/`: `operational_base.html`,
  `assets.html`, `asset_detail.html`, `_pagination.html`, `report_list.html`.
- Comando: `incidents/management/commands/seed_gymplanet_request_categories.py`.
- Pruebas: `incidents/test_lot7.py`.


## Corrección final: redacción y reconocimiento de cambios

La sustitución anterior conservaba el artículo de la descripción e insertaba un
nombre de equipo que también contenía artículo. Se reemplaza ahora una única
frase nominal identificable, con límites de palabra y tratamiento local del
artículo. No se aplica una limpieza global de palabras repetidas. Si no hay una
coincidencia única, se conserva la descripción original. No se intenta reconstruir
libremente el texto ni cambiar datos ajenos al equipo corregido.

Tras persistir un cambio válido del borrador, la respuesta reconoce el campo
actualizado y continúa con la pregunta pendiente o el resumen para confirmación.
Los cambios ambiguos, rechazados por catálogo o sin modificación efectiva no
reciben ese reconocimiento. Se cubren equipo, área/ubicación, síntoma, descripción,
impacto, cantidad y prioridad. La confirmación verbal nunca crea por sí sola el
reporte: se conserva la confirmación final y la cancelación.

Archivos de esta corrección: `incidents/services/conversation_controls.py`,
`incidents/services/incident_processor.py`, `incidents/test_lot7.py` y este informe.
Se agregan cuatro métodos de prueba con variantes que cubren los diez escenarios
solicitados. Se mantienen todos los cambios locales anteriores del lote 7.

Validación de la corrección final: **349 pruebas aprobadas, 0 fallos y 0 errores,
en 72,188 segundos**. `check`, `makemigrations --check --dry-run` y
`git diff --check` correctos. La huella de la principal no cambió; permanecen
los tres reportes protegidos. La base de pruebas terminó sin datos operativos.
No se llama a Gemini ni se envían mensajes reales durante estas pruebas.
