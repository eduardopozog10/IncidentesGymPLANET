# IncidentesGymPLANET

El [lote 10: inventario, mantenimiento y conversación](docs/lot10-inventory-conversation.md)
extiende los módulos existentes. Su migración 0023 requiere revisión y autorización
antes de utilizar esta versión contra la principal.


El [lote 9: estados y correo](docs/lot9-states-email.md) consolida cinco estados y
una outbox durable. Revisar y autorizar 0021/0022 antes de usar esta versión con
la principal; SMTP queda desactivado por defecto.


El [lote 8: gestión operativa](docs/lot8-operations.md) incorpora el rol general,
asignaciones, seguimiento e historial. Sus migraciones 0019/0020 están pendientes
de autorización en la principal; revisar ese informe antes de iniciar esta versión.

Sistema en desarrollo para gestionar incidencias y solicitudes operativas de las sucursales de Gym Planet Chile.

La base funcional de inventario, mantenimiento y solicitudes se documenta en
[docs/inventory-maintenance.md](docs/inventory-maintenance.md), incluidas las reglas
de historial, traslados, prioridades y validación. Las nuevas migraciones de esa
etapa (0013/0014) ya se aplicaron a la base principal con autorización.

El [flujo conversacional Gym Planet](docs/gymplanet-conversation.md) describe
la sucursal derivada de la cuenta corporativa, incidencias y solicitudes,
confirmación, prioridad explícita y recuperación ante errores de IA.

El [lote 6: interfaz operativa](docs/lot6-operational-dashboard.md) incorpora un
centro de control nuevo, incidencias, solicitudes, sucursales, inventario, filtros,
detalles y seguimiento con protección CSRF. Incluye la revisión visual y las
pruebas HTTP reales sobre Django y PostgreSQL.

La [administración de cuentas y controles de acceso](docs/corporate-accounts-security.md)
describe la vinculación corporativa y el acceso exclusivo de superusuarios activos.
El [lote 7: marca, conversaciones e inventario](docs/lot7-brand-conversation.md)
documenta los recursos oficiales, la paleta verificada, las correcciones del
asistente, el catálogo propuesto y la revisión visual con Django real.
La principal ya tiene **36 migraciones y 22 tablas**, incluida 0018, aplicada
antes de este lote. Conserva `SC-000001`, `SC-000002` y `GP-000003`.
El lote 7 no agrega migraciones ni carga categorías en la principal.
Los apartados de la primera tarea y el informe del lote 6 conservan su contexto
histórico; el estado y los comandos actuales están en el informe del lote 7.

## Independencia de Secuoya

Esta es una copia independiente de IncidentesCanal13 / Secuoya Chile. El remote esperado es `https://github.com/eduardopozog10/IncidentesGymPLANET.git`.

Se reutilizan Django, PostgreSQL, la API REST, el dashboard, los proveedores de IA, Telegram y el procesador conversacional. La interfaz operativa ya utiliza Gym Planet. Se conservan categorías audiovisuales y códigos `SC-` históricos por compatibilidad; Django Admin mantiene su diseño existente. El paquete sigue llamándose `tvtel-incidentes` en `pyproject.toml` y `uv.lock`.

No compartir bases, roles PostgreSQL, secretos, bots ni destinatarios reales con Secuoya. No copiar su `.env` ni datos operativos. Trabajar exclusivamente en esta carpeta; no modificar IncidentesCanal13 ni otros proyectos.

## Requisitos y estructura

- Python 3.13 o superior, según `pyproject.toml` y `uv.lock`.
- `uv` y Git.
- PostgreSQL independiente para desarrollo y pruebas; entorno local verificado: PostgreSQL 18 en `localhost:5432`.

Las dependencias declaradas son Django 5.2, Django REST Framework, django-environ, psycopg, google-genai y httpx. Las versiones resueltas están en `uv.lock`.

- `config/`: configuración Django y rutas.
- `incidents/models.py`: reportantes (`Technician`), canales, incidencias, mensajes y borradores.
- `incidents/migrations/`: diez migraciones heredadas (`0001` a `0010`). `0006` transforma datos históricos de WhatsApp; no ejecutarla sobre la base anterior.
- `incidents/services/`: recepción, procesamiento conversacional, borradores y notificaciones.
- `incidents/ai/`: proveedores `mock` y Gemini.
- `incidents/messaging/` y `incidents/management/commands/`: integración y comando de Telegram.
- `incidents/templates/` y `incidents/static/`: dashboard y recursos.
- `incidents/tests.py`, `incidents/test_*.py` e `incidents/messaging/test_*.py`: pruebas de API, conversaciones, prioridades, aclaraciones, timeouts y autorregistro.

## Instalación

Ejemplos para PowerShell desde la raíz de este repositorio. Verificar la carpeta, el remote y los cambios pendientes antes de editar:

```powershell
Get-Location
git rev-parse --show-toplevel
git remote -v
git status
```

Instalar las dependencias conservando el lockfile, sin iniciar Django:

```powershell
uv sync --locked
```

Este comando prepara el entorno virtual y puede descargar dependencias; no prepara PostgreSQL. No se ejecutó durante la primera tarea.

## Configuración local

`.env.example` es una plantilla pública. El entorno local ya tiene un `.env` privado e ignorado, una base principal y otra de pruebas independientes. Esta entrega no modifica credenciales ni roles.

Para una instalación nueva, preparar un `.env` exclusivo de Gym Planet con secretos nuevos. Revisar también las variables del proceso: `config/settings.py` lee `.env` y las variables existentes en el entorno pueden prevalecer. Cambiar la plantilla no modifica una configuración privada existente.

| Variable | Configuración inicial |
| --- | --- |
| `POSTGRES_DB` | `incidentes_gymplanet_db` |
| `POSTGRES_USER` | `incidentes_gymplanet_app` |
| `POSTGRES_PASSWORD` | Marcador; reemplazar por un secreto nuevo en la configuración privada |
| `POSTGRES_HOST` | `localhost`; confirmar la instancia antes de conectar |
| `POSTGRES_PORT` | `5432` en el entorno local verificado; la plantilla histórica aún contiene `5433` provisional |
| `DJANGO_SECRET_KEY` | Marcador; generar una clave local única |
| `AI_PROVIDER` | `mock`, sin llamadas al proveedor de IA |
| `GEMINI_API_KEY` / `TELEGRAM_BOT_TOKEN` | Vacíos |
| `TELEGRAM_AUTO_REGISTER` | `False` |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` |
| `INCIDENT_NOTIFICATION_EMAIL` | Vacío; no se envían notificaciones sin destinatario |

El backend de consola evita envíos SMTP; mostrará el correo en la terminal si posteriormente se configura un destinatario de prueba. `DJANGO_DEBUG=True` es solo para desarrollo local. `.gitignore` excluye `.env`, `.env.*` (excepto `.env.example`), entornos virtuales y archivos generados; no subir credenciales.

Antes de ejecutar Django, verificar el puerto, la instancia y los cinco valores `POSTGRES_*` efectivos, y limitar el rol a las bases de Gym Planet. `config/settings.py` mantiene SMTP como valor predeterminado si falta `EMAIL_BACKEND`; definir explícitamente el backend de consola en la configuración efectiva. `mock` no desactiva por sí solo Telegram ni el correo.

## Pruebas y ejecución posteriores

Las pruebas inspeccionadas usan `django.test.TestCase` y crean registros: requieren PostgreSQL. Antes de ejecutarlas falta instalar dependencias, preparar la configuración privada independiente, aprovisionar base y rol exclusivos, y verificar permisos para crear y eliminar `test_incidentes_gymplanet_db`. No usar bases compartidas ni credenciales de Secuoya. Revisar que los proveedores y transportes externos estén simulados o desactivados.

Solo después de esa preparación, ejecutar:

```powershell
uv run --locked python manage.py test incidents
```

Para seleccionar los flujos conversacionales:

```powershell
uv run --locked python manage.py test incidents.test_priority_flow incidents.test_guided_clarification incidents.test_conversation_data_quality incidents.test_ai_timeout_flow
```

El runner crea la base de pruebas, aplica migraciones allí y la elimina al terminar. No ejecutar estos comandos en la primera tarea. Las expectativas de Secuoya aún presentes no validan la adaptación a gimnasios. Hay pruebas de autorregistro Telegram en dos ubicaciones cuya posible duplicación debe revisarse después.

Tras preparar y migrar exclusivamente la nueva base en una tarea posterior, el arranque local será:

```powershell
uv run --locked python manage.py runserver 127.0.0.1:8000
```

Rutas principales: `/dashboard/`, `/admin/` y `/api/v1/`. No iniciar el servidor, Telegram ni servicios externos durante esta revisión inicial. Revisar autenticación, permisos y seguridad de endpoints antes de exponer el sistema.

## Alcance y verificación de la primera tarea

Solo se actualizan `.env.example` y este README. No se modifican configuración privada, modelos, migraciones, dependencias ni lockfile. No se ejecutan migraciones, pruebas con PostgreSQL ni servicios externos. Verificación estática:

```powershell
git diff -- .env.example README.md
git diff --check
git status --short
```

Revisar los cambios en GitHub Desktop antes de hacer commit o push.
