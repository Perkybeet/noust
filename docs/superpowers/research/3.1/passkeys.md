# Passkeys (WebAuthn/FIDO2) como método de acceso a la consola

Investigación del ítem 48 del backlog, rama `dev/3.1`, 2026-09-29. Solo lectura sobre el
repositorio: no hay cambios de código. Las cifras de disponibilidad de paquetes se consultaron el
mismo día en packages.debian.org, packages.ubuntu.com, packages.fedoraproject.org, rpmfind y
pkgdex; lo que no se pudo confirmar está marcado como **no verificado** en la sección 7.

## 0. Resumen y recomendación

**Dependencia.** Escribir nosotros la capa de protocolo WebAuthn (CBOR mínimo, `authenticatorData`,
`clientDataJSON`, política) y delegar únicamente la verificación de la firma (ES256 y RS256) en
`python3-cryptography`, como dependencia **opcional de la capa consola** (la misma categoría que
`fastapi`: extra `web`, `Recommends` en Debian, `Suggests` en RPM), importada de forma perezosa.

- `py_webauthn` queda descartada: no está empaquetada en Debian, Ubuntu ni Fedora (solo en
  openSUSE Tumbleweed) y su metadato pide `cryptography>=44.0.2` y `pyOpenSSL>=25` (2.7.1) o
  `cryptography>=49` y `pyOpenSSL>=26.3` (3.0.1), muy por encima de Debian 12 (38.0.4) y Ubuntu
  24.04 (41.0.7).
- `python-fido2` queda descartada como librería de servidor: existe en todas partes, pero en tres
  generaciones de API incompatibles (0.9.1 en Debian 12 y Ubuntu 22.04, 1.1 a 1.2 en Ubuntu 24.04,
  Debian 13 y Fedora 42, 2.x en Fedora 43+), está en `universe` en Ubuntu y sigue necesitando
  `cryptography`.
- `python3-cryptography` existe en todos los destinos (mínimo 3.4.8 en Ubuntu 22.04) y en
  Debian/Ubuntu **ya se instala hoy** porque `noust` depende de `certbot`, y `python3-certbot`
  depende de `python3-cryptography`.
- La alternativa sin dependencia Python nueva (`openssl` por `CommandRunner`, como ya hacen el
  sellado y el JWT de la GitHub App) es viable pero peor aquí: ficheros temporales por
  verificación, hay que ampliar la clasificación de solo lectura del runner y las pruebas de
  criptografía real quedarían tras `allow_subprocess`. Es el plan B si el propietario prohíbe
  cualquier dependencia. Criptografía de curva elíptica escrita en Python puro: no recomendada.

**Política.** Una passkey es siempre descubrible y con verificación de usuario (UV). Sirve como
inicio de sesión completo (sin token ni código) y como confirmación de modo sudo. El token maestro
(más TOTP y códigos de respaldo) se conserva como vía de recuperación, y `noust passkey reset`
como palanca de root.

**Hallazgos que cambian el plan** (detalle en las secciones 1 y 2):

1. Chrome desde la 110 y Firefox desde la 140 **bloquean WebAuthn en páginas con errores de
   certificado, aunque el usuario haya aceptado la excepción**. El central por defecto (HTTPS
   autofirmado) no podrá usar passkeys, y además el certificado que mintea Noust no tiene SAN.
2. `localhost` sí sirve como RP ID (incluso en `http://`); `127.0.0.1` y cualquier IP no.
3. El `user.id` de WebAuthn debe ser **aleatorio por servidor**; si es constante, dos servidores
   alcanzados por `localhost` se sobrescriben la passkey en el autenticador.
4. `SecurityConfig.allowed_hosts` es configuración muerta (nadie la lee), así que el `Host` no está
   filtrado por el servidor.
5. Todo `/api/auth/*` ya está vedado a los tokens de flota: un central no puede enrolar passkeys
   en un nodo, sin escribir nada.

## 1. Cómo funciona hoy el acceso (verificado en el código)

| Pieza | Dónde | Hecho |
|---|---|---|
| Inicio de sesión | `src/noust/web/api/auth.py:303` (`login`) | Token maestro; si hay TOTP, además `totp_code` (o código de respaldo). Sin estado entre pasos: el cliente reenvía el token junto al código. Error `totp_required` (401) para pedir el segundo paso. |
| Segundo factor | `src/noust/web/auth.py:2137-2423` | Estado en el fichero JSON `web-totp` (`enabled`, `secret`, `backup_codes` con hash salado, `last_steps`). `verify_second_factor` consume el código de respaldo y recuerda el paso TOTP por propósito (`login`, `elevate`, `disable`). Devuelve `False` si `enabled` es falso. |
| Sesiones | `web/auth.py:1065` (`SessionStore`) | SQLite `web-sessions.db` en el directorio de estado (`/etc/noust`, `/data/config` en el contenedor) con tablas `sessions`, `ws_tickets`, `api_tokens`; migraciones a base de `ALTER TABLE` idempotentes. Es donde ya viven las credenciales. |
| Modo sudo | `api/auth.py:453` (`elevate`), `web/auth.py:2919`, `api/deps.py:630` (`ensure_elevated`) | 10 minutos (`ELEVATION_SECONDS = 600`). Acepta código TOTP/respaldo si hay 2FA, y el token maestro si no. Master y tokens de API están exentos. Un token de flota solo pasa si el central lo avala (`X-Noust-Elevated`). |
| Bloqueo por fuerza bruta | `web/server.py:338` (`AUTH_PATHS`) | `login`, `elevate` y `2fa/disable` se rechazan a una IP bloqueada; `record_auth_failure` es el único punto que cuenta. Cualquier ruta nueva que verifique una credencial debe entrar aquí. |
| Central | `src/noust/fleet/policy.py:36` | Un central se niega a añadir su primer servidor si `totp_enabled()` es falso. Hoy una passkey no lo satisfaría. |
| Flota | `web/auth.py:273` (`FLEET_REFUSED_PREFIXES`) | Incluye `/api/auth` entero: un token de flota nunca llega a endpoints de credenciales de un nodo. |
| Cabeceras | `web/server.py:146-160`, `1579-1586` | CSP estricta con `require-trusted-types-for 'script'`; `Permissions-Policy: geolocation=(), microphone=(), camera=()`; `COOP: same-origin`. |
| Dependencias de consola | `pyproject.toml` (extra `web`), `setup.py`, `obs/debian.control` (`Recommends`), `rpm/noust.spec` (`Suggests`, bloques Fedora y SUSE), y `cli/commands/web.py:546` (`WEB_DEPENDENCIES`, para `noust web install`) | Cuatro sitios declarados más un mapa de comprobación en tiempo de ejecución. `tests/test_architecture.py:600` (`OPTIONAL`) exime las importaciones condicionales. |
| Uso actual de criptografía | `core/totp.py` (solo `hmac`), `core/sealing.py` y `integrations/github/app.py` (`openssl` vía `CommandRunner`) | **No hay ningún `import cryptography` en el árbol.** El docstring de `totp.py` documenta la regla: una dependencia obliga a declararla en cuatro ficheros y a que exista en todas las distribuciones. |

Frontend (todo verificado leyendo el fuente):

- `panel/src/features/auth/LoginForm.tsx`: dos pasos (`token`, `code`); ya nombra la máquina
  (`session.hostname`) en `LoginPage.tsx`, que encaja con nombrar las passkeys por servidor.
- `ElevateDialog.tsx`: un solo campo; código TOTP si `session.totp_enabled`, token si no.
- `settings/TwoFactorSection.tsx` y `SecuritySettings.tsx`: donde vive el alta de TOTP y de códigos
  de respaldo; aquí iría `PasskeysSection`.
- `panel/openapi.json` → `schema.gen.ts`: los nuevos endpoints entran por `npm run gen:api`.
- E2E: solo Chromium (`Desktop Chrome`, temas claro y oscuro). El backend de E2E imprime
  `http://127.0.0.1:<puerto>` (`scripts/console_server.py:27`): **WebAuthn rechaza una IP**, así que
  los E2E de passkeys deben navegar a `http://localhost:<puerto>`.

## 2. Restricciones del navegador que condicionan el diseño

### 2.1 RP ID: dominio, nunca IP; el puerto no cuenta

El RP ID debe ser un dominio válido (WHATWG "valid domain"): las IP, en cualquier forma, se
rechazan con `SecurityError` (discusión en la lista del W3C:
https://lists.w3.org/Archives/Public/public-webauthn/2020Jan/0033.html; resumen práctico en
https://ory.sh/docs/troubleshooting/passkeys-webauthn-security-error). El RP ID no lleva puerto, y
las credenciales no se distinguen por puerto: una creada en `:3000` sirve en `:8000`.

`localhost` es un dominio válido y contexto seguro sin TLS en Chrome y Firefox
(https://chromium.googlesource.com/chromium/src/+/main/content/browser/webauth/origins.md;
https://www.corbado.com/blog/test-passkeys-localhost-ngrok). Varias fuentes de terceros dicen lo
contrario ("localhost no funciona"): es un error, la excepción existe precisamente para desarrollo.

### 2.2 Errores de certificado: bloqueo aunque se acepte la excepción

- Chrome 110 (2023-02): "Starting on M110, Chrome will stop allowing WebAuthn requests on websites
  with TLS certificate errors" (anuncio de Google en
  https://lists.w3.org/Archives/Public/public-webauthn/2022Nov/0135.html). Se puede saltar con
  `--disable-features=DisableWebAuthnWithBrokenCerts` o la política de empresa
  `AllowWebAuthnWithBrokenTlsCerts`; nunca es una opción que podamos pedir a un operador.
- Firefox 140 (CVE-2025-6433, https://security-tracker.debian.org/tracker/CVE-2025-6433): antes
  permitía WebAuthn tras aceptar una excepción; ahora falla con `SecurityError: The operation is
  insecure`. Chromium 140+ responde `NotAllowedError: WebAuthn is not supported on sites with TLS
  certificate errors` (relato con Proxmox en https://forum.proxmox.com/goto/post?id=781905).
- Solo funciona un certificado que el navegador **confía de verdad** (emitido por una CA de
  confianza, o instalado como ancla en el almacén del sistema y válido para el nombre).

Consecuencia directa para Noust: el central por defecto sirve HTTPS con un certificado autofirmado
(`CertManager.generate_self_signed`, `src/noust/managers/cert_manager.py:1121`), y
**ese certificado no tiene `subjectAltName`** (comprobado ejecutando el mismo `openssl req -x509
-subj /CN=nas` en esta máquina: solo `CN`, `CA:TRUE`, SKI y AKI). Chrome ignora el `CN` desde la
58, así que ni siquiera instalándolo como confiable servirían passkeys. Sin cambio, un central en
un NAS con el certificado de fábrica **no puede usar passkeys**, ni por IP ni por nombre.

### 2.3 Mapa de escenarios

| Cómo se llega a la consola | RP ID | Passkeys | Qué hacer |
|---|---|---|---|
| `http://localhost:<p>` (túnel SSH a una consola en loopback, HTTP) | `localhost` | Sí | El caso de un servidor normal. Sin certificado, sin errores. |
| `http://127.0.0.1:<p>` | ninguno | No | Es una IP. El banner ya dice "browse to http://localhost:<port>"; la primera línea `Server:` imprime la IP: imprimir `localhost` cuando el bind es loopback. |
| `https://localhost:<p>` a un central con certificado autofirmado (túnel) | `localhost` | No (error de certificado) | Certificado del operador o autofirmado con SAN y confiado. |
| `https://192.168.x.x:8443` | ninguno | No, sin arreglo posible | Un certificado público no se emite para una IP privada. Ver 2.4. |
| `https://nas.lan:8443`, autofirmado sin confiar | `nas.lan` | No | Igual que arriba. |
| `https://nas.lan:8443`, CA propia instalada en el dispositivo, cert con SAN | `nas.lan` | Sí | Posible pero pesado (hay que instalar la CA en cada dispositivo). |
| `https://noust.ejemplo.com` con certificado válido (Let's Encrypt, proxy, `NOUST_TLS_CERT`) | `noust.ejemplo.com` | Sí | El camino recomendado y el que menos sorprende. |
| Detrás de un proxy inverso (`--trusted-proxy`) | host público | Sí | El `origin` incluye el puerto externo. Ofrecer `web.passkeys.origins` como anulación. |

### 2.4 Qué ofrecer a un central alcanzado por IP

Ninguna passkey funciona por IP: la respuesta honesta es decirlo, con la causa, y ofrecer la
salida, sin ocultar la función. Las salidas, de mejor a peor:

1. **Darle un nombre y un certificado reales.** Un registro DNS (por ejemplo
   `noust.casa.ejemplo.com` → IP de la LAN; un registro público apuntando a una IP privada es
   válido) más un certificado real (Let's Encrypt por DNS-01 con el plugin de `certbot` del
   proveedor, o un proxy inverso que ya lo tenga) y montarlo con `NOUST_TLS_CERT`/`NOUST_TLS_KEY`.
   Esto coincide con el ítem 30 de ENS ("certificado del operador como valor documentado por
   defecto de un central").
2. **Nombre de una red privada con certificado válido** (por ejemplo `tailscale cert` para un
   nombre `*.ts.net`, que entrega un certificado Let's Encrypt real). Mencionarlo como ejemplo, no
   como recomendación de producto.
3. **CA local propia** (estilo mkcert) instalada en cada dispositivo del operador: funciona, pero es
   trabajo manual por dispositivo. Un `noust web cert --name nas.lan` que la genere e imprima cómo
   instalarla sería un añadido posterior, fuera de este ítem.
4. **No usar passkeys** en ese central y seguir con token más TOTP. Nunca son obligatorias.

Cambio mínimo, independiente de las passkeys y necesario para (1) y (3): añadir `-addext
subjectAltName=DNS:<nombre>[,IP:<ip>]` al certificado autofirmado.

La consola debe explicarlo **con la causa exacta**, no con un botón que desaparece: el servidor
responde una `availability` calculada de la petición (`ip_address`, `insecure_context`,
`library_missing`) y el cliente añade lo que solo él sabe (`isSecureContext`, ausencia de
`PublicKeyCredential`). Si el navegador rechaza por certificado, el `DOMException` se muestra
**literal** en el bloque de salida del sistema, con la sugerencia encima, como manda el CLAUDE.md
("un error del sistema nunca se parafrasea").

### 2.5 Túneles SSH: todos los servidores comparten `localhost`

Cada servidor solo verifica sus propias credenciales, pero el selector del navegador muestra las
passkeys de **todos** los servidores que se abren en `localhost`. Tres decisiones:

1. **Nombrar por servidor.** `user.name = "<usuario>@<hostname>"`, `user.displayName = "Noust en
   <hostname>"`. El hostname ya se muestra en el login.
2. **`user.id` aleatorio por servidor y por usuario** (32 bytes de `secrets.token_bytes`), nunca
   constante ni derivado del nombre. En WebAuthn, crear una credencial descubrible con el mismo
   `rpId` y el mismo `user.id` en el mismo autenticador **sustituye** la anterior. Con un valor
   constante, registrar una passkey en el servidor B borraría la del servidor A si ambos se abren en
   `localhost`. Es el fallo más fácil de cometer y el más caro de diagnosticar.
3. **`allowCredentials` en la elevación** (solo las passkeys de este servidor), vacío en el login
   (descubrible). Si el operador elige la de otro servidor, la respuesta es un error específico
   ("esta passkey no está registrada en este servidor; puede ser de otro servidor que comparte la
   dirección `localhost`"). La API de señales (`signalUnknownCredential`) **no** debe usarse cuando
   el RP ID es `localhost`: le diría al gestor que una credencial válida de otro servidor "no
   existe" y la borraría (https://developer.mozilla.org/en-US/docs/Web/API/PublicKeyCredential/signalUnknownCredential_static;
   disponibilidad limitada, sin estado Baseline).

### 2.6 Navegador bajo la CSP y Trusted Types

- **Sin librería.** `navigator.credentials.create/get` es nativo. Desde 2025 el navegador
  serializa solo: `PublicKeyCredential.parseCreationOptionsFromJSON()`,
  `parseRequestOptionsFromJSON()` y `credential.toJSON()` son "Baseline 2025" (Chrome 129, Edge 129,
  Firefox 119, Safari 18.4; https://developer.chrome.com/blog/passkeys-updates-chrome-129,
  https://developer.mozilla.org/en-US/docs/Web/API/PublicKeyCredential/toJSON), y convierten todos
  los `ArrayBuffer` a base64url. Recomendación: usarlas cuando existan y llevar un respaldo de unas
  25 líneas (base64url a mano) para navegadores anteriores a esas versiones (por ejemplo Firefox
  ESR 115, anterior a Firefox 119), cubierto por vitest en ambas ramas.
- **Trusted Types: sin implicaciones.** WebAuthn no toca ningún sumidero DOM: recibe un objeto y
  devuelve un objeto; `JSON.parse`/`toJSON` no son sumideros. No hay que crear ninguna política.
- **CSP:** `connect-src 'self'` basta (la ceremonia no es una petición de red; la petición al
  servidor es la de siempre).
- **Permissions-Policy:** las directivas `publickey-credentials-create` y `-get` tienen por
  defecto `self` (https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API). La
  cabecera actual (`geolocation=(), microphone=(), camera=()`) no las toca: no hay que cambiar
  nada. Nunca añadir un `publickey-credentials-*=()` por endurecimiento.
- **UI condicional (autofill):** disponible en Chrome, Edge, Firefox y Safari (MDN la marca como
  Baseline desde octubre de 2023;
  https://developer.mozilla.org/en-US/docs/Web/API/PublicKeyCredential/isConditionalMediationAvailable_static).
  `get({ mediation: "conditional", signal })` con un `AbortController`, atada a un campo con
  `autocomplete="current-password webauthn"`. Solo ofrece credenciales descubribles, que es lo que
  registramos. Es mejora progresiva y va en la fase 2 (el campo `username` actual es
  `hidden`, y un campo oculto no muestra el autocompletado).
- **Ceremonia con gesto de usuario:** la llamada a `credentials.get/create` debe salir de un `click`
  (Safari lo exige); nada de disparar al cargar, salvo la UI condicional.

## 3. Librerías: disponibilidad y dependencias

### 3.1 Paquetes por distribución

| Destino | `python3-cryptography` | `python3-fido2` | `py_webauthn` | `python3-cbor2` |
|---|---|---|---|---|
| Debian 12 bookworm | 38.0.4-3+deb12u1 | 0.9.1-1 | no existe | 5.4.6-1 |
| Debian 13 trixie (referencia) | 43.0.0-3+deb13u1 | 1.2.0-2 | no existe | 5.6.5-1 |
| Ubuntu 22.04 jammy | 3.4.8-1ubuntu2.4 | 0.9.1-1 (universe) | no existe | 5.4.2-1 |
| Ubuntu 24.04 noble | 41.0.7-4ubuntu0.4 | 1.1.2-2 (universe) | no existe | 5.6.2-1 |
| Ubuntu 26.04 (referencia) | 46.0.5 | 2.0.0-1 (universe) | no existe | 5.8.0-2 |
| Fedora 41 (fin de vida) | 43.0.0 | no verificado | no existe | no verificado |
| Fedora 42 (fin de vida) | 44.0.0-3 | 1.2.0-2 | no existe | no verificado |
| Fedora 43 / 44 | 46.0.7 / 50.0.0 | 2.0.0-3 / 2.0.0 a 2.2.1 (dos fuentes discrepan) | no existe | 5.6.5 |
| openSUSE Leap 15.6 | `python311-cryptography` 41.0.3-150600.21.6 | solo `python3-fido2` 0.9.3 (sabor python 3.6); `python311-fido2` no confirmado | no existe | no verificado |
| openSUSE Leap 16.0 (referencia) | no verificado | `python313-fido2` 1.1.3 | no verificado | no verificado |
| openSUSE Tumbleweed | `python313-cryptography` 49.0.0 (`python3-` por provides) | no verificado | `python313-webauthn` 2.7.1 | no verificado |

Fuentes: https://packages.debian.org/search?keywords=python3-cryptography&searchon=names&suite=all&section=all,
https://packages.debian.org/search?keywords=python3-fido2&searchon=names&suite=all&section=all,
https://packages.debian.org/search?keywords=python3-webauthn&searchon=names&suite=all&section=all,
https://packages.ubuntu.com/search?keywords=python3-cryptography&searchon=names&suite=all&section=all,
https://packages.ubuntu.com/search?keywords=python3-fido2&searchon=names&suite=all&section=all,
https://packages.fedoraproject.org/pkgs/python-cryptography/python3-cryptography/,
https://packages.fedoraproject.org/pkgs/python-fido2/python3-fido2/,
https://packages.fedoraproject.org/search?query=webauthn (solo `perl-Authen-WebAuthn`),
https://rpmfind.net/linux/rpm2html/search.php?query=python311-cryptography,
https://rpmfind.net/linux/rpm2html/search.php?query=python3-fido2,
https://rpmfind.net/linux/rpm2html/search.php?query=python3-webauthn,
https://pkgdex.org/download/python313-cryptography.
Fedora 41 y 42 ya no figuran en `packages.fedoraproject.org` (fin de vida); sus versiones salen de
búsquedas de paquetes RPM.

### 3.2 Dependencias declaradas por los proyectos (PyPI)

| Proyecto | Versión | `requires_dist` |
|---|---|---|
| `webauthn` (py_webauthn) 3.0.1 (2026-09-25) | último | `cryptography>=49.0.0`, `pyOpenSSL>=26.3.0`, `cbor2>=6.1.2`, `pyasn1>=0.6.2`, `pyasn1-modules>=0.4.2` |
| `webauthn` 2.7.1 (2026-02-11) | | `cryptography>=44.0.2`, `pyOpenSSL>=25.0.0`, `cbor2>=5.6.5`, `pyasn1>=0.6.2` |
| `fido2` 2.2.1 (2026-06-29) | último | `cryptography>=2.6,!=35,<52`; `pyscard` opcional |
| `fido2` 1.1.3 (2024-03-13) | | `cryptography>=2.6,!=35,<45` |
| `fido2` 0.9.1 (2021) | | `cryptography>=1.5`, `six` (según Debian) |

Ninguna versión de `py_webauthn` publicada en 2025 o 2026 es instalable con las bibliotecas de
Debian 12 ni de Ubuntu 22.04/24.04. Sus dependencias `pyOpenSSL` y `cbor2` solo servirían para
validar cadenas de certificados de atestación, que no vamos a usar (atestación `none`).

### 3.3 ¿Se usa ya `cryptography`?

No: `grep` sobre `src`, `tests`, `pyproject.toml`, `setup.py`, `obs` y `rpm` no encuentra ni
`cryptography`, ni `cbor`, ni `fido2`, ni `webauthn`. Pero en Debian y Ubuntu ya está instalado:
`noust` depende de `certbot` (`obs/debian.control`), `certbot` de `python3-certbot` y este de
`python3-cryptography (>= 2.5.0)` (https://packages.debian.org/bookworm/python3-certbot). En
Fedora y openSUSE `certbot` es solo `Suggests`, así que allí el paquete hay que declararlo.

## 4. La alternativa: verificación propia

### 4.1 Qué hay que escribir (atestación `none` únicamente)

Anunciando `pubKeyCredParams = [-7 (ES256), -257 (RS256)]`. RS256 es necesario: Windows Hello no
funciona sin él ("if you don't include alg -257, Windows Hello won't work", y Chrome usa
ES256+RS256 por defecto:
https://chromium.googlesource.com/chromium/src/+/cff8ad5/content/browser/webauth/pub_key_cred_params.md).
EdDSA (-8) **no se anuncia**: el orden expresa preferencia, ES256 va primero, ningún autenticador
elegiría EdDSA, y no implementarlo ahorra superficie.

| Pieza | Líneas de código (sin docstrings) | Nota |
|---|---|---|
| Decodificador CBOR (tipos 0 a 5 y booleanos/nulo, longitudes definidas, profundidad acotada, claves de mapa únicas) | ~35 | CTAP2 exige CBOR canónico: no hay longitudes indefinidas, etiquetas ni flotantes en un `attestationObject` real. |
| `authenticatorData` (rpIdHash, flags UP/UV/BE/BS/AT/ED, contador, credencial atestiguada, extensiones, bytes sobrantes) | ~25 | |
| Clave COSE a clave pública y verificación (cryptography) | ~35 | ES256: `from_encoded_point` + `verify(ECDSA(SHA256))`; RS256: `RSAPublicNumbers` + `PKCS1v15`. Rechazar RSA de menos de 2048 bits. |
| Verificación de registro | ~45 | |
| Verificación de aserción | ~45 | |
| Desafío firmado (HMAC), utilidades base64url | ~40 | |
| **Total de protocolo** | **~225** | Con docstrings de Google, tipos y comentarios del "por qué" que exige el proyecto: ~600 líneas. |

Medido con un prototipo en `/tmp` (no en el repositorio): decodificador CBOR y analizador de
`authenticatorData` en **59 líneas de código**; el resto es política, no parsing.

### 4.2 Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Parser CBOR: DoS con anidamiento o longitudes enormes; claves duplicadas; tipos no soportados | Profundidad máxima 8, entrada acotada a 64 KiB (el middleware ya limita cualquier cuerpo a 1 MiB), rechazar indefinidos/etiquetas/flotantes, `hypothesis` (ya es extra `dev`) para comprobar que el decodificador solo lanza `ValueError`. |
| Clave COSE inconsistente (`kty`/`alg`/`crv`), punto fuera de curva, RSA débil | Validar `kty`/`alg`/`crv` contra la lista anunciada; `from_encoded_point` valida el punto; RSA de al menos 2048 bits y `e` impar. |
| `clientDataJSON`: comparar sobre la serialización reenviada | Hash de los **bytes recibidos**, nunca de un JSON reserializado. `type`, `challenge` (comparación en tiempo constante), `origin` exacto, `crossOrigin` ausente o falso, sin `topOrigin`. |
| `rpIdHash`, UP y UV | Comparar con `sha256(rp_id)`; exigir UP y **UV en el servidor** (no basta con pedir `userVerification: "required"`, un autenticador puede ignorarlo). |
| Contador de firmas | Como py_webauthn: si el guardado o el nuevo es mayor que cero, el nuevo debe ser estrictamente mayor. Las passkeys sincronizadas envían siempre 0 (ambos cero es válido). Un retroceso: rechazar y auditar como posible clon. |
| Bandera BE/BS | BE no puede cambiar tras el registro; BS solo si BE. Guardar `backup_eligible` para etiquetar "sincronizada" o "atada al dispositivo". |
| Reutilización del desafío | Un solo uso, expira a los 5 minutos, se consume **también si falla la verificación**. |
| Unicidad de credencial | `UNIQUE(credential_id)`; `excludeCredentials` con las existentes. |
| Atestación | Se ignora `attStmt` (se comprueba solo que `fmt` es texto). La política permite aceptar credenciales sin verificar atestación cuando se pidió `none`. Coste: el AAGUID no prueba nada, solo etiqueta. |

Referencia de comprobaciones: las 8 de la aserción y las ~19 del registro de py_webauthn
(https://raw.githubusercontent.com/duo-labs/py_webauthn/master/webauthn/authentication/verify_authentication_response.py,
https://raw.githubusercontent.com/duo-labs/py_webauthn/master/webauthn/registration/verify_registration_response.py);
las nuestras son un subconjunto, sin las ramas de atestación de `tpm`, `packed`, `android-*` y `apple`.
La especificación es Recomendación W3C de nivel 3 desde 2026-08-25 (REC-webauthn-3-20260825).

### 4.3 Prototipo: tres formas de verificar la firma

Medido en esta máquina (Python 3.12.3, OpenSSL 3.0.13, `cryptography` 41.0.7), con pares de claves
recién generados y una alteración de un bit para comprobar el rechazo:

| Método | ES256 por verificación | RS256 | Notas |
|---|---|---|---|
| `cryptography` | 0,05 ms | trivial | Una llamada. Es lo que recomiendo. |
| `openssl dgst -verify` por `CommandRunner` | 2,6 ms (incluye lanzar el proceso y dos ficheros temporales) | verificado | Hay que construir a mano el SPKI DER (prefijo fijo de 26 bytes para P-256; ~15 líneas para RSA). Ed25519 requiere `pkeyutl -rawin` (OpenSSL 3). |
| Python puro (aritmética modular) | 9,6 ms | 0,11 ms | ~130 líneas extra para ES256+RS256; aceptable en latencia. |

### 4.4 Comparación y elección

| Opción | Dependencia nueva | Pruebas unitarias con cripto real | Riesgo principal |
|---|---|---|---|
| **C. Protocolo propio + `python3-cryptography`** | Una, opcional, ya instalada en Debian/Ubuntu | Sí, en proceso, con claves generadas en el test | Bajo: la parte delicada (curvas, RSA) es OpenSSL. |
| D. Protocolo propio + `openssl` por `CommandRunner` | Ninguna nueva (`openssl` sigue implícito) | Solo con `@pytest.mark.allow_subprocess` (existe y se usa en el sellado) y `openssl` en CI | Ampliar `READ_ONLY_SUBCOMMANDS["openssl"]` (`runner.py:1182`, hoy solo `enc`) para que un ensayo `--dry-run` no se salte la verificación; ficheros temporales; `openssl` no está declarado como dependencia en `debian.control` ni en el `spec` (solo en el `Dockerfile`). |
| E. Todo en Python puro | Ninguna | Sí (con vectores Wycheproof) | Aritmética de curvas escrita a mano en el camino de confianza de un acceso equivalente a root; solo procesa datos públicos, así que no hay canales laterales, pero un fallo lógico sería un salto de autenticación. Contradice la regla 3 (existe una implementación). |
| A. `py_webauthn` | Cuatro, inexistentes o demasiado nuevas | | Inviable (sección 3). |
| B. `python-fido2` | Una, en tres APIs | | Adaptador para 0.9, 1.x y 2.x; `universe` en Ubuntu. |

**Recomiendo C.** Encaja con la regla del proyecto (comprobar que existe en todos los destinos y
declarar en los sitios que toca), no añade nada a la instalación de Debian/Ubuntu, y deja la parte
que un error convertiría en vulnerabilidad en una librería auditada. Precedente en contra: el
proyecto ha evitado hasta hoy toda dependencia criptográfica (`totp.py`, `sealing.py`, JWT de la
GitHub App). La diferencia es que aquellos casos tenían una vía estándar sin dependencia (HMAC de la
biblioteca estándar, `openssl enc`); para ECDSA la vía sin dependencia es peor por lo dicho arriba.
Si el propietario lo prohíbe, la respuesta es D, no E.

El punto de verificación de firma queda en **una** función (`verify_signature(cose_key, mensaje,
firma) -> bool`); no hay dos backends (regla 3).

## 5. Diseño recomendado

### 5.1 Principios y política

1. **Una passkey es completa**: descubrible (`residentKey: "required"`) y con verificación de
   usuario (`userVerification: "required"`, comprobada en el servidor). Por eso vale como inicio de
   sesión por sí sola (posesión más biometría o PIN, resistente a phishing por vinculación de
   origen), como lo hacen GitHub y Dokploy. Coste: no admite llaves U2F sin PIN; para esas queda
   TOTP.
2. **Nunca debilita nada.** Añadir una passkey no abre ninguna puerta nueva; el token maestro con
   TOTP sigue funcionando exactamente como hoy.
3. **Segundo factor unificado** (recomendado, decisión del propietario, sección 8): el conjunto de
   segundos factores es {TOTP, código de respaldo, passkey}. Mientras alguno esté dado de alta, el
   inicio de sesión con token exige uno de ellos y el token solo ya no basta. El inicio con passkey
   es completo por sí mismo. Esto hace que la regla del central ("no añade su primer servidor sin
   2FA", `fleet/policy.py:36`) siga siendo verdad para quien solo usa passkeys. Sin esta política,
   el token solo seguiría abriendo la consola y una passkey no podría contar como 2FA.
4. **Recuperación en tres niveles**: otra passkey, códigos de respaldo (se generan al dar de alta el
   primer segundo factor, sea cual sea, y valen aunque TOTP esté apagado), y `noust passkey reset`
   como root. El token maestro se puede rotar con `noust web token --new` como siempre.
5. **Altas y bajas en modo sudo.** Registrar o borrar una passkey es una credencial permanente,
   igual que emitir un token de API. La elevación con passkey exige UV.
6. **Etiqueta honesta**: sincronizada (BE=1) o atada al dispositivo (BE=0). Opción de configuración
   `web.passkeys.allow_synced` (por defecto `true`) para exigir solo llaves atadas al dispositivo
   (perfil ENS/alto). La bandera BE no está atestiguada con atestación `none`: es una etiqueta y una
   política blanda, no una prueba.

### 5.2 Modelo de datos

En `web-sessions.db` (es donde ya viven `api_tokens` y `ws_tickets`; se crean con `CREATE TABLE IF
NOT EXISTS` dentro de `SessionStore._create_schema`, sin migración versionada nueva). Si las cuentas
por usuario aterrizan en `noust.db`, las passkeys se mudan con ellas en una sola migración: la
columna `user_id` es el punto de unión.

```sql
CREATE TABLE IF NOT EXISTS passkeys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_id   BLOB    NOT NULL UNIQUE,          -- <= 1023 bytes
    user_id         TEXT    NOT NULL DEFAULT 'operator',
    user_handle     BLOB    NOT NULL,                 -- 32 bytes aleatorios; el user.id de WebAuthn
    rp_id           TEXT    NOT NULL,                 -- la credencial solo sirve bajo este RP ID
    public_key      BLOB    NOT NULL,                 -- COSE_Key validada, tal como llegó
    alg             INTEGER NOT NULL,                 -- -7 o -257
    sign_count      INTEGER NOT NULL DEFAULT 0,
    backup_eligible INTEGER NOT NULL,                 -- BE: 1 = sincronizable
    backup_state    INTEGER NOT NULL,                 -- BS en el último uso
    transports      TEXT    NOT NULL DEFAULT '[]',    -- JSON, solo pistas
    aaguid          TEXT,                             -- etiqueta: la atestación es none
    name            TEXT    NOT NULL,                 -- <= 64, elegido por el operador
    created_at      REAL    NOT NULL,
    created_by      TEXT    NOT NULL,                 -- actor_label() del alta
    last_used_at    REAL,
    last_used_ip    TEXT
);

-- Solo para el modo "un solo uso": un desafío se anota al consumirlo, no al emitirlo.
CREATE TABLE IF NOT EXISTS webauthn_spent (
    nonce      BLOB PRIMARY KEY,
    expires_at REAL NOT NULL
);
```

**Desafío sin estado en la emisión** (recomendado): `challenge = nonce(16) || caduca(8) ||
HMAC-SHA256(clave de firma, "noust-webauthn-v1" || propósito || sid || rp_id || origin || nonce ||
caduca)`. Se verifica recalculando el HMAC con el contexto de la petición, se comprueba caducidad y
se inserta el `nonce` en `webauthn_spent` (una violación de clave primaria es una reutilización).
Así el endpoint anónimo de opciones **no escribe nada**, y no hay tabla que un anónimo pueda inflar.
Alternativa simple si se prefiere: una tabla `webauthn_challenges` con tope global y purga por
caducidad, como `ws_tickets`. La clave de firma es la que ya salan los códigos de respaldo y firma
los tokens de sesión (`_signing_key`); `--regenerate` invalida los desafíos pendientes, que solo
viven 5 minutos.

`user_handle` se genera al registrar la primera passkey de un usuario y se reutiliza en las
siguientes. Si se borra la última, la siguiente vuelve a generar otro: no hay que guardarlo aparte.

### 5.3 Configuración de RP ID y origen

- **Por defecto:** `rp_id` = host de la petición (sin puerto), `origin` = `esquema://host[:puerto]`
  tal como llegó (esquema por `is_secure_request`, que solo cree `X-Forwarded-Proto` de un proxy
  declarado). Como `allowed_hosts` está muerto, la credencial queda **atada a su `rp_id`** (columna
  `rp_id` y comparación del `rpIdHash` del autenticador con `sha256(rp_id)`); un `Host` falsificado
  solo puede pedir un RP ID bajo el que no hay ninguna credencial, y el navegador impide a una
  página ajena reclamar el de otra.
- **Nunca un dominio padre.** El RP ID es el host exacto: uno más estrecho es más seguro.
- **Anulaciones** (`web.passkeys.rp_id`, `web.passkeys.origins`) para proxies con un origen
  externo distinto. Son ajustes de seguridad de la consola: entran en `FLEET_PROTECTED_CONFIG_SECTIONS`
  (`web`), así que un central no las toca.
- Una IP como host se rechaza en el servidor (`availability = ip_address`) antes de emitir opciones.

### 5.4 Endpoints

Todos bajo `/api/auth/passkeys` (nuevo módulo `src/noust/web/api/passkeys.py`; `auth.py` ya tiene
1266 líneas), modelos por `web/pydantic_compat.py`. Como cuelgan de `/api/auth`, `FLEET_REFUSED_PREFIXES`
los veda a un token de flota sin más. Los dos que verifican una credencial anónima o de sesión entran
en `AUTH_PATHS` (bloqueo y `record_auth_failure`); el test que cruza `AUTH_PATHS` con rutas reales
lo exige.

| Método y ruta | Autorización | Función |
|---|---|---|
| `GET /api/auth/passkeys` | sesión | Lista (`id`, `name`, `created_at`, `last_used_at`, `synced`, `transports`, `rp_id`) y `availability {supported, rp_id, reason}`. |
| `POST /api/auth/passkeys/registration/options` | modo sudo | `PublicKeyCredentialCreationOptionsJSON`: `rp {id, name: "Noust"}`, `user {id: handle, name: "<usuario>@<hostname>", displayName: "Noust en <hostname>"}`, `pubKeyCredParams [-7, -257]`, `excludeCredentials` (las del usuario), `authenticatorSelection {residentKey: "required", userVerification: "required"}`, `attestation: "none"`, `timeout: 300000`. |
| `POST /api/auth/passkeys/registration` | modo sudo | Cuerpo `{credential: RegistrationResponseJSON, name}`. Verifica y guarda. |
| `PATCH /api/auth/passkeys/{id}` | sesión | Renombrar. |
| `DELETE /api/auth/passkeys/{id}` | modo sudo | Borrar. |
| `POST /api/auth/passkeys/login/options` | anónimo | `PublicKeyCredentialRequestOptionsJSON` descubrible (`allowCredentials` vacío, `userVerification: "required"`). Nada persistido. |
| `POST /api/auth/passkeys/login` | anónimo | Verifica la aserción y crea la sesión (mismas cookies y `LoginResponse` que `/login`). En `AUTH_PATHS`. |
| `POST /api/auth/passkeys/elevate/options` | sesión | Opciones con `allowCredentials` = las del usuario; el desafío va ligado al `sid`. |
| `POST /api/auth/passkeys/elevate` | sesión | Verifica y llama a `token_manager.elevate(sid)`. En `AUTH_PATHS`. |

Cambios en lo existente, mínimos:

- `LoginRequest` gana `passkey: AuthenticationResponseJSON | None` (segundo factor tras el token).
- El error del segundo paso pasa a `second_factor_required` con `methods: ["totp", "passkey"]`
  (se conserva `totp_required` cuando `methods == ["totp"]`, por los clientes que lo ramifican).
- `SessionInfo` gana `passkeys: {count, available}` (para el anónimo solo `available`, igual que
  hoy se filtra `totp_enabled`).
- `TokenManager.second_factor_enabled()` = TOTP activo o al menos una passkey; lo usan `login`,
  `elevate`, `regenerate_backup_codes` y `fleet/policy.py`. `verify_second_factor` acepta un código
  de respaldo aunque TOTP esté apagado si hay passkeys.
- `ElevateRequest.token` solo se acepta cuando no hay ningún segundo factor (como hoy con TOTP).
- Auditoría: `auth.passkey.register`, `auth.passkey.delete`, `auth.passkey.rename`;
  `auth.login` y `auth.elevate` con `detail = "passkey <nombre>"`; los fallos, por
  `record_auth_failure`.

CLI, con paridad con `noust 2fa` (`tests/test_cli_twofa.py` es el molde): `noust passkey list`,
`noust passkey remove ID`, `noust passkey reset` (borra todas; la palanca de recuperación de root).
No se puede **enrolar** por CLI: hace falta un navegador.

### 5.5 Flujos

**Alta.** Ajustes, Seguridad, Passkeys, "Añadir una passkey" → si la sesión no está elevada, el
cliente HTTP ya abre "Confirma que eres tú" (403 `elevation_required`) → nombre (sugerido a partir
del navegador) → `registration/options` → `parseCreationOptionsFromJSON` + `navigator.credentials
.create()` → `credential.toJSON()` → `registration`. El servidor: consume el desafío; comprueba
`type`, `origin`, `rpIdHash`, UP, UV, AT, `alg` permitido, longitud del ID, clave COSE válida, ID no
repetido; guarda; si es la primera, genera los códigos de respaldo y los muestra una vez (el
diálogo que ya existe). Si es la única, la interfaz recomienda añadir otra (como GitHub y Dokploy).

**Inicio con passkey.** Botón principal "Iniciar sesión con una passkey" → `login/options` →
`credentials.get()` → `login`. El servidor busca por `credential_id` (error específico si no la
conoce, ver 2.5), exige que `userHandle` coincida con el guardado, `rpId` del hash igual al de la
credencial, UP y UV, contador, firma sobre `authData || sha256(clientDataJSON)`, actualiza
contador/`last_used_*`, crea la sesión.

**Inicio con token.** Igual que hoy hasta el segundo paso. Si hay segundo factor: campo de código
más botón "Usar una passkey"; con la passkey el cliente reenvía `{token, passkey}`.

**Elevación.** Si la sesión tiene passkeys, "Confirma que eres tú" muestra primero el botón
"Confirmar con una passkey" (el gesto sale del clic) y debajo el campo del código actual.

**Central (flota).** El inicio de sesión con passkey ocurre **en el central**. El central sigue
avalando la elevación a los nodos con `X-Noust-Elevated`: una elevación con passkey en el central
funciona en los nodos sin tocarlos. Un nodo tiene sus propias passkeys (bajo su propio RP ID, por
ejemplo `localhost` en su túnel); el central no puede gestionarlas (`/api/auth` vedado) y la pestaña
de Passkeys es de las "solo del central" del ítem 37 (Seguridad, 2FA y sesiones del propio
central).

### 5.6 Consola (React)

- `panel/src/features/auth/passkeys.ts`: envoltorio de WebAuthn (detección, JSON nativo con
  respaldo, traducción de `DOMException` a un error mostrable **con su texto literal**).
- `LoginForm.tsx`: botón "Iniciar sesión con una passkey" encima del formulario del token, separado
  por "o"; en el segundo paso, botón "Usar una passkey" junto al campo de código. Fase 2: UI
  condicional con `autocomplete="current-password webauthn"` y `AbortController` que se cancela al
  enviar el token a mano.
- `ElevateDialog.tsx`: botón de passkey primero cuando `session.passkeys.count > 0`.
- `settings/PasskeysSection.tsx` (en `SecuritySettings`, antes de `TwoFactorSection`): tabla
  (nombre, insignia "Sincronizada" o "Atada al dispositivo", creada, último uso), alta, renombrar,
  borrar (modo sudo), aviso "añade una segunda" si solo hay una. Cuando no está disponible: la
  causa y la solución en una frase más la salida del sistema si la hay, sin ocultar la sección.
- Cadenas en los catálogos tipados EN y ES (`panel/src/i18n`), oraciones completas con marcadores.
- Color solo para estado (regla D8): la insignia lleva texto; sin iconos de color decorativos.
- `npm run gen:api`, `npm run build` y commit de `src/noust/web/static`.
- Opcional (API de señales, disponibilidad limitada): tras borrar una passkey,
  `signalAllAcceptedCredentials({rpId, userId, allAcceptedCredentialIds})` con el `user_handle`
  propio para que el gestor la oculte; va acotada por nuestro `userId`, así que no toca las
  passkeys de otro servidor en `localhost`. `signalUnknownCredential` no se usa nunca.

### 5.7 Cuentas por usuario y roles (mismo lanzamiento)

- `passkeys.user_id` ya es la unión: hoy `'operator'` (el único operador implícito); con cuentas, la
  clave de `users`.
- El `user_handle` es por usuario y aleatorio, **no** el nombre ni el correo (WebAuthn: opaco, máximo
  64 bytes, sin datos personales;
  https://www.w3.org/TR/webauthn-3/).
- El inicio con passkey es descubrible, así que **no hay que escribir un nombre de usuario**: el
  `userHandle` de la aserción resuelve la cuenta y no permite enumerar usuarios (un flujo
  "usuario primero" con `allowCredentials` sí lo permitiría). Por eso `residentKey: "required"`
  desde el principio: no hay que cambiar nada cuando lleguen las cuentas.
- La sesión creada lleva `user_id` y su rol; `actor_label` pasa a nombrar al usuario. La elevación
  usa `allowCredentials` restringido a las passkeys **de ese usuario**.
- Separación de funciones (ENS op.acc): un usuario no puede borrar ni enrolar passkeys de otro; solo
  un administrador (con modo sudo) puede `reset` las de otro usuario. Guardar `created_by`.

### 5.8 Empaquetado (cinco sitios, ninguno nuevo en el modelo)

1. `pyproject.toml`: `cryptography>=3.4` en los extras `web` y `all`.
2. `setup.py`: lo mismo.
3. `obs/debian.control`: `python3-cryptography` en `Recommends` (ya llega por `certbot`).
4. `rpm/noust.spec`: `Suggests: python3-cryptography` (bloque Fedora) y
   `python%{python3_pkgversion}-cryptography` (bloque SUSE; en Leap 15.6 es `python311-cryptography`).
5. `tests/test_architecture.py:600`: añadir `"cryptography"` a `OPTIONAL` (importación perezosa
   dentro de `try/except ImportError`, con un mensaje "instala `python3-cryptography`").
- **No** añadir `cryptography` a `WEB_DEPENDENCIES` (`cli/commands/web.py:546`): rompería el arranque
  de la consola entera por una función opcional. Sí a `packaging/obs/upgrade-test.sh:185`, y `uv.lock`.
- El contenedor lo trae por `noust[web]` (`packaging/container/Dockerfile`, `pip install ...[web]`).
- Mínimo `3.4`: Ubuntu 22.04 trae 3.4.8, y las llamadas usadas (`from_encoded_point`,
  `ECDSA(SHA256())`, `RSAPublicNumbers`, `PKCS1v15`) existen desde antes de 3.1.

### 5.9 Pruebas

- `tests/webauthn_authenticator.py`: autenticador de software (genera claves ES256/RS256 con
  `cryptography`, que basta como dependencia de desarrollo; escribe `attestationObject`, `authData` y
  `clientDataJSON`; firma). Con él se prueba lo bueno y **cada** fallo: desafío distinto, reutilizado
  y caducado, `origin` ajeno, `rpIdHash` de otro RP, sin UP, sin UV, contador que retrocede, firma
  alterada, `alg` no permitido, RSA débil, ID repetido, `userHandle` distinto, BE que cambia.
- `hypothesis` (ya en `dev`) sobre el decodificador CBOR: nunca otra excepción que `ValueError`.
- Vectores capturados de un autenticador real: se pueden fijar desde el E2E (virtual authenticator).
- Vitest con `navigator.credentials` simulado: rama JSON nativa y rama de respaldo, error con texto
  literal, cancelación (`NotAllowedError`).
- E2E de Playwright (solo Chromium, coherente con `playwright.config.ts`) con el autenticador virtual
  del protocolo DevTools (`WebAuthn.addVirtualAuthenticator` con `protocol: "ctap2"`, `transport:
  "internal"`, `hasResidentKey`, `hasUserVerification`, `isUserVerified`;
  https://qaskills.sh/blog/playwright-webauthn-virtual-authenticator-testing): alta, inicio, elevación,
  borrado, axe en ambos temas y sin violaciones de CSP. Navegar a `http://localhost:<puerto>`, no a
  `127.0.0.1`. El autenticador virtual solo existe en Chromium: Firefox y Safari quedan sin E2E.

### 5.10 Tamaño estimado

| Parte | Líneas (con docstrings y tipos) |
|---|---|
| `core/webauthn.py` (protocolo) | ~600 |
| Tablas y CRUD en `SessionStore`, `TokenManager` (desafíos, alta/baja, segundo factor unificado) | ~300 |
| `web/api/passkeys.py` | ~350 |
| CLI `noust passkey` | ~120 |
| Pruebas Python (autenticador de software, protocolo, API, CLI) | ~950 |
| Consola (envoltorio, login, elevación, sección de ajustes, consultas, i18n) | ~800 |
| Pruebas de consola (vitest, E2E) | ~450 |

Son estimaciones de orden de magnitud, no compromisos: en total, algo más de 3.500 líneas, del
orden de 1,5 veces el 2FA actual (`core/totp.py` más los endpoints y la sección de ajustes).

## 6. Cómo lo resuelve la competencia

| Producto | Passkeys | Cómo |
|---|---|---|
| **Dokploy** | Sí, como inicio de sesión | Ajustes, Perfil, Passkeys; en el login "usa la opción de passkey: sin contraseña ni código 2FA"; varias, recomienda registrar al menos dos o mantener contraseña más 2FA; "solo funciona en el dominio de tu panel" (https://docs.dokploy.com/docs/core/account-security). Es el competidor directo y ya lo tiene. |
| **Coolify** | No | Petición abierta desde abril de 2024 sin respuestas (https://github.com/coollabsio/coolify/discussions/1985). |
| **Portainer** | No | Sin MFA propio; delega en OAuth/LDAP. Peticiones abiertas de U2F/2FA (https://github.com/portainer/portainer/issues/1590, https://github.com/portainer/portainer/issues/4968). |
| **Proxmox VE** | WebAuthn como 2FA, no como sustituto de la contraseña | `webauthn: id=...,rp=...,origin=...` en `datacenter.cfg`; `id` debe ser un dominio, no una IP; para un clúster, el dominio padre; exige HTTPS con certificado válido; "Require TFA" en un realm deshabilita WebAuthn; errores difíciles de diagnosticar (https://forum.proxmox.com/threads/howto-webauthn-passkeys-across-cluster-or-on-single-node.165331/). Con certificado autofirmado dejó de funcionar en Firefox 140 y Chromium 140. |
| **Cockpit** | No | La petición está bloqueada por PAM (https://github.com/cockpit-project/cockpit/issues/20389). |
| **GitHub** | Sí: inicio completo y modo sudo | La passkey satisface contraseña y 2FA en un paso; acepta passkeys sincronizadas; las llaves de seguridad sin verificación de usuario solo valen como segundo factor; el modo sudo se confirma con passkey, llave, TOTP, GitHub Mobile o contraseña, dos horas (https://docs.github.com/en/authentication/authenticating-with-a-passkey/about-passkeys, https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/sudo-mode). |
| **Vercel** | Sí, como método de inicio adicional (2024-01-11) | Se añade en Ajustes, Autenticación; no sustituye a los demás (https://vercel.com/changelog/login-with-passkey-is-now-supported). |
| **Cloudflare (panel)** | Llaves de seguridad WebAuthn como 2FA solamente | La documentación las describe como segundo factor; recomienda varias y guardar códigos de respaldo (https://developers.cloudflare.com/fundamentals/user-profiles/2fa/). No aparece como inicio sin contraseña. |
| Vaultwarden (referencia self-hosted) | Sí | Exige un FQDN y TLS válido; una IP no sirve. Mismo problema que un central por IP. |

Lo que hace que Noust pueda quedar por encima: nombrar cada passkey por servidor por el problema de
`localhost` (nadie lo trata), etiquetar sincronizada o atada al dispositivo con política opcional,
explicar **la causa exacta** cuando no se puede (IP, certificado, contexto), tratar el error del
navegador como salida del sistema literal, y que la elevación con passkey funcione sin tocar los
nodos de una flota.

## 7. Lo que no está verificado o queda abierto

- **Safari**: no hay confirmación propia de su comportamiento con certificados autofirmados ni con
  `http://localhost`. La política de Chrome (M110) y Firefox (140) está documentada; la de Safari no
  la he podido comprobar. Probar antes de prometerlo en la documentación.
- **RP ID de una etiqueta** (`https://nas:8443`, `.local`): son dominios válidos, pero no lo he
  probado en navegador; comprobar con Chromium antes de recomendarlo.
- **Fedora 41 y 42** ya no figuran en `packages.fedoraproject.org`; sus versiones de
  `python3-cryptography` (43.0.0 y 44.0.0) salen de búsquedas de RPM, no de la página del paquete.
- **openSUSE Leap 15.6**: no encontré `python311-fido2` ni `python311-webauthn`; da igual para la
  elección (no dependemos de ellos), pero descarta las dos librerías allí.
- **Fedora 44**: dos fuentes discrepan (`python3-fido2` 2.0.0-5 frente a 2.2.1-1), sin efecto.
- **Tumbleweed** `python3-cryptography`: la versión (49.0.0) viene de un agregador, no de OBS.
- `SecurityConfig.allowed_hosts` (`web/auth.py:376`) parece configuración muerta: en `src` solo se
  asigna en `cli/commands/web.py:515` y solo la leen dos tests (`tests/test_cli_web.py:615,734`);
  ningún middleware la consulta. Confirmar antes de decidir si se implementa el filtrado del
  `Host` (que sería mejor que confiar en la credencial atada al `rp_id`) o se borra la opción.
- La bandera BE/BS no está atestiguada con atestación `none`: una passkey "atada al dispositivo"
  podría mentir. Sirve de etiqueta y de política blanda, no de prueba (AAL3 requeriría atestación).
- Mismo `openssl` implícito que usan el sellado y la GitHub App: si se elige D, declararlo por fin en
  `debian.control` y el `spec` (en Debian ya llega por `ca-certificates`, que depende de `openssl`,
  https://packages.debian.org/bookworm/ca-certificates; en Fedora y openSUSE no lo he comprobado).

## 8. Decisiones que necesito del propietario

1. **Dependencia**: ¿se acepta `python3-cryptography` como dependencia opcional de la consola
   (recomendado), o se prohíbe cualquier dependencia Python nueva y se va a `openssl` por
   `CommandRunner` (opción D)?
2. **Segundo factor unificado** (5.1, punto 3): ¿una vez dada de alta una passkey, el token solo deja
   de bastar para iniciar sesión en la consola? Es lo coherente y hace que las passkeys cuenten para
   la regla de 2FA del central; el coste es que perder todas las passkeys sin TOTP obliga a usar los
   códigos de respaldo o `noust passkey reset`.
3. **`residentKey: "required"`** para todas las passkeys (recomendado, prepara las cuentas y evita
   enumerar usuarios) o `"preferred"` para admitir llaves de seguridad sin ranuras residentes.
4. **Certificado del central**: añadir el SAN al certificado autofirmado (cambio pequeño), y
   promover el certificado del operador a documentación principal del central. ¿Con el ítem 30 de
   ENS, o antes?
5. **`web.passkeys.allow_synced`**: ¿se ofrece (por defecto sí) la opción de exigir llaves atadas al
   dispositivo?
6. **Fase 2**: UI condicional (autofill) y API de señales, ¿en 3.1 o después?

## 9. Fuentes

- Repositorio: `src/noust/web/api/auth.py`, `src/noust/web/auth.py`, `src/noust/web/server.py`,
  `src/noust/core/totp.py`, `src/noust/core/sealing.py`, `src/noust/integrations/github/app.py`,
  `src/noust/managers/cert_manager.py`, `src/noust/fleet/policy.py`, `src/noust/core/runner.py`,
  `src/noust/cli/commands/web.py`, `panel/src/features/auth/*`,
  `panel/src/features/settings/{TwoFactorSection,SecuritySettings}.tsx`, `pyproject.toml`,
  `setup.py`, `obs/debian.control`, `rpm/noust.spec`, `tests/test_architecture.py`,
  `packaging/container/Dockerfile`.
- WebAuthn: https://www.w3.org/TR/webauthn-3/ ; MDN
  https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API ; comprobaciones de la
  implementación de referencia en `duo-labs/py_webauthn` (URLs en 4.2).
- Certificados: https://lists.w3.org/Archives/Public/public-webauthn/2022Nov/0135.html ;
  https://security-tracker.debian.org/tracker/CVE-2025-6433 ;
  https://forum.proxmox.com/goto/post?id=781905.
- Paquetes: URLs en 3.1 y https://packages.debian.org/bookworm/python3-certbot ;
  PyPI https://pypi.org/pypi/webauthn/json, https://pypi.org/pypi/fido2/json.
- Competencia: URLs en la sección 6.
- Prototipo de medición y de CBOR: `/tmp/passkey_proto/` (fuera del repositorio, desechable).
