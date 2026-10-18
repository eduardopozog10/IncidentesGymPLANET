# Inventario, mantenimiento y solicitudes de Gym Planet

Esta etapa agrega modelos y administración. No carga categorías, sucursales,
equipos, proveedores ni usuarios reales. No cambia el dashboard, la taxonomía
`Incident.AssetCategory`, los estados de reporte ni el procesador conversacional.

## Catálogos y activos

`EquipmentCategory` y `RequestCategory` tienen código único, nombre, descripción
opcional, estado activo y fechas. Son catálogos independientes. Sus registros
referenciados se protegen contra eliminación; desactivarlos conserva el historial.
El estado activo es informativo en esta etapa: no desactiva equipos, impide
reportes ni modifica asignaciones existentes automáticamente.

`GymAsset` requiere sucursal, categoría, código y nombre. Área, marca, modelo,
serie y descripción son opcionales. El código es único dentro de cada sucursal;
puede repetirse en otra. No se exige una serie única porque no todos los activos
son máquinas con número de serie. Los códigos conservan su escritura original.

Estados operativos: `operational`, `restricted`, `out_of_service`, `maintenance`.
El estado activo es independiente del estado operativo. Ninguno se deriva de un
reporte o mantenimiento sin una operación explícita del usuario.

El área de un activo debe pertenecer a su sucursal. Esto se comprueba en modelos,
formularios y una clave foránea compuesta de PostgreSQL, incluso para SQL y
escrituras masivas que omiten `full_clean()`.

## Traslados e historial

Usar `asset.transfer_to(sucursal, branch_area=area_opcional, code=codigo_opcional)`
o editar explícitamente la ubicación en el Admin. Un traslado entre sucursales
debe indicar un área del destino o ninguna. Si el código ya existe allí, se
rechaza todo el traslado. `asset.deactivate()` conserva sus referencias.

PostgreSQL registra cada cambio de sucursal, área o código en `AssetMovement`,
incluidas las actualizaciones masivas. El evento conserva sucursales, áreas,
códigos anterior/nuevo y fecha. Los movimientos son de solo lectura y no pueden
editarse ni eliminarse. Los activos con historial o referencias se protegen contra
eliminación; se desactivan en su lugar.

Un traslado no reescribe la sucursal, área, texto ni equipo de reportes anteriores.
La coherencia del activo se valida al crear o cambiar su asociación en un reporte;
editar notas de un reporte histórico sigue siendo posible tras un traslado.
Al asociar un activo, la sucursal debe coincidir. Si ambos tienen un área
informada, también debe coincidir; un área desconocida no se infiere ni se rellena.
Los registros sin activo conservan todo el funcionamiento heredado.

## Mantenimiento

`MaintenanceRecord` requiere activo, tipo (`preventive`/`corrective`) y descripción.
El estado inicial es `scheduled`; también admite `in_progress`, `completed` y
`cancelled`. Programación, observaciones, responsable/proveedor en texto y reporte
relacionado son opcionales. No se inventan cuentas de usuario.

Un registro completado requiere fecha de realización no futura. Los demás estados
no admiten fecha de realización. La programación es opcional y puede quedar
después de la realización si el trabajo se adelantó. No se impone una relación
artificial entre fecha programada y fecha real.

La sucursal se captura al crear el mantenimiento y no cambia al trasladar el
activo. No puede reasignarse el equipo de un mantenimiento ya registrado. Un
reporte asociado debe pertenecer a esa sucursal histórica y, si identifica un
activo, debe identificar el mismo. Se puede vincular posteriormente un reporte
compatible sin reescribir la sucursal original. Para corregir un equipo mal
asignado, cancelar el registro y crear otro; no borrar el historial.

No se permiten borrados de mantenimientos desde el Admin ni mediante SQL/ORM.
El historial se consulta desde el activo y desde su administración propia.
Guardar un mantenimiento no modifica estados de equipos o incidencias, no envía
avisos y no programa trabajos externos.

## Solicitudes e impacto operacional

`Incident.request_category` es opcional y se admite solo si `report_type=request`.
Las solicitudes pueden carecer de activo, categoría o ubicación estructurada,
manteniendo la compatibilidad actual. No se implementan cantidades, compras,
costos ni aprobaciones hasta validar ese flujo.

Se conservan `equipment`, `area`, `location_context` y las prioridades
`low`, `medium`, `high`, `critical`. Su elección se valida en Django y PostgreSQL;
no se calcula ni cambia automáticamente.

El reportante puede describir en `operational_impact`:

- Seguridad de usuarios y posibilidad de lesiones.
- Máquinas disponibles y alternativas de uso.
- Cierre parcial de áreas.
- Interrupción del funcionamiento de la sede.
- Urgencia de revisión o mantenimiento.

Estos datos aportan contexto para una decisión humana. No existe un algoritmo
de seguridad ni una correspondencia automática entre riesgo, impacto y prioridad.
El personal responsable debe definir posteriormente su protocolo operacional.

## Integridad y validación

La migración `0013_inventory_maintenance_requests` crea el esquema. La
`0014_inventory_integrity` incorpora claves compuestas y triggers reversibles para
validar asociaciones, capturar movimientos y proteger el historial en PostgreSQL.
Las validaciones de sucursal bloquean filas relacionadas al asignarlas para evitar
que un traslado concurrente deje una asociación nueva incoherente.

Las migraciones 0001–0012 permanecen intactas. Las nuevas se validaron primero en
`test_incidentes_gymplanet_db` y se aplicaron posteriormente en la principal con
autorización explícita. La adaptación posterior del asistente se documenta en
[gymplanet-conversation.md](gymplanet-conversation.md); su migración 0015 ya está
aplicada con autorización. La principal conserva 33 migraciones y 22 tablas. Las
nuevas 0016/0017 de [cuentas corporativas](corporate-accounts-security.md) están
solo en pruebas y requieren revisión antes de aplicarse a la principal.

Validaciones ejecutadas desde el entorno exclusivo del proyecto:

```powershell
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test incidents --keepdb
git diff --check
```

Las pruebas incluyen compatibilidad de migraciones y reportes anteriores,
formularios Admin, transferencias, catálogos inactivos, solicitudes sin equipos,
fechas de mantenimiento e integridad ante escrituras masivas. Gemini permanece
en mock; Telegram sin token y correo de pruebas en memoria.
