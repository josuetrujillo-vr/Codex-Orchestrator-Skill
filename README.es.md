# Orquestador multi-runtime para Codex

[![Licencia](https://img.shields.io/badge/licencia-MIT-blue.svg)](LICENSE)
[![Plataforma](https://img.shields.io/badge/host-Codex-6A5ACD.svg)](SKILL/SKILL.md)
[![English](https://img.shields.io/badge/read%20in-English-blue.svg)](README.md)

Coordina trabajo multiagente desde la conversación activa de Codex. Codex
divide la solicitud en tareas distintas de un DAG, las asigna a workers
configurados de Codex CLI, OpenCode o Antigravity, revisa los resultados e
integra el trabajo. Las tareas independientes pueden ejecutarse en paralelo.
Por defecto no se envía la misma tarea a todos los runtimes y las corridas
normales dirigidas por Codex no crean una raíz OpenCode `[00]`.

## Flujo

1. Descubre cada proveedor y sus capacidades disponibles.
2. Crea un ledger JSON schema 4 con criterios, dependencias, workspaces y
   scopes de escritura sin solapamiento.
3. Ejecuta tareas listas y continúa ramas independientes cuando un proveedor
   no está disponible.
4. Inspecciona cada resultado completado. Codex marca `verified` solo cuando
   se cumple el criterio de aceptación.
5. Integra los resultados y cierra el ledger registrando bloqueos y resultados
   inciertos con claridad.

## Inicio rápido

Ejecuta desde la raíz del repositorio. Primero descubre los proveedores:

```sh
sh SKILL/scripts/orchestrate.sh preflight-providers
```

Crea los ficheros de prompt y un ledger basado en
[la plantilla schema 4](SKILL/references/ledger-template.md), después:

```sh
sh SKILL/scripts/orchestrate.sh validate-ledger-v4 runs/example/ledger.json
sh SKILL/scripts/orchestrate.sh run-multiruntime --ledger runs/example/ledger.json
sh SKILL/scripts/orchestrate.sh status-multiruntime --ledger runs/example/ledger.json
# Después de revisar la salida contra el criterio:
sh SKILL/scripts/orchestrate.sh verify-task --ledger runs/example/ledger.json \
  --task-id research --observed "Revisé report.md contra los puntos de integración requeridos."
sh SKILL/scripts/orchestrate.sh validate-ledger-v4 --closing runs/example/ledger.json
```

El límite de concurrencia predeterminado es tres; `--parallel N` define un
límite positivo para esa corrida. El éxito del runtime queda como `completed`,
no como `verified`. Las dependencias se ejecutan cuando sus prerrequisitos ya
están verificados. La [guía de adaptadores](SKILL/references/provider-adapters.md)
explica capacidades, permisos, workspaces y reconciliación de timeouts.

Por defecto, los comandos de proveedores se ejecutan directamente. Para usar
terminales locales persistentes de HERDR Core, configura
`run.execution_backend` como `"herdr"` en el ledger. Necesitas `hc` instalado
localmente; `run.herdr.command` permite indicar otra ruta al ejecutable. El
ledger guarda el nombre del terminal en `transport_ref` y las sesiones del
proveedor en `runtime_ref`.

## Instalar como skill de Codex

Copia el directorio `SKILL/` al directorio de skills de Codex con el nombre
indicado en su frontmatter:

```sh
mkdir -p ~/.codex/skills/codex-multi-runtime-orchestrator
cp -R SKILL/. ~/.codex/skills/codex-multi-runtime-orchestrator/
```

El paquete se construye con `sh SKILL/scripts/package.sh`; la lista cerrada
genera `dist/codex-multi-runtime-orchestrator.zip`. Los comandos schema 4
requieren POSIX `sh` y Python 3.9+.
Windows Git Bash es un destino de CI y del paquete. Los proveedores instalados
deben ofrecer ejecutables nativos o puntos de entrada explícitos de runtime/CLI;
los shims `.cmd`, `.bat` y `.ps1` se reportan como no disponibles, mientras los
demás proveedores pueden continuar.

## Compatibilidad con schema 3

Se mantienen como ruta heredada los ledgers YAML schema 3 de OpenCode,
sesiones raíz `[00]`, reglas de workers/subagents y sus comandos y validadores
originales. No son requisitos para corridas Codex-led con schema 4. No pases un
ledger schema 3 a `validate-ledger-v4`.

## Estructura del proyecto

- `SKILL/SKILL.md` — flujo Codex-led y referencia de comandos.
- `SKILL/scripts/` — entrada de shell, runner schema 4, adaptadores de
  proveedores y utilidades heredadas de OpenCode schema 3.
- `SKILL/references/` — ledger, adaptadores y documentación heredada de
  OpenCode.
- `.github/workflows/ci.yml` — matriz CI para Linux, macOS y Windows Git Bash.

## Contribuir

Mantén sincronizada la lista cerrada de `SKILL/scripts/package.sh` con el
contenido de `SKILL/`. Conserva la matriz de CI/paquete para Linux, macOS y
Windows Git Bash. No incluyas artefactos de revisión de `.superpowers/sdd/` en el
paquete distribuible.

## Licencia

MIT; consulta [LICENSE](LICENSE).
