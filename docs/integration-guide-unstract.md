# Unstract OSS Extraction — Informe de Integración y Guía de Implementación

> POC (Proof of Concept) que demuestra a Unstract Open Source Edition
> extrayendo JSON estructurado de documentos argentinos (facturas, remitos,
> cartas de porte) vía OCR + LLM, servido como REST API. Este documento
> cubre (1) qué se construyó y cómo funciona, y (2) cómo integrarlo a un
> sistema propio (ej. Rentax).

---

## Parte 1 — Qué es el proyecto

### 1.1 Objetivo

Probar, con evidencia real (no solo documentación), que **Unstract OSS**
(self-hosted, sin depender del trial cloud) puede:

1. Recibir un documento (PDF, imagen escaneada) vía API REST.
2. Extraer sus campos usando OCR (LLMWhisperer) + un LLM (Gemini) según un
   prompt definido en lenguaje natural (no regex, no templates por
   proveedor).
3. Devolver JSON estructurado y tipado, listo para persistir en una base
   de datos.

Se probó contra 3 tipos de documento reales de uso argentino:

| Tipo | Formato de origen | Método de lectura |
|---|---|---|
| Factura (A/B/C/E/M) | Factura C: PDF con capa de texto real | Texto directo |
| Remito | Imagen escaneada | OCR (LLMWhisperer) |
| Carta de Porte Electrónica | Imagen escaneada | OCR (LLMWhisperer) |

### 1.2 Arquitectura de la plataforma

```
┌─────────────────────────────────────────────────────────────┐
│                    Unstract OSS (v0.186.1)                   │
├───────────┬───────────┬───────────┬────────────┬─────────────┤
│ Frontend  │ Backend   │ Workers   │ Platform   │ Reverse     │
│ (React)   │ (Django)  │ (Celery,  │ Service    │ Proxy       │
│           │           │  v2 split)│ (FastAPI)  │ (Traefik)   │
├───────────┴───────────┴───────────┴────────────┴─────────────┤
│  Postgres (app DB)  │  Qdrant (vector DB)  │  Redis + RabbitMQ │
├─────────────────────────────────────────────────────────────┤
│  Adapters: LLM (Gemini) · Embedding (Gemini) · X2Text (LLMWhisperer) │
└─────────────────────────────────────────────────────────────┘
```

Todo el tráfico (UI, API REST, websocket) entra por **Traefik en el puerto
80**, enrutado por el header `Host: frontend.unstract.localhost`. Es clave
entenderlo: pegarle directo a un contenedor (ej. el frontend en su puerto
propio) rompe el ruteo de `/api/v1`, `/deployment` y `/public` hacia el
backend (bug real encontrado y documentado, ver sección 1.4).

### 1.3 Cómo se configuró la extracción (sin código)

Todo el flujo de configuración es **manual, vía UI de Prompt Studio** — no
es scripteable por API en esta versión:

1. **Adapters** (Settings → Adapters): 4 conexiones, una vez por
   plataforma —
   - LLM: Google Gemini (`gemini-flash`)
   - Embedding: Google Gemini (mismo API key que el LLM, no requiere alta
     separada)
   - Vector DB: Qdrant (viene incluido en el stack, sin key externa)
   - Text Extractor (OCR): LLMWhisperer (100 páginas/día gratis)
2. **Proyecto de Prompt Studio** por tipo de documento (factura, remito,
   carta de porte): se sube un documento de muestra y se escribe **un
   único prompt combinado** que le pide al LLM el JSON completo con la
   estructura exacta esperada (no un prompt por campo — más simple y
   igual de efectivo).
3. **Deploy as API**: cada proyecto se publica como un endpoint REST con
   su propia API key.

### 1.4 Los 5 bugs reales encontrados (y por qué importan)

Ninguno de estos está documentado en el README oficial de Unstract — se
encontraron ejecutando la plataforma de verdad, no leyendo la doc:

| # | Bug | Causa | Impacto si no se arregla |
|---|---|---|---|
| 1 | Preflight de puertos bloqueaba reinicios | El chequeo de "puerto libre" no distinguía "puerto libre" de "puerto usado por mi propio stack ya corriendo" | `bootstrap.ps1` no podía ser idempotente |
| 2 | Healthcheck con falso positivo | Un worker (`worker-api-deployment-v2`) no expone puerto de salud HTTP en esta versión, pero el healthcheck asumía que sí | El script de salud podía reportar "OK" con un worker realmente caído |
| 3 | **URL del frontend rota** | El bypass de puerto directo al contenedor frontend salta Traefik → `/api/v1` y `/deployment` no enrutan → login y llamadas a la API fallan con 404, aunque la página cargue | Cualquiera que siga la URL "obvia" (`localhost:3100`) no puede loguearse ni usar la API |
| 4 | **Workers sin `ENCRYPTION_KEY`** | El script de bootstrap (igual que el `run-platform.sh` oficial) solo inyecta la key de encriptación en `backend` y `platform-service`, nunca en los workers Celery | Cualquier adapter (credencial de LLM/OCR) falla al usarse en tareas async, con el mensaje engañoso "Platform encryption key has changed" |
| 5 | **Drift entre schema y prompt** | El contrato de campos (`schemas/*.yaml`) y el prompt real cargado en la UI se escribieron por separado y no coincidían en los nombres de campo para Carta de Porte | La validación fallaba en un tipo de documento aunque la extracción funcionara bien |

**Por qué importa para una integración real**: si alguien reproduce este
setup siguiendo solo la documentación oficial de Unstract, va a pisar el
bug #3 y el #4 con certeza. Este POC ya los tiene resueltos en
`bootstrap.ps1`, `healthcheck.ps1` y `README.md`.

### 1.5 Resultado end-to-end verificado

Los 3 tipos de documento fueron probados contra archivos reales de
`Documentos/Demo OCR 1` y devuelven JSON válido:

- **Factura**: todos los campos correctos (fecha, punto de venta, emisor,
  receptor, detalle de ítems, CAE).
- **Remito**: campos clave presentes con ruido de OCR tolerable en texto
  libre (nombres con errores tipográficos menores) — comportamiento
  esperado y aceptado por diseño.
- **Carta de Porte**: extracción completa de las 5 secciones (A a G),
  incluyendo coordenadas geográficas en formato grados/minutos/segundos.

### 1.6 Estructura del repositorio

```
unstract-poc/
├── bootstrap.ps1                # Bring-up de la plataforma (idempotente, PowerShell puro)
├── healthcheck.ps1              # Probe de salud por servicio
├── docker-compose.override.yaml # Remapeo de puertos para este entorno
├── unstract/                    # Clone vendorizado (gitignored, pineado a v0.186.1)
├── schemas/                     # Contratos de campo por tipo de documento (YAML)
│   ├── factura.yaml
│   ├── remito.yaml
│   └── carta_de_porte.yaml
├── deployments/                 # Config de Prompt Studio exportada + .env.example
│   ├── deployment-config.md     # Cada paso manual de la UI documentado
│   ├── {factura,remito,carta-porte}-poc.json
│   └── .env.example
├── client/
│   ├── client.py                # Cliente CLI async (POST + poll + JSON)
│   ├── validate.py              # Validación tolerante a ruido de OCR
│   └── tests/                   # 31 tests (fixtures limpias + ruidosas)
├── docs/ports.md                # Mapa de puertos + guía de RAM real medida
└── Documentos/                  # Corpus de documentos de prueba
```

---

## Parte 2 — Guía de implementación: integrar esto a un sistema propio

Esta sección es agnóstica al stack (no se conoce el detalle técnico
interno de Rentax) — describe **puntos de integración, contratos y
decisiones de arquitectura** aplicables a cualquier backend que necesite
extraer datos estructurados de documentos.

### 2.1 Modelo de integración: API REST asíncrona

Unstract expone cada extracción como un endpoint REST con un flujo
**asíncrono de 2 pasos** (el sync está deprecado):

```
1. POST  /deployment/api/{org}/{api_name}/     (multipart, el PDF/imagen)
         Headers: Authorization: Bearer <api_key>
         → devuelve execution_id

2. GET   /deployment/api/{org}/{api_name}/?execution_id=<id>   (poll)
         → status: PENDING | EXECUTING | COMPLETED | ERROR | STOPPED
         → cuando COMPLETED: JSON extraído en el payload
```

El cliente de este POC (`client/client.py`) ya implementa este flujo
completo, con manejo de errores y timeouts — es el punto de partida más
directo para portar la lógica a otro lenguaje/stack si Rentax no es
Python.

### 2.2 Opciones de arquitectura para integrar en Rentax

| Opción | Cómo funciona | Cuándo conviene |
|---|---|---|
| **A. Llamada síncrona desde el flujo existente** | El servicio de Rentax que recibe el documento llama directo a Unstract (POST + poll bloqueante) y espera el JSON antes de continuar | Volumen bajo/medio, UX puede tolerar unos segundos de espera (la extracción real tarda ~5-30s según tipo de documento) |
| **B. Cola de trabajo (job async)** | Rentax encola el documento, un worker propio llama a Unstract y persiste el resultado cuando está listo, notifica por webhook/evento interno | Volumen alto, no se puede bloquear el request original, ya existe infraestructura de colas en Rentax |
| **C. Pipeline ETL** | Unstract mismo lee de una carpeta/bucket (S3, GCS, Azure Blob, SFTP) y escribe a una base destino (Postgres, Snowflake, BigQuery, etc.) sin pasar por la API REST | Ingesta batch de documentos que ya llegan a un storage compartido, sin necesidad de respuesta inmediata |

Para la mayoría de integraciones con un ERP/sistema de gestión como
Rentax, **la opción B (cola async)** es la más robusta: desacopla la
disponibilidad de Unstract del flujo crítico de Rentax, y permite
reintentos sin bloquear al usuario.

### 2.3 Contrato de datos (schemas)

Los 3 `schemas/*.yaml` de este repo son el contrato fuente de verdad de
qué campos devuelve cada tipo de documento, con sus tipos. Son el punto de
partida para:

1. Mapear el JSON de Unstract al modelo de datos interno de Rentax (ORM,
   tablas).
2. Escribir la capa de validación (portar `client/validate.py`, que ya
   separa "campo faltante/tipo incorrecto" — bloqueante — de "valor con
   ruido de OCR" — tolerable).

**Recomendación**: si Rentax maneja los mismos tipos de documento
(factura, remito, carta de porte) u otros similares, extender este
enfoque de "schema YAML + prompt combinado" es más rápido que reinventarlo
— nuevo tipo de documento = nuevo YAML + nuevo proyecto en Prompt Studio,
sin tocar código.

### 2.4 Autenticación y manejo de credenciales

- **API keys de Unstract** (una por deployment/tipo de documento): se
  generan en Prompt Studio → API Deployments → Manage Keys. Deben vivir
  en el sistema de secretos de Rentax (vault, variables de entorno del
  servicio, KMS) — nunca en código ni en el repositorio.
- **Credenciales de proveedores LLM/OCR** (Gemini, LLMWhisperer): se
  configuran una sola vez a nivel plataforma (adapters), no por
  integración — Rentax no necesita conocerlas, solo la API key de
  Unstract.
- **`ENCRYPTION_KEY`**: si Rentax despliega su propia instancia de
  Unstract (recomendado para producción, no reusar la de este POC), debe
  generarse una vez, respaldarse en un vault, e inyectarse en **backend,
  platform-service Y workers** (bug #4, sección 1.4) — perderla vuelve
  todos los adapters inaccesibles sin forma de recuperación.

### 2.5 Manejo de errores y reintentos

`client.py` ya define 3 códigos de salida claros, útiles como base para
diseñar la lógica de reintento en Rentax:

| Código | Causa | Estrategia de reintento sugerida |
|---|---|---|
| 1 | Archivo inválido/inexistente | No reintentar — error de datos de entrada, requiere corrección humana |
| 2 | Error de conexión/HTTP no-2xx | Reintentar con backoff — puede ser caída transitoria de Unstract |
| 3 | Extracción falló (status ERROR/STOPPED, o sin output) | Reintentar 1 vez; si vuelve a fallar, encolar para revisión manual (el documento puede ser realmente ilegible) |

### 2.6 Consideraciones de producción (más allá del POC)

Este POC está deliberadamente simplificado (auth default `unstract`/`unstract`,
sin HA, sin rate limiting propio). Antes de integrar contra un sistema real:

- **No usar las credenciales default** (`unstract`/`unstract`) — cambiarlas
  siguiendo la guía oficial de Unstract de cambio de credenciales.
- **Rate limits del proveedor OCR**: LLMWhisperer free tier son 100
  páginas/día — para volumen de producción se necesita el plan pago o un
  extractor alternativo (Unstructured.io y LlamaIndex Parse también están
  soportados como adapters).
- **Costos de LLM**: cada extracción consume tokens de Gemini. Si el
  volumen de documentos de Rentax es alto, vale la pena evaluar
  SinglePass/Summarized extraction (feature de Unstract Cloud/Enterprise)
  para reducir costo por documento.
- **Licencia AGPL-3.0**: Unstract OSS es AGPL. Si Rentax va a **modificar**
  el código de Unstract (no solo consumirlo vía API) y ofrecerlo como
  servicio a terceros, hay obligaciones de distribución de código fuente
  a revisar con el equipo legal antes de producción. Consumir Unstract
  solo vía su API REST (como hace este POC) es la integración de menor
  riesgo legal.
- **Recursos**: sizing real medido en este POC — ~24 contenedores, ~6.6GB
  RAM en reposo (ver `docs/ports.md`). Para producción, presupuestar
  infraestructura dedicada, no correrlo en la misma máquina que Rentax.
- **Alta disponibilidad**: este POC es un solo nodo Docker Compose. Para
  producción, Unstract soporta Kubernetes/Helm (fuera del alcance de este
  POC, ver documentación oficial).

### 2.7 Próximos pasos sugeridos para una integración real

1. Definir qué tipos de documento maneja Rentax hoy y si coinciden con
   los 3 de este POC o requieren nuevos schemas.
2. Decidir el modelo de integración (2.2: A, B o C) según el flujo actual
   de ingesta de documentos de Rentax.
3. Desplegar una instancia de Unstract dedicada (no reusar este POC),
   generando su propia `ENCRYPTION_KEY` y credenciales.
4. Portar `client.py`/`validate.py` al lenguaje del servicio de Rentax que
   vaya a consumir la API (la lógica es simple: HTTP POST + poll + parseo
   JSON, no hay SDK propietario que aprenda).
5. Definir la política de reintentos y de documentos que fallan
   extracción (cola de revisión manual, alertas, etc.).
6. Evaluar volumen esperado vs. límites del tier gratuito de LLMWhisperer
   y costo de tokens de Gemini para dimensionar el plan pago si aplica.
