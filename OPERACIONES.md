# PhinodIA API — operación

Lo que hay que saber para mantener esto vivo. Escrito el 12-09-2026, después de
que la aplicación estuviera **semanas caída sin que nadie se enterara**.

---

## 1. Qué es y dónde vive

Generador de contenido de marketing con IA para tiendas colombianas: vídeos UGC
de producto, imágenes de producto y landings. Se paga por créditos.

| Pieza | Dónde |
|---|---|
| Aplicación | `https://app.phinodia.com` (y `n8n-phinodia-api.zb12wf.easypanel.host`) |
| Servidor | VPS Hostinger `31.97.147.178`, EasyPanel = Docker Swarm, servicio `n8n_phinodia-api` |
| Código | GitHub `Seryi358/phinodia-api`, rama `main` |
| Base de datos | Supabase `bxeiecdxryelwrtcwupe` (PostgREST, sin ORM) |
| Panel del servidor | `https://zb12wf.easypanel.host` — **el puerto 3000 está cerrado a internet a propósito** |
| Vigilancia | `https://vigilancia.zb12wf.easypanel.host` (Uptime Kuma) |

Proveedores: **KIE AI** (VEO 3.1 vídeo + GPT Image 2), **OpenAI** (guiones),
**Anthropic** (landings), **Wompi** (pagos, Colombia), **Gmail OAuth** (correo).

---

## 2. Requisitos no funcionales que se cumplen hoy

Medido el 12-09-2026, no estimado.

| Requisito | Estado |
|---|---|
| Disponibilidad vigilada | Centinela cada 5 min + resumen diario 8:00 (Colombia) |
| Latencia | p95 < 600 ms con 20 peticiones simultáneas; 0 errores 5xx |
| Autenticación | Sesión firmada HMAC-SHA256, cookie HttpOnly + Secure + SameSite=Lax |
| Copias de seguridad | Diarias a las 3:20, 30 días de retención, se releen para comprobarlas |
| Dependencias | `pip-audit` en CI: 0 vulnerabilidades conocidas |
| Accesibilidad | 8/8 páginas sin hallazgos a 390 px (etiquetas, alt, foco, sin desborde) |
| Correo | SPF ✅, DKIM ✅, DMARC presente (ver §7) |
| Certificados | Let's Encrypt vía Traefik; el centinela avisa 10 días antes |

---

## 3. Cómo se despliega

```bash
# En el Mac: cambiar, probar, empujar
pytest tests/ -q          # tienen que pasar TODAS
git push origin main

# En el VPS: desplegar
ssh phinodia-vps 'bash /root/desplegar-phinodia.sh'
```

`desplegar-phinodia.sh` trae `main`, sincroniza el directorio de EasyPanel,
construye la imagen, actualiza el servicio **y no da el despliegue por bueno
hasta que `/health` responde 200**.

**No uses el botón de EasyPanel.** Se cae a mitad de build (el contenedor del
panel se reinicia) y el servicio se queda con la imagen vieja sin decir nada.
Pasó el 12-09-2026.

---

## 4. Entornos

| Entorno | Qué es |
|---|---|
| Desarrollo | `python -m venv` + `pip install -r requirements-dev.txt` + `pytest`. Las variables las pone `tests/conftest.py` con valores de mentira: **no hace falta ninguna credencial real para desarrollar ni para la CI**. |
| Producción | El servicio del Swarm. Las variables viven en EasyPanel (`services.app.updateEnv`), no en el repo. |
| Staging | **No hay.** Montarlo exige un proyecto de Supabase aparte: compartir la base sería peor que no tenerlo. Mientras tanto, la red de seguridad son la suite, la CI y que el despliegue verifica `/health`. |

Los secretos **nunca** en el repo. `.env` está en `.gitignore` y el Dockerfile
no lo copia: en producción las inyecta el Swarm.

---

## 5. Autenticación (leer antes de tocar nada)

Hasta el 12-09-2026 **la app no tenía sesiones**: la identidad era el correo que
venía en el cuerpo de la petición. Sabiendo el correo de un cliente se le podían
gastar los créditos, listar y descargar sus generaciones y ver su saldo.

Hoy: inicio de sesión sin contraseña (código de 6 dígitos + enlace), cookie
firmada, sin estado en base de datos (los 4 workers validan lo mismo).

**Regla que no se negocia:** un endpoint que gasta créditos o devuelve datos de
un cliente lleva `Depends(exigir_sesion)` y usa ESE correo. El del cuerpo o la
query se ignora. La CI tiene un guardián que falla si alguien lo quita de
alguno de los 9 endpoints protegidos.

---

## 6. Cuando algo se rompe

**Llega un correo `[PhinodIA] ALGO SE ROMPIO`:**

```bash
ssh phinodia-vps 'python3 /root/centinela.py --probar'   # qué falla exactamente
ssh phinodia-vps 'docker service ls'                     # réplicas
ssh phinodia-vps 'docker service logs n8n_phinodia-api --tail 50'
```

| Síntoma | Causa que ya ha pasado | Arreglo |
|---|---|---|
| Servicios en 0/1, `exit 137` | Sin memoria, o disco al 98% | `docker builder prune -af`; el cron semanal ya lo hace |
| Todos los dominios dan `000` | Se reinició el demonio de Docker: el Swarm dejó de publicar 80/443 | `docker service update --force traefik` |
| Un servicio no resuelve a su base | El reinicio de Docker rompió el DNS overlay | `docker service update --force <servicio de la BASE>` |
| Un servicio da 502 | Swarm intentó bajar la imagen del registro | `docker service update --force --no-resolve-image <svc>` |
| El despliegue "no entra" | EasyPanel se cayó a mitad de build | `bash /root/desplegar-phinodia.sh` |

**Nunca `systemctl restart docker` en este servidor.** `docker service ls` puede
decir `1/1` con todo inalcanzable desde fuera.

---

## 7. Pendiente (necesita acceso que no tengo)

1. **Índice único en la base.** La idempotencia de pagos está resuelta por el id
   derivado de la clave de negocio (ver `app/database.py::id_idempotente`), pero
   lo correcto es además:
   ```sql
   create unique index concurrently if not exists
     ux_transactions_wompi_tx on transactions (wompi_transaction_id);
   ```
   Requiere un token de acceso de Supabase (Account → Access Tokens).

2. **DMARC en `p=none`.** Hoy solo observa. Con SPF y DKIM ya correctos, el
   siguiente paso es `p=quarantine; pct=10` durante dos semanas mirando los
   informes de `dmarc@phinodia.com`, y después `p=reject`.

3. **Cuentas de prueba en producción.** Quedan 28 (`@example.com`, `@test.com`,
   `*pentest*`) con 486 créditos fantasma que inflan cualquier métrica. Listado:
   `ssh phinodia-vps 'set -a; . /root/.phinodia-api.env; set +a; python3 /root/listar_prueba.py'`
   Borrado (revisa la lista antes): `python3 /root/limpiar_prueba.py`
   Nunca toca a quien pagó ni a quien tiene generaciones.

---

## 8. Lo que corre solo en el servidor

| Cuándo | Qué | Registro |
|---|---|---|
| cada 5 min | `centinela.py` — URLs, réplicas, disco, memoria, certificados, y que los endpoints protegidos sigan devolviendo 401 | `/var/log/centinela.log` |
| cada 10 min | `cerrar-panel.sh` — vuelve a cerrar el puerto 3000 (Docker reescribe sus reglas) | — |
| 3:20 diario | `respaldo-phinodia.py` — copia de las 9 tablas a `/opt/backups/phinodia-api/`, 30 días | `/var/log/respaldo-phinodia.log` |
| domingos 4:17 | limpieza del caché de Docker | `/var/log/centinela.log` |
