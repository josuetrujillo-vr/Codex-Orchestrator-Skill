# Activación en español

La skill coordina trabajo multiagente desde Codex y puede delegar tareas
distintas en Codex CLI, OpenCode y Antigravity. Esta lista refleja el
`description` de `SKILL.md` y los casos canónicos de
[`trigger-tests.md`](trigger-tests.md).

## Debe activarse

- *"Desde Codex, divide esta migración en tareas y coordina workers de Codex, OpenCode y Antigravity; integra sus resultados."*
- *"Orquesta varios agentes para revisar módulos distintos, asigna scopes sin solapamiento y registra el resultado en un ledger."*
- *"Coordina desde Codex una revisión paralela con los runtimes que estén disponibles; si falta uno, continúa las tareas independientes."*
- *"Reparte este trabajo entre varios agentes, controla dependencias en un DAG y verifica cada resultado antes de integrarlo."*
- *"Continúa esta corrida OpenCode existente con su ledger YAML schema 3 y sus workers."* (ruta heredada)

## No debe activarse

- *"Explícame qué es un agente de IA y en qué se diferencia de un chatbot."* (explicación general)
- *"Corrige este bug de React."* (solicitud de agente único)
- *"Orquesta un pipeline de Airflow que carga CSV cada noche."* (orquestación genérica de datos)

Los ejemplos positivos requieren coordinación de tareas mediante varios
agentes o runtimes. Las tareas individuales, explicaciones generales y
pipelines de datos no entran en el alcance.
