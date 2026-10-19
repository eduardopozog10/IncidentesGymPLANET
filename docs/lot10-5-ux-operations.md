# Lote 10.5 — Pulido visual y gestión de pendientes

## Punto de partida y alcance

Repositorio independiente IncidentesGymPLANET, main, commit 6091608; Git limpio al
comenzar. Se conservaron las funcionalidades del Lote 10. La principal ya tenía
0023_inventory_operations aplicada por el usuario: 41 migraciones y 26 tablas.
No se modificaron modelos, migraciones, rutas, dependencias, .env ni credenciales.
No se accedió a otras bases o proyectos y no hubo Telegram, Gemini o SMTP reales.

## Mejoras realizadas

1. Navegación compartida de inventario: Inventario, Registrar activo, Categorías
   y Mantenimiento y recurrencia. Se reutiliza _inventory_nav en lista, categorías,
   registro/edición de activos y formularios de mantenimiento; aria-current marca
   la sección y se conservan los controles de acceso en backend.
2. Mantenimiento usa filtros con etiquetas, controles y acciones coherentes,
   tabla desplazable mediante teclado, fechas y distintivos, estado vacío fuera
   de la tabla y lista de registros recientes. Se conserva el criterio de dos
   incidencias del mismo activo en 90 días, así como los filtros existentes.
3. Centro de control con cuatro KPI: incidencias abiertas, solicitudes pendientes,
   incidencias altas/críticas abiertas y equipos fuera de servicio. La tercera
   métrica es un subconjunto de la primera, indicado expresamente. Inventario
   refleja el estado actual por sucursal, no el período histórico de los reportes.
   Se retiraron de la composición principal tarjetas históricas redundantes,
   distribuciones secundarias y actividad duplicada. El historial sigue accesible.
   La cola de revisión prioriza crítica, alta, media y baja, y después antigüedad.
   El resumen de pendientes separa sucursales por ID/código incluso con igual nombre.
   Las últimas incidencias/solicitudes son pendientes; la evolución histórica queda
   plegada con explicación de su alcance.
4. Controles compartidos con label en bloque, separación consistente, foco visible,
   estados deshabilitados y errores. Los formularios de inventario usan campos
   reutilizables y pasan a una columna en móvil. Las tarjetas de detalle no se
   estiran hasta la altura completa del formulario lateral.
5. Pie del sidebar agrupado con cierre de sesión por POST/CSRF. El menú puede
   desplazarse en pantallas de poca altura; se verificó a 1440 × 600.
6. Validación específica del estado y mensajes de longitud/obligatoriedad por campo.
7. Incidencias y Solicitudes son vistas de pendientes con contador del resultado
   filtrado y acceso al historial. La paginación cuenta el mismo queryset.
8. Mantenimiento opcional, sin fechas ficticias; ayuda explícita en el formulario.
9. Área/ubicación idénticas (ignorando mayúsculas y espacios repetidos) se muestran
   una vez, conservando sus valores en la base. La capitalización inicial es solo
   CSS en títulos de equipos/tipos de solicitud, sin reescribir nombres, acrónimos,
   códigos SC/GP ni datos. Las tarjetas de solicitudes muestran request_kind.
10. Revisión responsive y estados vacíos: los mensajes vacíos no quedan centrados
    fuera del viewport dentro de tablas anchas. Las tablas con datos conservan
    desplazamiento interno para no recortar contenido ni desbordar la página.

## Causa del problema al resolver

El servicio ya permitía transiciones libres: no había que recorrer estados
intermedios. text() combinaba dato obligatorio vacío y exceso de longitud en
«Completa el dato requerido respetando el largo máximo». En cambio a Resuelto,
se reprodujo que una solución vacía entraba en esa validación genérica. No se
dispone del contenido exacto del intento original para distinguirlo de un exceso
de longitud; ambas causas quedan ahora diferenciadas. StatusForm no comprobaba
esa obligación condicional y el servicio solo devolvía un error general.

Ahora el formulario señala la solución o motivo al lado del campo y conserva lo
introducido. El servicio mantiene la misma protección para otras entradas/API,
con etiquetas y límite exacto. No se eliminan requisitos esenciales:
- Resuelto requiere solución aplicada, máximo 4000 caracteres.
- Cancelado requiere motivo, máximo 4000 caracteres.
- Cerrado admite salto directo sin solución nueva y conserva la auditoría previa.
- Elegir de nuevo el mismo estado no genera eventos ni obliga a repetir solución.
- Se conservan permisos, CSRF, bloqueos transaccionales, autor y antes/después.
No se agregaron estados «Realizado» o «Completado» a los reportes: el estado de
finalización operativa existente es Resuelto. Completado pertenece a mantenimiento.

## Estados en las listas

| Pantalla | Estados mostrados |
| --- | --- |
| Incidencias | Solo incident: reported y in_progress |
| Solicitudes | Solo request: reported y in_progress |
| Todos los reportes | reported, in_progress, resolved, closed y cancelled |

Las vistas operativas excluyen resolved, closed y cancelled incluso si se intenta
forzar su selección por URL; un filtro de estado finalizado devuelve validación
sin resultados, sin exponer históricos en esas listas. El contador corresponde
al resultado de filtros actuales (sucursal, fecha, búsqueda, prioridad, área).
Los detalles históricos permanecen accesibles con los permisos existentes.
Se actualizó una expectativa previa que incluía una incidencia cerrada en la
vista operativa, para reflejar el requisito nuevo; no se eliminó ninguna prueba.

## Fechas opcionales

La nulabilidad ya funcionaba en 0023 y no cambió. Un activo se registra/edita sin
próximo mantenimiento y puede programarse después. Sin fecha efectiva muestra
«Sin mantenimiento programado» y no entra en vencidos. Una fecha pasada muestra
Vencido; dentro de 30 días, Próximo; después, Programado. Se conserva la fecha
más temprana entre la planificación explícita y mantenimientos pendientes.
Completar un mantenimiento sigue requiriendo su fecha de realización; esa regla
es distinta de tener una planificación preventiva y conserva integridad SQL.

## Revisión visual real

Django con StaticLiveServerTestCase y PostgreSQL test_incidentes_gymplanet_db.
Cuenta temporal con manage_operations, sin acceso técnico ni proveedores externos.
Se recorrieron 13 pantallas a 1440 × 1000, 820 × 1180 y 390 × 844:
Centro de control, Incidencias, Solicitudes, Todos los reportes, detalle,
Sucursales, Equipos, ficha de activo, registro, edición, Categorías, Mantenimiento
y registro de mantenimiento. Se guardaron capturas y medidas de layout en env/
(ignorado por Git): 39 combinaciones sin errores de servidor o desbordamiento
horizontal de la página. Las tablas con datos pueden desplazarse internamente.

Se comprobaron también solución vacía con error de campo, cambio real de una
solicitud sintética Reportado → Resuelto, historial y salida de pendientes, foco
en asignación, navegación Tab/Enter entre acciones, edición con fecha vacía,
filtro sin resultados y cierre de sesión seguro. Consola sin errores de JavaScript;
la respuesta HTTP 400 deliberada al omitir solución corresponde a validación.
Es revisión de viewports del navegador, no ensayos con dispositivos físicos.

Datos visuales: usuario test-visual105, sucursales VIS105-A/B, categoría
VIS105-CARDIO, tres activos VIS105-NONE/OVER/SOON, dos mantenimientos y diez
reportes sintéticos con estados distintos. Se verificó previamente que la base
de pruebas no contenía otros registros operativos. Tras cada recorrido el runner
limpió esa base, sin desactivar triggers; se retiraron sesión, usuario y datos
sintéticos, y se comprobó que la principal conservaba su huella íntegra.
La contraseña temporal aleatoria no se imprimió y su archivo privado fue retirado.

## Validación final

- `uv run python manage.py check`: sin incidencias.
- `uv run python manage.py makemigrations --check --dry-run`: sin cambios.
- `uv run python manage.py test incidents --keepdb`: **442 aprobadas**, cero fallos
  y cero errores, en **61,550 segundos** (63,518 incluyendo arranque del comando).
  Son 425 anteriores y 17 nuevas, sin eliminar métodos de prueba existentes.
- `git diff --check`: sin errores.
- Pruebas exclusivamente en test_incidentes_gymplanet_db, con Mock/proveedores
  simulados, correo en memoria y notificaciones externas desactivadas.
- Principal sin cambios: 41 migraciones, 26 tablas; misma huella de filas,
  estructura, restricciones y secuencias que antes del trabajo. Conserva
  SC-000001, SC-000002, GP-000003 y GP-000004, sus 13 actividades y tres asignaciones.
- Pruebas: 41 migraciones, 26 tablas, sin reportes, activos, usuarios, sucursales,
  sesiones ni otros datos operativos tras finalizar. Se conservan sus metadatos
  Django y esquema. No se aplicó ninguna migración en la principal.

Las nuevas pruebas cubren estados y auditoría, errores claros, idempotencia del
mismo estado, permisos/CSRF, pendientes por tipo/sucursal/filtros/paginación,
historial completo, métricas y prioridad, sucursales de igual nombre, presentación
sin alterar datos, navegación y mantenimiento sin fecha/vencido/próximo.

## Archivos para revisar

Modificados (16):

- incidents/dashboard_views.py
- incidents/forms.py
- incidents/inventory_views.py
- incidents/services/operations.py
- incidents/static/incidents/dashboard.css
- incidents/templates/incidents/_recent.html
- incidents/templates/incidents/_report_table.html
- incidents/templates/incidents/asset_detail.html
- incidents/templates/incidents/assets.html
- incidents/templates/incidents/dashboard.html
- incidents/templates/incidents/inventory_form.html
- incidents/templates/incidents/maintenance_dashboard.html
- incidents/templates/incidents/operational_base.html
- incidents/templates/incidents/report_detail.html
- incidents/templates/incidents/report_list.html
- incidents/test_operational_dashboard.py

Nuevos (4):

- incidents/templates/incidents/_inventory_nav.html
- incidents/templates/incidents/_review_report.html
- incidents/test_lot105.py
- docs/lot10-5-ux-operations.md

Logs, capturas y auxiliares quedan en env/, ignorado por Git. Para revisar
localmente: iniciar `uv run python manage.py runserver`, entrar por
`/operations/login/` con un usuario autorizado y recorrer `/dashboard/`,
`/dashboard/incidents/`, `/dashboard/requests/`, `/dashboard/reports/`,
`/dashboard/assets/` y `/dashboard/maintenance/`. Las acciones sobre la principal
son escrituras reales: revisar lectura/navegación sin usar reportes existentes
para ensayos. El servidor visual aislado ya fue detenido.

Mensaje de commit sugerido:
`Pule la interfaz operativa y mejora la gestión de pendientes y estados`

## Límites y revisión posterior

No hay nuevas migraciones que aplicar. Hace falta revisión humana del diff y
aceptación visual con el usuario; no se hizo commit ni push. No se incorporaron
planes obligatorios, notificaciones de mantenimiento ni nuevos proveedores.
La prueba de integración con servicios reales queda fuera de este lote.
