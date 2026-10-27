# A container application: Noust builds this Dockerfile, keeps one image per release and runs it as a
# compose file it writes. The program listens on 8080 and prints one line.
FROM python:3.12-alpine
WORKDIR /srv
RUN printf 'Hello from Noust (container)\n' > index.html \
    && adduser -D -u 10001 app
# Nothing here needs root: the port is above 1024 and the files are only read.
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=4)"]
CMD ["python", "-m", "http.server", "8080", "--bind", "0.0.0.0"]
