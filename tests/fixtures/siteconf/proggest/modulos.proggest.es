# Subdominios de producto de Proggest: timpora (control horario), partes (partes
# de trabajo), gestordoc (gestor documental) y uaap. Todos van a la misma
# aplicación que proggest.es; el proxy de Next.js (apps/web-gateway/src/proxy.ts)
# enseña el login de cada producto según el host. Mismos bloques que
# proggest.es salvo /admin/queues, que solo se abre desde el dominio principal.
#
# En el servidor: /etc/nginx/sites-available/modulos.proggest.es. Un subdominio
# nuevo va en los dos server_name, en el certificado (--expand) y en CORS_ORIGIN
# de docker-compose.prod.yml. Ver «Subdominios de producto» en
# docs/operaciones/despliegue.md.

# HTTP server (redirect to HTTPS)
server {
    listen 80;
    listen [::]:80;
    server_name timpora.proggest.es partes.proggest.es partestrabajo.proggest.es gestordoc.proggest.es uaap.proggest.es;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl;
    http2 on;
    listen [::]:443 ssl;
    server_name timpora.proggest.es partes.proggest.es partestrabajo.proggest.es gestordoc.proggest.es uaap.proggest.es;

    ssl_certificate /etc/letsencrypt/live/proggest.es/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/proggest.es/privkey.pem;

    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # partestrabajo.proggest.es es otro nombre de partes.proggest.es: una sola
    # dirección (y una sola app instalable) por producto. 302 y no 301: el
    # navegador guarda un 301 para siempre, y ya costó un bucle con /uaap.
    if ($host = partestrabajo.proggest.es) {
        return 302 https://partes.proggest.es$request_uri;
    }

    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/rss+xml application/atom+xml image/svg+xml;

    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }

    # Acceso público de partes para los ayuntamientos.
    #
    # El límite va aquí y no solo en el @Throttle de Nest porque este bloque ve
    # la IP real del visitante: la petición llega directa del navegador. El
    # throttle de la aplicación es la segunda barrera.
    #
    # El PDF puede tardar si el parte lleva anexo fotográfico, de ahí el timeout
    # ampliado: los `location` no heredan los del bloque raíz.
    location /api/v1/work-reports/public/ {
        limit_req zone=auth_limit burst=10 nodelay;
        proxy_pass http://nestjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    location /api/v1/auth/login {
        limit_req zone=auth_limit burst=20 nodelay;
        proxy_pass http://nestjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/auth/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;

        # Aumentar buffers para NextAuth headers grandes
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;
    }

    # ---- Next.js API route handlers (proxy al backend via Next.js) ----
    # Estas rutas tienen route.ts en Next.js que hacen proxy autenticado al backend.
    # DEBEN ir ANTES del catch-all /api/ que va directo al backend NestJS.

    location /api/settings/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10G;
    }

    location /api/clocking/photo {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10G;
    }

    location /api/upload/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10G;
    }

    location /api/super-admin/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10G;
    }

    location /api/user/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy de imágenes de Next.js: lo usa `app-tint.ts` para teñir el icono de
    # una app al abrirla. Sin este bloque caía en el comodín /api/ (NestJS): 404.
    location /api/proxy/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/app-info {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/health {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/manifest {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy de ficheros del gestor documental (Next.js -> NestJS -> S3).
    # Debe ir ANTES del catch-all /api/, que apunta a NestJS: sin este bloque
    # el visor de documentos y las descargas responderian 404.
    # Sin buffering y con timeout largo porque un ZIP de una emision completa
    # se genera al vuelo y se streamea.
    location /api/files/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
        client_max_body_size 10G;
    }

    # Catch-all API -> NestJS backend
    location /api/ {
        limit_req zone=api_general burst=200 nodelay;
        proxy_pass http://nestjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 90s;
        proxy_connect_timeout 90s;
        client_max_body_size 10G;
    }

    # WebSocket para notificaciones en tiempo real (Socket.IO -> NestJS)
    location /socket.io {
        proxy_pass http://nestjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    # Servir archivos estáticos de assets
    location /assets/ {
        alias /var/www/proggest/apps/web-gateway/public/assets/;
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # Descarga del informe agregado de partes (proxy Next -> NestJS).
    # Un informe anual DETAILED encadena varias agregaciones y el render de
    # pdfmake: el cliente espera hasta 300s y el `location /` heredaria el
    # default de 60s de Nginx, devolviendo un 504 en vez del fichero.
    location /work-reports/reports/download {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
    }

    # Páginas del módulo UAAP (proxy Next -> Next).
    # El informe global va por Server Action, y una Server Action POSTea a la URL
    # de la PÁGINA que la invoca (/uaap/dashboard y /uaap/trabajadores), no a
    # /api/: hereda el `location /` y sus 60s por defecto de Nginx. Generar la
    # ficha de hasta 200 trabajadores encadena 200 render de pdfmake más el
    # merge, así que sin este bloque la descarga muere en 504 con la plantilla
    # entera seleccionada. Mismo motivo y mismos valores que
    # /work-reports/reports/download.
    # Cortacircuito del bucle de redirecciones cacheado: la primera version del
    # bloque /uaap/ emitio un 301 permanente (/uaap -> /uaap/) que los navegadores
    # guardan; con Next devolviendo 308 (/uaap/ -> /uaap) el bucle vivia en la
    # cache del cliente aunque el servidor ya estuviera corregido. Un 302 (no
    # cacheable) directo al dashboard rompe el ciclo sin pedirle nada al usuario.
    location = /uaap/ {
        return 302 /uaap/dashboard;
    }

    location /uaap {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
        # Los adjuntos de formaciones suben por Server Action a estas mismas
        # rutas: sin repetirlo aquí caerían al default de 1M de Nginx (los
        # bloques `location` NO heredan el 10G del `location /`).
        client_max_body_size 10G;
    }

    location / {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        client_max_body_size 10G;
    }

    # Con barra: sin ella casaba también con `/_next/staticX`, que Next pinta como
    # página. X-Real-IP siempre: donde Nginx no la fija, llega la del cliente
    location /_next/static/ {
        proxy_pass http://nextjs_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location ~ /\. {
        deny all;
    }

    location ~ ^/(\.env|\.git|docker-compose|Dockerfile) {
        deny all;
        return 404;
    }
}
