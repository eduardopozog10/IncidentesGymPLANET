# Lote 9 — Estados definitivos y notificaciones

## Estado inicial y preservación

Partida: `main`, commit `1971e21`, remote Gym Planet correcto. Se conservaron los
cuatro archivos modificados manualmente y la migración 0021 local: modelo de cinco
estados, pendientes Reportado/En atención, indicador Cancelados, motivo obligatorio
al cancelar y formulario de estado. No se restauró ni descartó trabajo.

Estados vigentes exclusivos de Incident: `reported`, `in_progress`, `resolved`,
`closed`, `cancelled`. El servicio permite cambios directos entre cualquiera de
ellos a operations_manager y superusuarios activos. Resuelto exige solución;
Cancelado exige motivo. Cerrado no requiere resolución anterior ni inventa fecha.
Reabrir conserva el historial y la asignación. Repetir el estado actual no genera
soluciones ni eventos adicionales. Estados de mantenimiento, borrador y mensajes
no se alteran. Se retiraron referencias activas a En revisión en summary, analytics,
selector, filtros, métricas, gráficos y CSS; se preservaron migraciones e historial.

## Migraciones

- **0021_alter_incident_status**, existente y revisada: normaliza exclusivamente
  `under_review`→`in_progress` usando el alias de la migración, conserva los demás
  campos y el historial protegido. Mantiene los choices manuales y agrega un CHECK
  para los cinco valores en PostgreSQL. No crea actividad retrospectiva.
- **0022_notification_outbox**, nueva: tabla `notification_outbox` con referencia
  única al reporte, snapshot de contenido, destinatario, estado, contador y fechas
  de intento/envío, próximo intento y error sanitizado. No carga correos históricos.

0019/0020 y migraciones anteriores permanecen intactas. **No se aplicó ninguna de
estas migraciones a la principal.** La aplicación requiere ambas antes de utilizar
esta versión con la principal. El rollback de 0021 no puede distinguir qué registros
in_progress antes eran under_review: devuelve los choices antiguos sin deshacer la
normalización. No revertir 0022 con correos pendientes: eliminaría la tabla outbox.

## Un solo mecanismo durable

La creación normal de Incident se realiza en una transacción; su señal crea el
evento auditable y una única fila outbox, independientemente de prioridad o tipo.
Una cancelación de transacción elimina también su outbox. No se generan correos
por borradores, mensajes sin confirmar, cambios de estado, comentarios o asignación.
La confirmación y deduplicación del motor existente permanecen intactas. Las
inserciones bulk_create/SQL directo no son flujos soportados para crear reportes:
eluden señales, validaciones y auditoría y no deben usarse para la operación.

El callback on_commit únicamente acelera el envío; la fila durable ya existe y
puede recuperarse con el comando. Se retiró el callback independiente del procesador
conversacional: no hay dos mecanismos enviando el mismo correo. La función pública
anterior delega en esa misma fila, sin crear un evento nuevo ni reenviar enviados.

Antes de SMTP, el worker bloquea la fila y confirma su estado `sending`. Otro
worker no puede reclamarla. Estados: pending, sending, sent, failed. Se registran
intentos, fecha y solo clase de error, nunca el cuerpo de excepciones o credenciales.
Un fallo conocido deja failed y espera cinco minutos. Máximo predeterminado tres
intentos (configurable, acotado entre uno y diez). Una fila sent no vuelve a enviarse.
Sin URL, destinatario o credenciales SMTP válidas no se abre conexión de correo.

**SMTP no garantiza entrega exactamente una vez.** Si hay timeout/desconexión, o
el proceso cae después de que Gmail acepta pero antes de guardar sent, se conserva
sending: entrega incierta, sin reintento automático. Revisar en el proveedor antes
de decidir manualmente si se marca enviada o se habilita otro intento. El Message-ID
estable ayuda al diagnóstico, pero no obliga al proveedor a deduplicar. No se ofrece
un comando que restablezca ciegamente esas filas. Una falla SMTP no revierte el
reporte confirmado. Si el callback no llega a ejecutarse, el comando recupera pending.

El snapshot conserva el contenido de creación aunque el reporte cambie antes del
envío. El destinatario queda fijado al crear la fila; si estaba vacío, se completa
con el configurado al procesar. Cambiar la configuración afecta las creaciones
futuras y las filas sin destinatario, no reenvía mensajes a otro correo. Revisar las
filas antiguas explícitamente si se cambia el destinatario durante una puesta en marcha.

## Diseño y enlace

HTML de tablas, estilos inline, terracota oficial #ab4e3c, tarjeta clara y alternativa
de texto plano. Incluye icono PNG oficial embebido por CID y título Gym Planet;
se evita el AVIF del wordmark por su compatibilidad limitada en clientes de correo.
No carga imágenes externas, JavaScript, fuentes remotas ni datos secretos.
Incluye código, tipo, sucursal/área si existen, equipo o requerimiento según tipo,
descripción escapada, prioridad, estado y fecha. Omite campos vacíos e irrelevantes.

Botón visible **Ver reporte** y URL alternativa: `APP_BASE_URL` más
`reverse("report-detail", args=[id])`. Sin localhost fijo, tokens o accesos ocultos.
Se requiere HTTPS para hosts públicos; HTTP se admite solo en localhost/loopback.
El detalle mantiene autenticación operativa, next y permisos de Django; el correo
no concede autorización. Se conserva login del rol sin requerir Staff.

## Variables (sin secretos)

La plantilla `.env.example` usa consola y notificaciones desactivadas; este lote
no cambia `.env`. Los valores Gmail y destinatario fueron proporcionados por el usuario.

| Variable | Valor para activación manual |
| --- | --- |
| EMAIL_NOTIFICATIONS_ENABLED | True solo después de revisar todo |
| EMAIL_BACKEND | django.core.mail.backends.smtp.EmailBackend |
| EMAIL_HOST | smtp.gmail.com |
| EMAIL_PORT / EMAIL_USE_TLS / EMAIL_USE_SSL | 587 / True / False |
| EMAIL_HOST_USER | gymplanetreportes@gmail.com |
| EMAIL_HOST_PASSWORD | contraseña de aplicación ingresada privadamente |
| DEFAULT_FROM_EMAIL | Gym Planet Reportes <gymplanetreportes@gmail.com> |
| INCIDENT_NOTIFICATION_EMAIL | eduardopozo.g01@gmail.com (temporal) |
| APP_BASE_URL | http://127.0.0.1:8000 local; URL pública HTTPS real al desplegar |
| EMAIL_TIMEOUT | 20 segundos |
| NOTIFICATION_MAX_ATTEMPTS | 3 |

No se configura el correo de Bastián porque no fue proporcionado. No se asume que
la cuenta Gmail ya dispone de acceso SMTP; no se inspeccionan ni imprimen secretos.

## Activación manual, después de aprobación

1. Revisar diff y autorizar 0021/0022 en otra ejecución. Confirmar identidad de la
   principal y permisos antes de aplicarlas; no están aplicadas por este lote.
2. El titular de Gmail activa verificación en dos pasos y, si su cuenta lo permite,
   genera una contraseña de aplicación. Ingresarla en la configuración privada
   mediante editor local o gestor de secretos; nunca en chat, terminal o repositorio.
   Si la opción no está disponible, revisar política de la cuenta; no usar la
   contraseña normal ni reducir la seguridad. No se implementa OAuth en este lote.
3. Configurar las variables de la tabla y una URL base que pueda abrir el receptor.
   Localhost solo sirve en el mismo computador; para otro equipo se necesita la URL
   real del despliegue, no un dominio inventado. Mantener desactivado hasta verificar.
4. Validar primero con backend locmem mediante tests; consola permite inspección
   local pero imprime contenido operativo, por lo que no debe compartirse su salida.
   Un correo aceptado por consola/locmem se marca sent de desarrollo: no reenviarlo
   automáticamente al pasar a SMTP. Hacer el ensayo real con un reporte nuevo y
   autorización expresa, sin usar los tres reportes preservados ni backfill.
5. Activar EMAIL_NOTIFICATIONS_ENABLED=True y reiniciar el proceso. La creación
   confirmada intentará entregar después del commit. Revisar outbox en Admin técnico
   (solo lectura) y ejecutar primero la simulación del comando.

```powershell
uv run python manage.py process_report_notifications
# Envío explícito, solo después de configurar y autorizar SMTP:
uv run python manage.py process_report_notifications --apply --limit 20
# Incluye fallidas conocidas cuyo plazo de espera ya venció:
uv run python manage.py process_report_notifications --apply --retry-failed --limit 20
```

No se ejecutaron envíos reales ni se programó un servicio/scheduler. Para recuperar
pending tras interrupciones se debe ejecutar el comando de forma supervisada o
programarlo posteriormente con autorización. El comando simula por defecto; no
expone cuerpos ni destinatarios en su salida. Admin outbox es exclusivo de superusuario
sin permisos de agregar/editar/eliminar para el encargado general.

Fuentes de configuración: [SMTP de Google](https://support.google.com/a/answer/176600?hl=en)
y [contraseñas de aplicación](https://support.google.com/accounts/answer/185833),
consultadas el 18 de octubre de 2026. Google exige verificación en dos pasos para
contraseñas de aplicación; la disponibilidad depende de la cuenta/políticas.

## Verificaciones

Pruebas con locmem y proveedores simulados, sin SMTP, Telegram o Gemini reales.
Cubren prioridades/tipos, confirmación/deduplicación, rollback, callback posterior
al commit, error sanitizado, recuperación, límites, entrega incierta, remitente,
destinatario, HTML/texto, URL, autenticación/permisos y migración histórica aislada.
Se comparan huellas de contenido/esquema/restricciones/secuencias de la principal.
Los resultados finales y listado de archivos se completan al cerrar la validación.

## Archivos para revisión

- `docs/lot9-states-email.md`
- `incidents/management/commands/process_report_notifications.py`
- `incidents/migrations/0021_alter_incident_status.py`
- `incidents/migrations/0022_notification_outbox.py`
- `incidents/templates/incidents/email/report_created.html`
- `incidents/templates/incidents/email/report_created.txt`
- `incidents/test_notifications.py`
- `.env.example`
- `README.md`
- `config/settings.py`
- `incidents/admin.py`
- `incidents/dashboard_views.py`
- `incidents/models.py`
- `incidents/operational_signals.py`
- `incidents/report_filters.py`
- `incidents/services/incident_notification_service.py`
- `incidents/services/incident_processor.py`
- `incidents/services/operations.py`
- `incidents/static/incidents/dashboard.css`
- `incidents/templates/incidents/dashboard.html`
- `incidents/test_corporate_identity.py`
- `incidents/test_inventory.py`
- `incidents/test_operational_dashboard.py`
- `incidents/test_operations.py`
- `incidents/tests.py`
- `incidents/views.py`

Revisión visual del correo: HTML sintético renderizado en navegador en escritorio
y viewport de 390 px, sin desbordamiento horizontal. Las capturas ignoradas son
`env/lot9-email-desktop.png` y `env/lot9-email-mobile.png`. En la vista previa
se sustituye únicamente la referencia CID por datos locales del mismo icono;
el correo real lleva el recurso MIME embebido. No se ha verificado en una cuenta
Gmail real ni se garantiza idéntica representación en todos los clientes.


## Resultado final del lote 9

**398 pruebas aprobadas: 378 existentes adaptadas y 20 nuevas; 0 fallos y
0 errores; 55,774 segundos.** Correctos `check`, `makemigrations --check --dry-run`
y `git diff --check`. Se conserva también el caso histórico de inventario con
under_review antes de migrar: al llegar al esquema vigente se normaliza a in_progress.
No quedan cambios de modelos sin migración; 0021/0022 permanecen pendientes en la
principal por restricción expresa, mientras en pruebas están aplicadas.

Principal: 38 migraciones, 24 tablas, SC-000001, SC-000002 y GP-000003 intactos.
Se preservaron las cinco entradas de actividad y la asignación existentes. Su
huella completa no cambió. Pruebas: 40 migraciones, 25 tablas, sin reportes,
notificaciones, actividad, asignaciones ni cuentas operativas al terminar.

26 archivos para revisión, incluidos los cambios manuales conservados. No se
modificó .env ni se crearon usuarios. No se aplicaron migraciones a la principal,
no hubo SMTP real, llamadas a Gemini o mensajes Telegram reales, commit ni push.

Pendientes: autorización de 0021/0022 en la principal, configuración privada SMTP,
URL base accesible para el destinatario, activación explícita y ensayo real
autorizado. La prueba en Gmail/otros clientes sigue pendiente: la revisión visual
realizada fue del HTML sintético en navegador. Entregas inciertas requieren
conciliación manual; SMTP no proporciona garantía universal de exactamente una vez.
