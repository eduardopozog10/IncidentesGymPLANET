# Lote 11 — Solicitudes inteligentes y documentos privados

Fecha de revisión: 21 de octubre de 2026. Entrega local para revisión, sin commit ni push.

## Estado inicial y diagnóstico

La implementación comenzó en `main`, commit `5f61624`, con Git limpio y origin
`eduardopozog10/IncidentesGymPLANET`. Los lotes 10 y 10.5 estaban publicados;
no fue necesario reconstruirlos. Se revisaron modelos, procesador conversacional,
Telegram, permisos, dashboard y outbox antes de editar.

La principal tenía 41 migraciones aplicadas hasta `0023_inventory_operations`,
26 tablas y cinco reportes: `SC-000001`, `SC-000002`, `GP-000003`, `GP-000004`
y `GP-000005`. Se tomó una huella de filas, esquema, restricciones y secuencias
para comprobar su conservación después de las verificaciones.

Las solicitudes anteriores conservaban descripción y justificación, pero carecían
de líneas estructuradas, catálogo y recepción de formularios. El envío de correo
ya disponía de outbox persistente; se amplió ese mecanismo.

## Catálogo y modelos

`incidents/request_catalog.py` contiene los 34 nombres oficiales y sus
presentaciones, en el orden solicitado, con códigos `GP-ASEO-001` a `GP-ASEO-034`.
Se consultó el Word de referencia sin importar su pedido ni sus cantidades.
Las unidades que el documento no define permanecen vacías. Los artículos de
oficina y otros materiales se admiten fuera del catálogo, con identificación expresa.

El comando `seed_request_products` simula por defecto; únicamente `--apply`
permite escribir, en una transacción. Usa los códigos como identidad, conserva
registros existentes y rechaza discrepancias sin aplicar cambios parciales.
No se ejecutó la carga en la principal.

| Modelo | Finalidad |
| --- | --- |
| `RequestProduct` | Código único, nombre, presentación, unidad documentada, actividad y fechas. |
| `ProductAlias` | Sinónimos editables; unicidad normalizada por producto. Un alias compartido exige aclaración. |
| `RequestItem` | Producto opcional, nombres original/oficial, cantidad decimal, unidad, presentación y marca fuera de catálogo; snapshot protegido de la solicitud confirmada. |
| `RequestDocument` | Original privado, mensaje/borrador/sucursal/reporte, nombre seguro, MIME, tamaño, SHA-256 y resultado de extracción. |

Se conservan los campos y relaciones históricos. Las solicitudes de servicios
siguen sin necesitar artículos. No se modificaron los cinco estados operativos,
inventario, mantenimiento ni los códigos SC existentes.

## Migraciones preparadas

- `0024_request_products_documents`: cuatro tablas (`request_products`,
  `product_aliases`, `request_items`, `request_documents`), claves foráneas,
  cantidad positiva, coherencia de referencia de catálogo, posición única y
  triggers de integridad e inmutabilidad.
- `0025_protect_request_originals`: protección SQL adicional contra eliminación
  de originales, incluidos los de borradores cancelados o sustituidos.

Los triggers impiden asociar artículos a incidencias técnicas, cambiar líneas
confirmadas, cruzar originales entre cuentas/sucursales o cambiar un reporte de
forma incompatible con sus originales. Las claves foráneas protegen el historial.
Las migraciones `0001`–`0023` no se reescribieron. Las nuevas se aplicaron solamente
en `test_incidentes_gymplanet_db` mediante el runner de pruebas. Quedan pendientes
en la principal; requieren revisión y autorización independiente.

## Conversación y cantidades

La clasificación distingue fallas técnicas, compras/reposición, oficina y servicios.
Una intención ambigua recibe una pregunta breve y admite corrección explícita de
tipo. La sucursal continúa dependiendo de la cuenta corporativa autorizada.

Una solicitud de productos necesita al menos una línea con cantidad positiva.
Las cantidades provienen del texto o de celdas literales del formulario; no del
tamaño del envase ni de una propuesta libre del modelo. Se aceptan hasta tres
decimales; cero, negativos, rangos y cantidades contradictorias exigen corrección.
No se convierten unidades sueltas en paquetes. Las coincidencias se basan en
nombres/códigos/sinónimos y coincidencias literales acotadas; las alternativas
ambiguas se consultan al reportante.

Se pueden agregar o quitar artículos, corregir cantidades, vaciar la lista,
corregir motivo/descripción/prioridad, sustituir el documento y cancelar.
Se mantienen los demás datos del borrador y no se duplican líneas por corrección.
Una justificación natural de falta de stock se conserva; un propósito genérico
no se inventa como impacto. La prioridad es explícita, con explicaciones de
reposición y continuidad operativa.

La creación requiere un resumen vigente y un «Sí» final. Un cambio vuelve a
pedir confirmación; «No» y `/cancelar` no crean reportes. Para extracción visual
se requieren dos pasos: «Revisé el archivo» y después la confirmación final.
La transacción y los bloqueos existentes protegen contra confirmaciones concurrentes.

## Formatos y límites del piloto

| Formato | Implementado y límites |
| --- | --- |
| DOCX | Extracción estructurada de tablas de dos pares producto/cantidad, celdas combinadas, texto fragmentado, metadatos y secciones de oficina. Ignora guiones y celdas vacías; no interpreta instrucciones del documento. |
| PDF digital | Extracción acotada de texto con estructura de columnas o pares nombre/cantidad. No es un reconocedor universal de tablas. |
| PDF escaneado | Solicita documento legible o ingreso manual; no se implementó OCR de PDF. |
| JPG/JPEG/PNG | Validación real y conservación del original. Sin visión habilitada solicita ingreso manual. Adaptador opcional Gemini de visión, probado con respuestas simuladas y revisión humana obligatoria. |
| Servicios | Mantiene el flujo por texto. Un DOCX/PDF requiere contexto explícito de servicio y etiqueta «Servicio:» o «Trabajo solicitado:». Una imagen puede conservarse como referencia sin afirmar que fue leída. |

Límites: 5 MB por original, 20 artículos por solicitud, cinco originales por
borrador, seis páginas PDF, 12 megapíxeles y una imagen estática. El análisis
estructurado tiene un timeout predeterminado de 10 segundos (tope de 15).
La descarga valida tamaño antes y durante la transferencia. DOCX limita entradas,
tamaño expandido, compresión, XML y filas; no extrae archivos al disco ni ejecuta
macros. PDF rechaza cifrado y contenido activo y limita descompresión/complejidad.
Los documentos incompatibles o inciertos conservan el borrador y permiten reintentar.

La extracción corre en un subprocess acotado; no constituye una sandbox del
sistema operativo. Mantener actualizadas las dependencias de análisis sigue
siendo necesario.

## Archivos, correo y seguridad

Los originales se guardan fuera de `MEDIA_URL`, por defecto en
`env/request-originals`, excluido de Git, con nombres internos UUID y sin URL
pública. Se conserva un nombre legible saneado. SHA-256 y tamaño se verifican
antes de confirmar, descargar y adjuntar. La política de ACL de Windows depende
del directorio anfitrión; los modos POSIX se solicitan donde son aplicables.

El receptor existente verifica la cuenta antes de descargar, serializa el borrador
y deduplica el mensaje antes de volver a procesar el archivo. Solo descarga rutas
de la API de Telegram, sin redirecciones ni URL suministrada por el usuario.
Un documento no determina identidad, permisos, destinatario ni confirmación.
Los errores no exponen tokens ni detalles técnicos internos al reportante.

Al confirmar se crean reporte, líneas, relaciones de originales y notificación
dentro de la transacción existente. La outbox captura nombres, cantidades,
presentaciones, categoría, justificación y referencias/hash de originales. El envío
se programa después del commit. Se conservan deduplicación, intentos, reintentos
y tratamiento de entrega SMTP incierta.

El correo tiene tabla HTML y texto plano, prioridad, estado, fecha y botón
«Ver reporte». Adjunta los bytes originales verificados; no genera un documento
sustituto. Las solicitudes de servicio no muestran una tabla vacía. Los mensajes
sin documento no llevan un adjunto de solicitud. Los destinatarios y remitente
siguen obteniéndose de settings; no se incorporaron direcciones fijas nuevas.
Una sustitución conserva todos los originales en el historial y adjunta solamente
el vigente. Los archivos se conservan para la outbox pendiente.

## Dashboard y administración

`/dashboard/products/` permite consultar, buscar, paginar, crear, editar y
desactivar productos y sus sinónimos. Django Admin incluye catálogo, alias y
lectura protegida de líneas/originales. No se permite eliminar productos
referenciados por solicitudes históricas.

El detalle de solicitud incorpora tabla de artículos y originales descargables,
conservando responsable, historial y acciones. La descarga comprueba permisos en
backend, exige un documento vinculado a reporte y verifica su integridad. Usa
`Content-Disposition: attachment`, `nosniff` y caché privada deshabilitada.
No hay enlaces públicos ni tokens de acceso. Actualmente se permite a los roles
operativos globales autorizados; no se agregó acceso implícito a roles de sucursal.

## Pruebas y verificación

Resultado final: **545 pruebas aprobadas, cero fallos y cero errores**, en
**130,398 segundos** de ejecución de la suite (135,195 segundos del comando
completo). Las 103 pruebas específicas de documentos, solicitudes y regresiones
también aprobaron antes de la suite completa.

| Verificación | Resultado |
| --- | --- |
| `manage.py check` | Sin problemas. |
| `makemigrations --check --dry-run` | Sin cambios detectados. |
| `test incidents --keepdb` | 545/545 aprobadas en la base de pruebas. |
| `migrate --plan` | Solo 0024 y 0025 pendientes; conexión de solo lectura. |
| `sqlmigrate incidents 0024/0025` | SQL y restricciones inspeccionados, sin ejecutar. |
| `uv sync --locked --offline` | Lock coherente; 35 paquetes comprobados, sin cambios de dependencias. |
| `git diff --check` | Correcto; además se revisaron espacios, sintaxis y secretos en los archivos nuevos. |

Se probaron clasificación, catálogo/carga, cantidades, presentaciones, alias,
correcciones, oficina, compatibilidad histórica, DOCX/PDF/imágenes sintéticos,
archivos inválidos, identidad, duplicados, confirmación concurrente, cancelación,
integridad SQL, originales exactos, correo/outbox/reintentos, permisos y CSRF.

Se recuperó una fixture mínima y sintética para las regresiones del lote anterior:
el módulo publicado dependía de un JSON no versionado. No contiene documentos
privados ni identificadores reales. Se actualizaron expectativas de solicitudes
genéricas para exigir artículos/cantidades; no se eliminaron pruebas anteriores.

`manage.py test` utiliza por defecto `config.test_settings`: Mock, tokens vacíos,
correo en memoria y notificaciones automáticas deshabilitadas. El runner valida
el nombre exacto de la base de pruebas y la conexión local, usa almacenamiento
temporal y bloquea sockets externos durante la suite. Las pruebas de transportes
y proveedores usan simulaciones; los tests de notificación habilitan el backend
en memoria de forma explícita.

Comandos de validación:

```powershell
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test incidents --keepdb
git diff --check
```

La revisión visual utilizó Django real, PostgreSQL de pruebas y un
`StaticLiveServerTestCase` con datos sintéticos. Se verificaron catálogo de 34
productos, paginación, búsqueda, detalle con cuatro líneas y original privado,
con estilos y marca cargados y sin desbordamiento horizontal en escritorio.
Se cerró la sesión temporal y el runner retiró los datos. No se realizó una
revisión móvil en este lote.

## Archivos principales

- Esquema: `incidents/models.py`, `incidents/migrations/0024_*`, `0025_*`.
- Catálogo: `request_catalog.py`, `management/commands/seed_request_products.py`.
- Archivos: `document_worker.py`, `private_files.py`,
  `services/request_documents.py`, `services/incoming_file_service.py`.
- Conversación: `services/request_items.py`, `product_conversation.py`,
  `gymplanet_conversation.py`, `conversation_evidence.py`,
  `conversation_controls.py`, `incident_processor.py`.
- Integración: `messaging/telegram_provider.py`, `telegram_update_handler.py`,
  `operational_signals.py`, `services/incident_notification_service.py`.
- Interfaz: `request_views.py`, `admin.py`, templates de catálogo, detalle,
  navegación y correo; `config/urls.py`.
- Configuración pública: `config/settings.py`, `test_settings.py`,
  `test_runner.py`, `manage.py`, `pyproject.toml`, `uv.lock`.
- Pruebas: `test_lot11_requests.py`, `test_request_download.py`, fixture mínima y
  adaptación de `test_telegram_real_regressions.py`, `test_corporate_identity.py`
  y `test_gymplanet_conversation.py`.
- Documentación: este informe y enlace en README.

## Dependencias y pendientes

Se agregaron únicamente `pypdf` 6.21.1 y Pillow 13.0.0 al entorno y al lockfile.
Son bibliotecas para PDF e imágenes; DOCX usa ZIP/XML de la biblioteca estándar.
No se agregó OCR pesado, otro sistema de correo, WhatsApp ni OpenAI.
Referencias técnicas: [PdfReader](https://pypdf.readthedocs.io/en/latest/modules/PdfReader.html),
[seguridad de Pillow](https://pillow.readthedocs.io/en/stable/handbook/security.html)
y [SDK Google GenAI](https://googleapis.github.io/python-genai/index.html).

La visión es optativa (`REQUEST_DOCUMENT_VISION_ENABLED=False` por defecto) y
requiere el proveedor Gemini ya autorizado. No se verificó con llamadas reales.
Los formatos complejos o inciertos requieren ingreso manual. La política de
retención de borradores cancelados y el eventual retiro de archivos huérfanos
tras una interrupción abrupta quedan por definir; no se programaron borrados.
Se recomienda incorporar los originales privados al respaldo operativo.

## Prueba manual posterior con Telegram

1. Revisar esta entrega y autorizar por separado migraciones y carga del catálogo
   en una base QA designada. No iniciar el bot sobre la principal sin las nuevas
   migraciones. La carga se revisa primero con `seed_request_products` sin `--apply`.
2. Preparar un proceso QA aislado, con la cuenta corporativa de prueba autorizada,
   correo en memoria, sin receptor concurrente sobre la principal y sin cambiar
   el `.env` compartido. Confirmar identidad SQL antes de iniciar.
3. Solicitar por texto «2 bidones de cloro, 3 paquetes de bolsas grandes y
   1 bidón de jabón». Indicar motivo y prioridad; revisar que no exista reporte
   antes del «Sí». Corregir cantidades, quitar/agregar una línea y volver a confirmar.
4. Repetir con un DOCX sintético de cantidades elegidas para QA. Comprobar detalle,
   outbox y adjunto idéntico al original, sin envíos SMTP. Probar cancelación y
   archivo inválido, y que un mensaje duplicado no cree otro reporte.
5. Probar PDF digital y una imagen. Sin visión, esta última debe solicitar ingreso
   manual. Una prueba real de visión necesita habilitación independiente y siempre
   revisión de lectura y confirmación final.
6. Revisar catálogo en `/dashboard/products/`, detalle y descarga autenticada.
   La descarga sin permisos debe rechazarse. Detener el receptor QA al terminar.

No se enviaron mensajes reales por Telegram ni correos SMTP, ni se llamó a Gemini
real durante este lote. No se modificó `.env`, no se cargaron productos ni pedidos
en la principal y no se ejecutaron migraciones allí. No hubo commit ni push.

Estado final comprobado mediante consultas de solo lectura:

| Base | Migraciones | Tablas | Reportes | Estado |
| --- | ---: | ---: | ---: | --- |
| `incidentes_gymplanet_db` | 41 | 26 | 5 | Misma huella de filas/esquema/restricciones/secuencias que al inicio; 0024 y 0025 pendientes. |
| `test_incidentes_gymplanet_db` | 43 | 30 | 0 | 0024 y 0025 aplicadas por el runner; sin productos, originales ni sucursales sintéticas residuales. |

Git conserva los cambios locales del lote para revisión: 42 archivos nuevos o
modificados, incluidos documentación, fixture y pruebas. Rama y commit de partida
siguen siendo `main` / `5f61624`; `.env` continúa ignorado. No se accedió a bases
de Canal 13 ni TVTEL.

Mensaje de commit sugerido tras revisión:
`Implementa solicitudes con catálogo, documentos privados y adjuntos en la outbox`.
